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
    RiskEvent,
    record_ai_tune_log,
)

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

            data["trade_history"] = {
                "total_trades": len(closed_positions),
                "wins": len(wins),
                "losses": len(losses),
                "win_rate": round(len(wins) / len(closed_positions), 4) if closed_positions else 0,
                "total_pnl": round(sum(p.realized_pnl or 0 for p in closed_positions), 2),
                "avg_win": round(sum(p.realized_pnl or 0 for p in wins) / len(wins), 2) if wins else 0,
                "avg_loss": round(sum(p.realized_pnl or 0 for p in losses) / len(losses), 2) if losses else 0,
                "trades": [
                    {
                        "symbol": p.symbol,
                        "pnl": round(p.realized_pnl or 0, 4),
                        "pnl_pct": round(p.realized_pnl_pct or 0, 4),
                        "exit_reason": p.exit_reason,
                        "notes": p.notes,
                    }
                    for p in closed_positions[:20]
                ],
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
    weight_keys = {"strategy.volume_weight", "strategy.momentum_weight", "strategy.relative_strength_weight"}
    changed_weights = {k: v["new"] for k, v in applied.items() if k in weight_keys}
    if changed_weights:
        # Fill in unchanged weights with current values
        all_weights = {
            "strategy.volume_weight": config.strategy.volume_weight,
            "strategy.momentum_weight": config.strategy.momentum_weight,
            "strategy.relative_strength_weight": config.strategy.relative_strength_weight,
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
        "assessment": parsed.get("assessment", ""),
        "proposed_changes": parsed.get("proposed_changes", {}),
        "confidence": parsed.get("confidence", "unknown"),
        "warnings": parsed.get("warnings", []),
        "raw_response": raw_text,
    }


def auto_tune_cycle(
    db: Database,
    mode: str,
    config: AppConfig,
    api_key: str,
    cycle_number: int,
) -> dict[str, Any]:
    """Run one auto-tune cycle: gather data → AI → validate → apply → log."""
    data = _gather_data(db, mode, cycles=config.auto_tune.require_min_cycles)

    total_cycles = data.get("cycle_summary", {}).get("total_cycles", 0)
    total_trades = data.get("trade_history", {}).get("total_trades", 0)

    if total_cycles < config.auto_tune.require_min_cycles:
        return {"status": "skipped", "reason": f"need {config.auto_tune.require_min_cycles} cycles, have {total_cycles}"}

    if total_trades < config.auto_tune.require_min_trades:
        return {"status": "skipped", "reason": f"need {config.auto_tune.require_min_trades} trades, have {total_trades}"}

    current_config = {
        "strategy": config.strategy.model_dump(),
        "position": config.position.model_dump(),
        "risk": config.risk.model_dump(),
    }

    model = config.auto_tune.openai_model

    try:
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
