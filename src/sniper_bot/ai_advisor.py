"""AI Strategy Advisor — uses OpenAI API to analyze trading data and auto-tune parameters."""
from __future__ import annotations

import json
from datetime import datetime, timezone
from typing import Any

from sqlalchemy import desc, select

from sniper_bot.config import TUNABLE_PARAM_BOUNDS, AppConfig
from sniper_bot.storage import (
    AITuneLog,
    CycleLog,
    Database,
    EquitySnapshot,
    Position,
    PreTuneSnapshot,
    RiskEvent,
    get_latest_pre_tune_snapshot,
    record_ai_tune_log,
    record_pre_tune_snapshot,
)

def compute_health_score(data: dict[str, Any]) -> dict[str, Any]:
    """Compute a composite strategy health score from trading data.

    Components:
    - profit_factor: gross_wins / gross_losses (>1 is profitable)
    - risk_adjusted: win_rate * avg_win / max(avg_loss, 0.01) (Sharpe-like)
    - capture_efficiency: avg ratio of exit gain to peak gain
    - trade_frequency: penalize too few trades (bot should be active)
    - drawdown_penalty: reduce score for high drawdowns

    Returns a dict with the composite score (0-100) and components.
    """
    trade_hist = data.get("trade_history", {})
    equity_trend = data.get("equity_trend", {})

    wins = trade_hist.get("wins", 0)
    losses = trade_hist.get("losses", 0)
    total = trade_hist.get("total_trades", 0)
    avg_win = abs(trade_hist.get("avg_win", 0))
    avg_loss = abs(trade_hist.get("avg_loss", 0.01)) or 0.01
    win_rate = trade_hist.get("win_rate", 0)
    capture = trade_hist.get("avg_capture_ratio")
    max_dd = equity_trend.get("max_drawdown_pct", 0)

    # Profit factor (capped at 5 for scoring)
    gross_wins = avg_win * wins
    gross_losses = avg_loss * losses if losses > 0 else 0.01
    profit_factor = min(5.0, gross_wins / gross_losses) if gross_losses > 0 else 0.0
    pf_score = min(25, profit_factor * 5)  # 0-25 points

    # Risk-adjusted return
    risk_adj = win_rate * avg_win / avg_loss if avg_loss > 0 else 0
    ra_score = min(25, risk_adj * 12.5)  # 0-25 points

    # Capture efficiency (how much of peak gain is kept)
    cap_score = min(20, (capture or 0.5) * 20)  # 0-20 points

    # Trade frequency (at least 1 trade per 200 cycles is healthy)
    cycles = data.get("cycle_summary", {}).get("total_cycles", 1) or 1
    trades_per_100_cycles = (total / cycles) * 100
    freq_score = min(15, trades_per_100_cycles * 5)  # 0-15 points

    # Drawdown penalty
    dd_penalty = min(15, max_dd * 100)  # lose up to 15 points

    composite = max(0, round(pf_score + ra_score + cap_score + freq_score - dd_penalty, 1))

    return {
        "composite": composite,
        "profit_factor": round(profit_factor, 2),
        "risk_adjusted": round(risk_adj, 2),
        "capture_efficiency": round(capture or 0, 4),
        "trade_frequency_per_100_cycles": round(trades_per_100_cycles, 2),
        "max_drawdown_pct": round(max_dd, 4),
        "components": {
            "profit_factor_pts": round(pf_score, 1),
            "risk_adjusted_pts": round(ra_score, 1),
            "capture_pts": round(cap_score, 1),
            "frequency_pts": round(freq_score, 1),
            "drawdown_penalty": round(dd_penalty, 1),
        },
    }


SYSTEM_PROMPT = """\
You are an expert quantitative trading strategist analyzing a crypto momentum bot.

The bot scans all Bybit USDT spot pairs every 30 seconds for volume spikes and price momentum, \
then enters positions with trailing stops.

Your task: analyze the provided data and suggest specific parameter adjustments.

You MUST respond with a JSON object in this exact format:
{
  "assessment": "Brief summary of performance and market conditions",
  "proposed_changes": {
    "strategy.min_entry_score": {"current": 0.4, "proposed": 0.35, "reason": "..."},
    "position.trailing_stop_pct": {"current": 0.15, "proposed": 0.12, "reason": "..."}
  },
  "confidence": "low|medium|high",
  "warnings": ["any risks or concerns"]
}

Rules:
- Only include parameters you actually want to change. Use dotted notation (section.param_name).
- Be conservative — small incremental adjustments preferred over large swings.
- Strategy weights (volume_weight, momentum_weight, relative_strength_weight) must sum to 1.0.
- Reference specific data patterns to justify each change.
- If the bot has insufficient data, say so and propose fewer changes.\
"""


# ---------------------------------------------------------------------------
# Data gathering (shared by CLI analyze + auto-tune)
# ---------------------------------------------------------------------------

def _gather_data(db: Database, mode: str, cycles: int = 500) -> dict[str, Any]:
    """Gather trading data for AI analysis."""
    data: dict[str, Any] = {}

    with db.session() as session:
        cycle_rows = list(session.scalars(
            select(CycleLog)
            .where(CycleLog.mode == mode)
            .order_by(desc(CycleLog.recorded_at))
            .limit(cycles)
        ).all())

        if cycle_rows:
            scores = [r.top_score for r in cycle_rows if r.top_score]
            actions: dict[str, int] = {}
            for r in cycle_rows:
                actions[r.entry_action] = actions.get(r.entry_action, 0) + 1

            btc_prices = [r.btc_price for r in cycle_rows if r.btc_price]
            breadths = [r.market_breadth_pct for r in cycle_rows if r.market_breadth_pct is not None]

            data["cycle_summary"] = {
                "total_cycles": len(cycle_rows),
                "time_range": f"{cycle_rows[-1].recorded_at} to {cycle_rows[0].recorded_at}",
                "score_stats": {
                    "max": round(max(scores), 4) if scores else 0,
                    "min": round(min(scores), 4) if scores else 0,
                    "avg": round(sum(scores) / len(scores), 4) if scores else 0,
                    "p90": round(sorted(scores)[int(len(scores) * 0.9)], 4) if scores else 0,
                    "p75": round(sorted(scores)[int(len(scores) * 0.75)], 4) if scores else 0,
                    "above_0.6": sum(1 for s in scores if s >= 0.6),
                    "above_0.4": sum(1 for s in scores if s >= 0.4),
                    "above_0.3": sum(1 for s in scores if s >= 0.3),
                    "above_0.2": sum(1 for s in scores if s >= 0.2),
                },
                "entry_actions": actions,
                "btc_price_range": {
                    "min": round(min(btc_prices), 2) if btc_prices else None,
                    "max": round(max(btc_prices), 2) if btc_prices else None,
                },
                "market_breadth_avg": round(sum(breadths) / len(breadths), 4) if breadths else None,
            }

            symbol_scores: dict[str, list[float]] = {}
            for r in cycle_rows:
                for c in (r.top_candidates or []):
                    sym = c.get("symbol", "?")
                    symbol_scores.setdefault(sym, []).append(c.get("composite_score", 0))

            top_symbols = sorted(
                symbol_scores.items(), key=lambda x: max(x[1]), reverse=True
            )[:15]
            data["top_symbols"] = [
                {
                    "symbol": sym,
                    "appearances": len(sc),
                    "max_score": round(max(sc), 4),
                    "avg_score": round(sum(sc) / len(sc), 4),
                }
                for sym, sc in top_symbols
            ]

        closed_positions = list(session.scalars(
            select(Position)
            .where(Position.mode == mode, Position.status == "closed")
            .order_by(desc(Position.exit_time))
            .limit(50)
        ).all())

        if closed_positions:
            wins = [p for p in closed_positions if (p.realized_pnl or 0) > 0]
            losses = [p for p in closed_positions if (p.realized_pnl or 0) <= 0]

            # Build per-trade detail with flattened analytics
            trade_details = []
            for p in closed_positions[:20]:
                notes = p.notes or {}
                entry_time = p.entry_time
                if entry_time and entry_time.tzinfo is None:
                    entry_time = entry_time.replace(tzinfo=timezone.utc)
                trade_details.append({
                    "symbol": p.symbol,
                    "pnl": round(p.realized_pnl or 0, 4),
                    "pnl_pct": round(p.realized_pnl_pct or 0, 4),
                    "exit_reason": p.exit_reason,
                    "entry_price": p.entry_price,
                    "exit_price": p.exit_price,
                    "hold_hours": notes.get("hold_hours"),
                    "max_gain_pct": notes.get("max_gain_pct"),
                    "exit_gain_pct": notes.get("exit_gain_pct"),
                    "peak_price": notes.get("peak_price"),
                    "final_stop": notes.get("final_stop"),
                    "btc_at_exit": notes.get("btc_price_at_exit"),
                    "breadth_at_exit": notes.get("market_breadth_at_exit"),
                })

            # Compute advanced stats
            hold_hours_list = [t["hold_hours"] for t in trade_details if t["hold_hours"] is not None]
            max_gains = [t["max_gain_pct"] for t in trade_details if t["max_gain_pct"] is not None]
            exit_gains = [t["exit_gain_pct"] for t in trade_details if t["exit_gain_pct"] is not None]

            # Capture ratio: how much of peak gain was kept at exit
            capture_ratios = []
            for t in trade_details:
                mg = t.get("max_gain_pct")
                eg = t.get("exit_gain_pct")
                if mg and mg > 0 and eg is not None:
                    capture_ratios.append(round(eg / mg, 4))

            data["trade_history"] = {
                "total_trades": len(closed_positions),
                "wins": len(wins),
                "losses": len(losses),
                "win_rate": round(len(wins) / len(closed_positions), 4) if closed_positions else 0,
                "total_pnl": round(sum(p.realized_pnl or 0 for p in closed_positions), 2),
                "avg_win": round(sum(p.realized_pnl or 0 for p in wins) / len(wins), 2) if wins else 0,
                "avg_loss": round(sum(p.realized_pnl or 0 for p in losses) / len(losses), 2) if losses else 0,
                "avg_hold_hours": round(sum(hold_hours_list) / len(hold_hours_list), 2) if hold_hours_list else None,
                "avg_max_gain_pct": round(sum(max_gains) / len(max_gains), 4) if max_gains else None,
                "avg_capture_ratio": round(sum(capture_ratios) / len(capture_ratios), 4) if capture_ratios else None,
                "trades": trade_details,
            }

            exit_reasons: dict[str, dict[str, Any]] = {}
            for p in closed_positions:
                reason = p.exit_reason or "unknown"
                if reason not in exit_reasons:
                    exit_reasons[reason] = {"count": 0, "total_pnl": 0.0, "pnls": []}
                exit_reasons[reason]["count"] += 1
                exit_reasons[reason]["total_pnl"] += p.realized_pnl or 0
                exit_reasons[reason]["pnls"].append(p.realized_pnl or 0)

            data["exit_analysis"] = {
                reason: {
                    "count": info["count"],
                    "total_pnl": round(info["total_pnl"], 2),
                    "avg_pnl": round(info["total_pnl"] / info["count"], 2),
                    "win_rate": round(sum(1 for p in info["pnls"] if p > 0) / info["count"], 4),
                }
                for reason, info in exit_reasons.items()
            }

        equity_rows = list(session.scalars(
            select(EquitySnapshot)
            .where(EquitySnapshot.mode == mode)
            .order_by(desc(EquitySnapshot.recorded_at))
            .limit(100)
        ).all())

        if equity_rows:
            equities = [e.equity for e in equity_rows]
            data["equity_trend"] = {
                "current": equities[0],
                "min": round(min(equities), 2),
                "max": round(max(equities), 2),
                "max_drawdown_pct": round(max(e.drawdown_pct for e in equity_rows), 4),
            }

        risk_rows = list(session.scalars(
            select(RiskEvent)
            .where(RiskEvent.mode == mode)
            .order_by(desc(RiskEvent.created_at))
            .limit(20)
        ).all())

        if risk_rows:
            data["risk_events"] = [
                {"type": r.event_type, "message": r.message, "severity": r.severity, "at": str(r.created_at)}
                for r in risk_rows[:10]
            ]

        # Include recent auto-tune history so AI can see what it changed before
        tune_rows = list(session.scalars(
            select(AITuneLog)
            .where(AITuneLog.mode == mode)
            .order_by(desc(AITuneLog.recorded_at))
            .limit(3)
        ).all())

        if tune_rows:
            data["recent_auto_tunes"] = [
                {
                    "at": str(t.recorded_at),
                    "applied_changes": t.applied_changes,
                    "confidence": t.confidence,
                    "reasoning": (t.reasoning or "")[:200],
                }
                for t in tune_rows
            ]

    # Compute health score from gathered data
    data["health_score"] = compute_health_score(data)

    return data


# ---------------------------------------------------------------------------
# Prompt building
# ---------------------------------------------------------------------------

def build_analysis_prompt(data: dict[str, Any], current_config: dict[str, Any]) -> str:
    """Build the user message for OpenAI with all data."""
    sections = ["# Bot Trading Data for Analysis\n"]

    sections.append("## Current Configuration")
    sections.append(f"```json\n{json.dumps(current_config, indent=2)}\n```\n")

    sections.append("## Parameter Bounds (AI cannot exceed these)")
    sections.append(f"```json\n{json.dumps(TUNABLE_PARAM_BOUNDS, indent=2)}\n```\n")

    # Compute and include health score
    health = compute_health_score(data)
    sections.append("## Strategy Health Score (your optimization target)")
    sections.append(f"```json\n{json.dumps(health, indent=2)}\n```\n")
    sections.append("**Goal: maximize the composite health score (0-100).** "
                     "Focus on improving the weakest component.\n")

    for key, title in [
        ("cycle_summary", "Scan Cycle Summary"),
        ("top_symbols", "Top Scoring Symbols"),
        ("trade_history", "Trade History"),
        ("exit_analysis", "Exit Reason Analysis"),
        ("equity_trend", "Equity Trend"),
        ("risk_events", "Recent Risk Events"),
        ("recent_auto_tunes", "Recent Auto-Tune History"),
    ]:
        if key in data:
            sections.append(f"## {title}")
            sections.append(f"```json\n{json.dumps(data[key], indent=2, default=str)}\n```\n")

    sections.append("## Task")
    sections.append(
        "Analyze the data and respond with a JSON object containing:\n"
        '- "assessment": brief summary of performance\n'
        '- "proposed_changes": object where keys are "section.param" (e.g. "strategy.min_entry_score") '
        "and values have {current, proposed, reason}\n"
        '- "confidence": "low", "medium", or "high"\n'
        '- "warnings": list of risk warnings\n\n'
        "Only propose changes you are confident will improve performance. "
        "Be conservative — small incremental adjustments preferred."
    )

    return "\n".join(sections)


# ---------------------------------------------------------------------------
# OpenAI API call
# ---------------------------------------------------------------------------

def analyze_with_openai(
    api_key: str,
    data: dict[str, Any],
    current_config: dict[str, Any],
    model: str = "gpt-4o",
) -> dict[str, Any]:
    """Send data to OpenAI API and get structured parameter recommendations."""
    import openai

    client = openai.OpenAI(api_key=api_key)
    user_message = build_analysis_prompt(data, current_config)

    response = client.chat.completions.create(
        model=model,
        response_format={"type": "json_object"},
        messages=[
            {"role": "system", "content": SYSTEM_PROMPT},
            {"role": "user", "content": user_message},
        ],
        temperature=0.3,
        max_tokens=4096,
    )

    raw_text = response.choices[0].message.content or "{}"
    return json.loads(raw_text), raw_text


# ---------------------------------------------------------------------------
# Validation & application (safety layer)
# ---------------------------------------------------------------------------

def validate_proposed_changes(
    proposed: dict[str, dict],
    config: AppConfig,
    max_change_pct: float = 0.30,
) -> tuple[dict[str, Any], dict[str, Any]]:
    """Validate and clamp AI-proposed changes against safety bounds.

    Returns (applied_changes, rejected_changes).
    """
    applied: dict[str, Any] = {}
    rejected: dict[str, Any] = {}

    for dotted_key, change_info in proposed.items():
        parts = dotted_key.split(".", 1)
        if len(parts) != 2:
            rejected[dotted_key] = {"reason": "invalid key format"}
            continue

        section, param = parts

        # Check param is in bounds dict
        if section not in TUNABLE_PARAM_BOUNDS or param not in TUNABLE_PARAM_BOUNDS[section]:
            rejected[dotted_key] = {"reason": "not a tunable parameter"}
            continue

        # Get current value from config
        sub_config = getattr(config, section, None)
        if sub_config is None:
            rejected[dotted_key] = {"reason": f"config section '{section}' not found"}
            continue

        current_val = getattr(sub_config, param, None)
        if current_val is None:
            rejected[dotted_key] = {"reason": f"param '{param}' not found in {section}"}
            continue

        proposed_val = change_info.get("proposed")
        if proposed_val is None:
            rejected[dotted_key] = {"reason": "missing 'proposed' value"}
            continue

        try:
            proposed_val = float(proposed_val)
        except (ValueError, TypeError):
            rejected[dotted_key] = {"reason": f"non-numeric proposed value: {proposed_val}"}
            continue

        lo, hi = TUNABLE_PARAM_BOUNDS[section][param]

        # Clamp to bounds
        clamped = max(lo, min(hi, proposed_val))

        # Check max change rate
        current_f = float(current_val)
        if current_f > 0:
            change_ratio = abs(clamped - current_f) / current_f
            if change_ratio > max_change_pct:
                # Clamp the change
                if clamped > current_f:
                    clamped = current_f * (1 + max_change_pct)
                else:
                    clamped = current_f * (1 - max_change_pct)
                clamped = max(lo, min(hi, clamped))

        # Round integers
        if isinstance(current_val, int):
            clamped = int(round(clamped))

        if clamped != current_f:
            applied[dotted_key] = {
                "old": current_val,
                "new": clamped if not isinstance(current_val, int) else int(clamped),
                "reason": change_info.get("reason", ""),
            }
        else:
            rejected[dotted_key] = {"reason": "no effective change after clamping"}

    # Special handling: if strategy weights were changed, re-normalize
    weight_keys = {
        "strategy.volume_weight", "strategy.momentum_weight", "strategy.relative_strength_weight",
        "strategy.ta_weight", "strategy.obi_weight", "strategy.funding_weight",
    }
    changed_weights = {k: v["new"] for k, v in applied.items() if k in weight_keys}
    if changed_weights:
        # Fill in unchanged weights with current values
        all_weights = {
            "strategy.volume_weight": config.strategy.volume_weight,
            "strategy.momentum_weight": config.strategy.momentum_weight,
            "strategy.relative_strength_weight": config.strategy.relative_strength_weight,
            "strategy.ta_weight": config.strategy.ta_weight,
            "strategy.obi_weight": config.strategy.obi_weight,
            "strategy.funding_weight": config.strategy.funding_weight,
        }
        all_weights.update(changed_weights)

        total = sum(all_weights.values())
        if abs(total - 1.0) > 0.01:
            # Re-normalize
            for k in all_weights:
                all_weights[k] = all_weights[k] / total

            # Check bounds after normalization
            valid = True
            for k, v in all_weights.items():
                _, param = k.split(".", 1)
                lo, hi = TUNABLE_PARAM_BOUNDS["strategy"][param]
                if v < lo or v > hi:
                    valid = False
                    break

            if valid:
                for k, v in all_weights.items():
                    if k in applied:
                        applied[k]["new"] = round(v, 4)
                    elif abs(v - getattr(config.strategy, k.split(".")[1])) > 0.001:
                        applied[k] = {
                            "old": getattr(config.strategy, k.split(".")[1]),
                            "new": round(v, 4),
                            "reason": "re-normalized to sum to 1.0",
                        }
            else:
                # Reject all weight changes
                for k in weight_keys:
                    if k in applied:
                        rejected[k] = applied.pop(k)
                        rejected[k]["reason"] = "weights cannot be normalized within bounds"

    return applied, rejected


def apply_tune(config: AppConfig, applied_changes: dict[str, Any]) -> None:
    """Apply validated parameter changes to the in-memory config."""
    for dotted_key, change in applied_changes.items():
        section, param = dotted_key.split(".", 1)
        sub_config = getattr(config, section)
        setattr(sub_config, param, change["new"])


# ---------------------------------------------------------------------------
# High-level API
# ---------------------------------------------------------------------------

def gather_and_analyze(
    db: Database,
    mode: str,
    api_key: str,
    current_config: dict[str, Any],
    model: str = "gpt-4o",
    cycles: int = 500,
) -> dict[str, Any]:
    """CLI analyze command: gather data → call OpenAI → return human-readable results."""
    data = _gather_data(db, mode, cycles)

    if not data:
        return {"error": "No data found. Run the bot first to collect scan data."}

    parsed, raw_text = analyze_with_openai(api_key, data, current_config, model)

    return {
        "data_summary": {
            "cycles_analyzed": data.get("cycle_summary", {}).get("total_cycles", 0),
            "trades_analyzed": data.get("trade_history", {}).get("total_trades", 0),
        },
        "health_score": data.get("health_score", {}),
        "assessment": parsed.get("assessment", ""),
        "proposed_changes": parsed.get("proposed_changes", {}),
        "confidence": parsed.get("confidence", "unknown"),
        "warnings": parsed.get("warnings", []),
        "raw_response": raw_text,
    }


def _consensus_filter(
    primary_proposed: dict[str, dict],
    secondary_proposed: dict[str, dict],
    current_config: dict[str, Any],
) -> dict[str, dict]:
    """Keep only changes where both models agree on direction (increase vs decrease)."""
    agreed: dict[str, dict] = {}
    for key, primary_change in primary_proposed.items():
        if key not in secondary_proposed:
            continue
        p_val = primary_change.get("proposed")
        s_val = secondary_proposed[key].get("proposed")
        c_val = primary_change.get("current")
        if p_val is None or s_val is None or c_val is None:
            continue
        try:
            p_val, s_val, c_val = float(p_val), float(s_val), float(c_val)
        except (ValueError, TypeError):
            continue
        # Both must agree on direction (both increase or both decrease)
        if (p_val - c_val) * (s_val - c_val) > 0:
            # Use the more conservative change (closer to current)
            if abs(p_val - c_val) <= abs(s_val - c_val):
                agreed[key] = primary_change
            else:
                agreed[key] = secondary_proposed[key]
    return agreed


def analyze_with_consensus(
    api_key: str,
    data: dict[str, Any],
    current_config: dict[str, Any],
    primary_model: str,
    secondary_model: str,
) -> tuple[dict[str, Any], str]:
    """Query two models and return only changes both agree on."""
    primary_parsed, primary_raw = analyze_with_openai(api_key, data, current_config, primary_model)

    try:
        secondary_parsed, _ = analyze_with_openai(api_key, data, current_config, secondary_model)
    except Exception:
        # Secondary model failed — fall back to primary only
        return primary_parsed, primary_raw

    primary_proposed = primary_parsed.get("proposed_changes", {})
    secondary_proposed = secondary_parsed.get("proposed_changes", {})

    agreed = _consensus_filter(primary_proposed, secondary_proposed, current_config)

    # Merge: use primary's assessment/warnings but consensus changes
    result = {
        "assessment": primary_parsed.get("assessment", ""),
        "proposed_changes": agreed,
        "confidence": primary_parsed.get("confidence", "low"),
        "warnings": primary_parsed.get("warnings", []) + [
            f"Consensus: {len(agreed)}/{len(primary_proposed)} primary changes confirmed by {secondary_model}"
        ],
    }
    return result, primary_raw


def _snapshot_config(config: AppConfig) -> dict[str, Any]:
    """Dump the tunable config sections for snapshot storage."""
    return {
        "strategy": config.strategy.model_dump(),
        "position": config.position.model_dump(),
        "risk": config.risk.model_dump(),
    }


def restore_config_from_snapshot(config: AppConfig, snapshot: dict[str, Any]) -> None:
    """Restore tunable params from a snapshot dict."""
    for section_name in ("strategy", "position", "risk"):
        section_data = snapshot.get(section_name, {})
        sub_config = getattr(config, section_name, None)
        if sub_config is None:
            continue
        for param, value in section_data.items():
            if hasattr(sub_config, param):
                setattr(sub_config, param, value)


def check_auto_rollback(
    db: Database,
    mode: str,
    config: AppConfig,
    current_equity: float,
    rollback_drop_pct: float = 0.05,
    rollback_eval_cycles: int = 50,
) -> dict[str, Any] | None:
    """Check if the last AI tune caused a performance drop and rollback if so.

    Returns rollback info dict if rolled back, None otherwise.
    """
    with db.session() as session:
        snap = get_latest_pre_tune_snapshot(session, mode)
        if snap is None:
            return None

        # Update tracking
        snap.post_tune_cycles = (snap.post_tune_cycles or 0) + 1
        snap.post_tune_equity_latest = current_equity

        # Not enough cycles to evaluate yet
        if snap.post_tune_cycles < rollback_eval_cycles:
            session.commit()
            return None

        start_eq = snap.post_tune_equity_start or current_equity
        if start_eq <= 0:
            session.commit()
            return None

        drop_pct = (start_eq - current_equity) / start_eq
        if drop_pct >= rollback_drop_pct:
            # Rollback: restore config from snapshot
            restore_config_from_snapshot(config, snap.config_snapshot)
            snap.rolled_back = 1
            session.commit()
            return {
                "rolled_back": True,
                "trigger_cycle": snap.trigger_cycle,
                "equity_start": start_eq,
                "equity_now": current_equity,
                "drop_pct": round(drop_pct, 4),
            }

        session.commit()
    return None


def auto_tune_cycle(
    db: Database,
    mode: str,
    config: AppConfig,
    api_key: str,
    cycle_number: int,
    current_equity: float | None = None,
) -> dict[str, Any]:
    """Run one auto-tune cycle: gather data → AI → validate → apply → log."""
    data = _gather_data(db, mode, cycles=config.auto_tune.require_min_cycles)

    total_cycles = data.get("cycle_summary", {}).get("total_cycles", 0)
    total_trades = data.get("trade_history", {}).get("total_trades", 0)

    if total_cycles < config.auto_tune.require_min_cycles:
        return {"status": "skipped", "reason": f"need {config.auto_tune.require_min_cycles} cycles, have {total_cycles}"}

    if total_trades < config.auto_tune.require_min_trades:
        return {"status": "skipped", "reason": f"need {config.auto_tune.require_min_trades} trades, have {total_trades}"}

    current_config = _snapshot_config(config)
    model = config.auto_tune.openai_model
    secondary_model = config.auto_tune.secondary_model

    try:
        if secondary_model:
            parsed, raw_text = analyze_with_consensus(api_key, data, current_config, model, secondary_model)
        else:
            parsed, raw_text = analyze_with_openai(api_key, data, current_config, model)
    except Exception as exc:
        with db.session() as session:
            record_ai_tune_log(
                session, mode, cycle_number, model,
                data_summary={"cycles": total_cycles, "trades": total_trades},
                raw_response=str(exc),
                proposed_changes={}, applied_changes={},
                status="error",
            )
            session.commit()
        return {"status": "error", "error": str(exc)}

    proposed = parsed.get("proposed_changes", {})
    applied, rejected = validate_proposed_changes(proposed, config, config.auto_tune.max_change_pct)

    if applied:
        # Save pre-tune snapshot for rollback
        with db.session() as session:
            record_pre_tune_snapshot(
                session, mode, cycle_number, current_config, equity=current_equity,
            )
            session.commit()
        apply_tune(config, applied)

    with db.session() as session:
        record_ai_tune_log(
            session, mode, cycle_number, model,
            data_summary={"cycles": total_cycles, "trades": total_trades},
            raw_response=raw_text,
            proposed_changes=proposed,
            applied_changes=applied,
            rejected_changes=rejected if rejected else None,
            reasoning=parsed.get("assessment", ""),
            confidence=parsed.get("confidence"),
            status="applied" if applied else "skipped",
        )
        session.commit()

    return {
        "status": "applied" if applied else "no_changes",
        "applied": applied,
        "rejected": rejected,
        "confidence": parsed.get("confidence"),
        "assessment": parsed.get("assessment", "")[:200],
    }
