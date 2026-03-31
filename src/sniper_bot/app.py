from __future__ import annotations

import threading
import time
from dataclasses import dataclass, field
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from sniper_bot.alerts import TelegramNotifier, format_alert
from sniper_bot.config import AppConfig, get_optional_secret, get_required_secret, load_config, resolve_path
from sniper_bot.exchange import BybitClient
from sniper_bot.execution import BybitBroker, FillResult, PaperBroker
from sniper_bot.logging_config import configure_logging, get_logger
from sniper_bot.positions import evaluate_position
from sniper_bot.risk import (
    RiskCheck,
    check_drawdown_halt,
    check_market_regime,
    check_portfolio_gates,
    position_size,
    record_closed_trade,
    reset_drawdown_state,
    sync_daily_state,
    update_equity_state,
)
from sniper_bot.scanner import TokenCandidate, compute_volume_spike, scan_market
from sniper_bot.storage import (
    Database,
    close_position,
    get_open_position_for_symbol,
    get_open_positions,
    get_or_create_state,
    open_position,
    record_cycle_log,
    record_equity_snapshot,
    record_order,
    record_risk_event,
    record_signal,
    upsert_pair,
)
from sniper_bot.strategy import compute_price_changes, rank_candidates, score_candidate

LOGGER = get_logger(__name__)


@dataclass
class Runtime:
    config: AppConfig
    config_path: Path
    mode: str
    db: Database
    bybit: BybitClient
    notifier: TelegramNotifier | None
    cycle_count: int = 0
    last_tune_cycle: int = 0          # cycle when last tune ran
    last_regime_allowed: bool = True   # previous regime state for change detection
    _tune_lock: threading.Lock = field(default_factory=threading.Lock, repr=False)
    _tune_thread: threading.Thread | None = field(default=None, repr=False)

    def close(self) -> None:
        self.bybit.close()
        if self.notifier:
            self.notifier.close()


def create_runtime(config_path: Path, mode: str | None = None, require_private: bool = False) -> Runtime:
    config = load_config(config_path)
    active_mode = mode or config.mode
    base_dir = config_path.parent.resolve()

    log_dir = resolve_path(base_dir, config.paths.log_dir)
    configure_logging(log_dir)

    db_path = resolve_path(base_dir, config.database_path_for_mode(active_mode))
    db = Database(db_path)
    db.create_tables()

    api_key = get_required_secret("BYBIT_API_KEY") if require_private else get_optional_secret("BYBIT_API_KEY")
    api_secret = get_required_secret("BYBIT_API_SECRET") if require_private else get_optional_secret("BYBIT_API_SECRET")
    bybit = BybitClient(config.exchange, api_key=api_key, api_secret=api_secret)

    notifier = None
    if config.alerts.enabled:
        token = get_optional_secret("TELEGRAM_BOT_TOKEN")
        chat_id = get_optional_secret("TELEGRAM_CHAT_ID")
        if token and chat_id:
            notifier = TelegramNotifier(token, chat_id)

    return Runtime(
        config=config,
        config_path=config_path,
        mode=active_mode,
        db=db,
        bybit=bybit,
        notifier=notifier,
    )


# ---------------------------------------------------------------------------
# Core loop
# ---------------------------------------------------------------------------

def run_bot(config_path: Path, mode: str, once: bool = False, confirm_live: bool = False) -> None:
    config = load_config(config_path)
    _validate_run(config, mode, confirm_live)

    runtime = create_runtime(config_path, mode=mode, require_private=(mode in {"demo", "live"}))
    try:
        _notify(runtime, "Bot Started", [f"Mode: {mode}", "Scanning all USDT spot pairs"])
        while True:
            try:
                result = process_once(runtime)
                runtime.cycle_count += 1
                LOGGER.info("cycle_complete", extra={"result": result, "cycle": runtime.cycle_count})
            except Exception as exc:
                LOGGER.exception("cycle_error")
                _notify(runtime, "Cycle Error", [str(exc)[:200]])

            # Auto-tune check
            _maybe_auto_tune(runtime)

            if once:
                break
            time.sleep(runtime.config.execution.poll_interval_seconds)
    except KeyboardInterrupt:
        _notify(runtime, "Bot Stopped", [f"Mode: {mode}", "Stopped by operator"])
    finally:
        runtime.close()


def process_once(runtime: Runtime) -> dict[str, Any]:
    """Execute one full scan-score-risk-execute-manage cycle."""
    config = runtime.config
    mode = runtime.mode
    now = datetime.now(timezone.utc)

    with runtime.db.session() as session:
        state = get_or_create_state(session, mode)

        # Initialize paper balances
        if mode == "paper":
            broker = PaperBroker(config.execution)
            broker.sync_initial_balances(state, config.risk.initial_paper_cash)
        else:
            broker = BybitBroker(config.execution, runtime.bybit)
            broker.sync_balances(state)

        # --- Phase 1: Fetch all tickers ---
        tickers = runtime.bybit.fetch_all_tickers()
        candidates = scan_market(tickers, config.scanner)
        state.last_scan_at = now

        # Compute market context for logging
        btc_ticker = _find_ticker(tickers, "BTCUSDT")
        usdt_tickers = [t for t in tickers if t.symbol.endswith("USDT")]
        positive_count = sum(1 for t in usdt_tickers if t.price_change_24h_pct > 0)
        market_breadth = positive_count / len(usdt_tickers) if usdt_tickers else 0.0
        turnover_values = sorted([t.turnover_24h for t in usdt_tickers])
        median_turnover = turnover_values[len(turnover_values) // 2] if turnover_values else 1.0
        avg_turnover = sum(t.turnover_24h for t in usdt_tickers) / len(usdt_tickers) if usdt_tickers else 0.0

        # --- Phase 2: Manage existing positions ---
        open_pos = get_open_positions(session, mode)
        position_value = 0.0
        exits_this_cycle: list[dict] = []

        for pos in open_pos:
            ticker = _find_ticker(tickers, pos.symbol)
            if ticker is None:
                continue
            current_price = ticker.last_price

            exit_reason = evaluate_position(pos, current_price, config.position, now)
            if exit_reason:
                fill = broker.execute_sell(state, pos.symbol, pos.quantity, current_price)
                usdt_received = fill.cost - fill.fee

                # Compute trade analytics before closing
                _entry_time: datetime | None = getattr(pos, "entry_time", None)
                if _entry_time is not None and _entry_time.tzinfo is None:
                    _entry_time = _entry_time.replace(tzinfo=timezone.utc)
                hold_hours = (now - _entry_time).total_seconds() / 3600 if _entry_time else 0.0
                _entry_px: float = getattr(pos, "entry_price", 0.0) or 0.0
                _max_px: float = getattr(pos, "max_price", 0.0) or 0.0
                _stop_px: float = getattr(pos, "stop_price", 0.0) or 0.0
                max_gain_pct = (_max_px - _entry_px) / _entry_px if _entry_px else 0.0
                exit_gain_pct = (fill.avg_price - _entry_px) / _entry_px if _entry_px else 0.0

                # Store analytics in position notes
                trade_notes = {
                    "hold_hours": round(hold_hours, 2),
                    "max_gain_pct": round(max_gain_pct, 4),
                    "exit_gain_pct": round(exit_gain_pct, 4),
                    "peak_price": _max_px,
                    "final_stop": _stop_px,
                    "btc_price_at_exit": btc_ticker.last_price if btc_ticker else None,
                    "market_breadth_at_exit": round(market_breadth, 4),
                }
                object.__setattr__(pos, "notes", trade_notes)

                pnl = close_position(session, pos, fill.avg_price, usdt_received, exit_reason)
                record_order(session, mode, pos.symbol, "sell", pos.quantity, current_price, fill.avg_price, fill.fee, fill.cost, "filled", fill.order_id)
                cooldown_started = record_closed_trade(state, config.risk, now, pnl)
                exits_this_cycle.append({
                    "symbol": pos.symbol, "pnl": pnl, "reason": exit_reason,
                    "hold_hours": round(hold_hours, 2), "max_gain_pct": round(max_gain_pct, 4),
                })
                _notify(runtime, "Trade Closed", [
                    f"Symbol: {pos.symbol}",
                    f"PnL: {pnl:+.2f} USDT ({pos.realized_pnl_pct:+.1%})",
                    f"Reason: {exit_reason}",
                    f"Hold: {hold_hours:.1f}h | Peak gain: {max_gain_pct:+.1%}",
                ])
                if cooldown_started:
                    record_risk_event(session, mode, "cooldown_started", "Cooldown after consecutive losses")
                    _notify(runtime, "Cooldown Started", [f"Until: {state.cooldown_until}"])
            else:
                position_value += pos.quantity * current_price

        # --- Phase 3: Update equity ---
        equity = state.usdt_balance + position_value
        sync_daily_state(state, now, equity)
        drawdown_pct = update_equity_state(state, equity)
        state.position_value = position_value
        record_equity_snapshot(session, mode, equity, state.usdt_balance, position_value, state.high_water_mark or equity, drawdown_pct)

        # --- Phase 4: Check drawdown halt ---
        if check_drawdown_halt(state, config.risk):
            state.status = "HALTED"
            state.halted_at = now
            state.halt_reason = "max_drawdown"
            record_risk_event(session, mode, "drawdown_halt", f"Drawdown {drawdown_pct:.1%} exceeds {config.risk.max_drawdown_pct:.0%}", severity="critical")
            _notify(runtime, "DRAWDOWN HALT", [f"Equity: {equity:.2f}", f"Drawdown: {drawdown_pct:.1%}", "Trading halted. Manual reset required."])
            session.commit()
            return {"status": "halted", "drawdown_pct": drawdown_pct}

        # --- Phase 5: Risk gate check for new entries ---
        open_pos = get_open_positions(session, mode)  # refresh after exits
        risk_check = check_portfolio_gates(state, open_pos, config.risk, now)

        entries_this_cycle: list[dict] = []
        cycle_entry_action = "no_signal"
        cycle_entry_symbol = None
        cycle_block_reason = None
        cycle_top_candidates: list[dict] = []

        if not risk_check.entry_allowed:
            cycle_entry_action = "blocked"
            cycle_block_reason = risk_check.reason

        if risk_check.entry_allowed and candidates:
            # Fetch true BTC 1h change from klines (not 24h ticker change)
            btc_change_1h = 0.0
            try:
                btc_1h_candles = runtime.bybit.fetch_klines("BTCUSDT", 60, limit=3)
                if len(btc_1h_candles) >= 2:
                    prev_close = btc_1h_candles[-2]["close"]
                    last_close = btc_1h_candles[-1]["close"]
                    if prev_close > 0:
                        btc_change_1h = (last_close - prev_close) / prev_close
            except Exception:
                LOGGER.debug("btc_1h_kline_fetch_failed")

            enriched: list = []

            # Fetch BTC funding rate once for all candidates
            btc_funding = 0.0
            try:
                btc_funding = runtime.bybit.fetch_funding_rate("BTCUSDT") or 0.0
            except Exception:
                pass

            # Market regime gate: block entries in bear conditions
            regime_allowed, regime_reason = check_market_regime(config.risk, btc_change_1h, market_breadth)
            # Track regime flips for adaptive tune triggers
            if regime_allowed != runtime.last_regime_allowed:
                LOGGER.info("regime_change", extra={"from": runtime.last_regime_allowed, "to": regime_allowed})
                runtime.last_regime_allowed = regime_allowed
            if not regime_allowed:
                cycle_entry_action = "blocked"
                cycle_block_reason = regime_reason
                LOGGER.info("regime_gate_blocked", extra={"reason": regime_reason, "btc_1h": btc_change_1h, "breadth": market_breadth})
            else:
                enriched = _enrich_and_score(runtime, candidates[:config.scanner.max_candidates_to_enrich], btc_change_1h, open_pos, btc_funding)

            # Record all enriched candidates for analysis (before threshold filter)
            cycle_top_candidates = [
                {
                    "symbol": s.symbol,
                    "composite_score": round(s.composite_score, 4),
                    "volume_score": round(s.volume_score, 4),
                    "momentum_score": round(s.momentum_score, 4),
                    "relative_strength": round(s.relative_strength_score, 4),
                    "ta_score": round(s.ta_score, 4),
                    "obi_score": round(s.obi_score, 4),
                    "funding_score": round(s.funding_score, 4),
                    "volume_spike_ratio": round(s.volume_spike_ratio, 4),
                    "price": s.price,
                }
                for s in sorted(enriched, key=lambda x: x.composite_score, reverse=True)
            ]

            ranked = rank_candidates(enriched, config.strategy)

            if not ranked:
                cycle_entry_action = "below_threshold"
            else:
                cycle_entry_action = "no_entry"

            # Compute Kelly fraction from historical trades
            kelly_pct: float | None = None
            if config.risk.use_kelly:
                from sniper_bot.risk import compute_kelly_pct
                from sniper_bot.storage import Position as PositionModel
                from sqlalchemy import desc as desc_
                closed = list(session.scalars(
                    select(PositionModel)
                    .where(PositionModel.mode == mode, PositionModel.status == "closed")
                    .order_by(desc_(PositionModel.exit_time))
                    .limit(100)
                ).all())
                if len(closed) >= config.risk.kelly_min_trades:
                    wins = [p for p in closed if (p.realized_pnl_pct or 0) > 0]
                    losses = [p for p in closed if (p.realized_pnl_pct or 0) <= 0]
                    win_rate = len(wins) / len(closed) if closed else 0
                    avg_win = sum(abs(p.realized_pnl_pct or 0) for p in wins) / len(wins) if wins else 0
                    avg_loss = sum(abs(p.realized_pnl_pct or 0) for p in losses) / len(losses) if losses else 0.01
                    kelly_pct = compute_kelly_pct(win_rate, avg_win, avg_loss, config.risk.kelly_fraction)

            for scored in ranked[:config.strategy.max_entries_per_cycle]:
                # Skip if already holding
                if get_open_position_for_symbol(session, mode, scored.symbol):
                    continue

                qty = position_size(config.risk, equity, scored.price, state.usdt_balance, scored.composite_score, kelly_pct)
                if qty <= 0:
                    continue

                # Check minimum order
                instruments = runtime.bybit.fetch_all_instruments()
                inst = instruments.get(scored.symbol)
                if inst and qty < inst.min_order_qty:
                    continue
                if inst and qty * scored.price < inst.min_order_amount:
                    continue

                fill = broker.execute_buy(state, scored.symbol, qty, scored.price)
                usdt_invested = fill.cost + fill.fee
                stop_price = fill.avg_price * (1 - config.position.trailing_stop_pct)

                record_order(session, mode, scored.symbol, "buy", fill.quantity, scored.price, fill.avg_price, fill.fee, fill.cost, "filled", fill.order_id)
                record_signal(session, mode, scored.symbol, "enter", "momentum_score", scored.composite_score, scored.volume_spike_ratio, scored.momentum_score, scored.relative_strength_score, scored.price)
                open_position(session, mode, scored.symbol, fill.avg_price, fill.quantity, usdt_invested, stop_price)

                entries_this_cycle.append({"symbol": scored.symbol, "score": scored.composite_score, "qty": fill.quantity, "price": fill.avg_price})
                cycle_entry_action = "entered"
                cycle_entry_symbol = scored.symbol
                _notify(runtime, "Trade Opened", [
                    f"Symbol: {scored.symbol}",
                    f"Score: {scored.composite_score:.2f}",
                    f"Price: {fill.avg_price:.6f}",
                    f"Qty: {fill.quantity:.6f}",
                    f"Stop: {stop_price:.6f}",
                ])

        record_cycle_log(
            session, mode,
            candidates_scanned=len(candidates),
            top_candidates=cycle_top_candidates,
            entry_action=cycle_entry_action,
            entry_symbol=cycle_entry_symbol,
            block_reason=cycle_block_reason,
            market_context={
                "btc_price": btc_ticker.last_price if btc_ticker else None,
                "btc_change_24h_pct": btc_ticker.price_change_24h_pct if btc_ticker else None,
                "market_breadth_pct": round(market_breadth, 4),
                "total_tickers": len(usdt_tickers),
                "avg_volume_ratio": round(avg_turnover / median_turnover, 4) if median_turnover > 0 else None,
                "equity": equity,
                "open_positions_count": len(open_pos),
            },
            config_snapshot={
                "min_entry_score": config.strategy.min_entry_score,
                "volume_weight": config.strategy.volume_weight,
                "momentum_weight": config.strategy.momentum_weight,
                "rs_weight": config.strategy.relative_strength_weight,
                "trailing_stop_pct": config.position.trailing_stop_pct,
                "hard_stop_pct": config.position.hard_stop_pct,
                "take_profit_multiple": config.position.take_profit_multiple,
                "max_position_pct": config.risk.max_position_pct,
                "regime_gate_enabled": config.risk.regime_gate_enabled,
            },
        )

        session.commit()
        return {
            "status": "ok",
            "scanned": len(candidates),
            "entries": entries_this_cycle,
            "exits": exits_this_cycle,
            "equity": equity,
            "drawdown_pct": drawdown_pct,
            "open_positions": len(open_pos),
        }


# ---------------------------------------------------------------------------
# Scan command (one-shot)
# ---------------------------------------------------------------------------

def scan_once(runtime: Runtime) -> list[dict[str, Any]]:
    """One-shot scan: return top momentum pairs with scores."""
    tickers = runtime.bybit.fetch_all_tickers()
    candidates = scan_market(tickers, runtime.config.scanner)

    btc_change_1h = 0.0
    try:
        btc_1h_candles = runtime.bybit.fetch_klines("BTCUSDT", 60, limit=3)
        if len(btc_1h_candles) >= 2 and btc_1h_candles[-2]["close"] > 0:
            btc_change_1h = (btc_1h_candles[-1]["close"] - btc_1h_candles[-2]["close"]) / btc_1h_candles[-2]["close"]
    except Exception:
        pass

    btc_funding = runtime.bybit.fetch_funding_rate("BTCUSDT") or 0.0

    scored = _enrich_and_score(runtime, candidates[:runtime.config.scanner.max_candidates_to_enrich], btc_change_1h, [], btc_funding)
    ranked = rank_candidates(scored, runtime.config.strategy)

    return [
        {
            "symbol": s.symbol,
            "score": s.composite_score,
            "volume_score": s.volume_score,
            "momentum_score": s.momentum_score,
            "rs_score": s.relative_strength_score,
            "ta_score": s.ta_score,
            "obi_score": s.obi_score,
            "funding_score": s.funding_score,
            "spike_ratio": s.volume_spike_ratio,
            "price": s.price,
        }
        for s in ranked[:20]
    ]


# ---------------------------------------------------------------------------
# Status
# ---------------------------------------------------------------------------

def get_status(runtime: Runtime) -> dict[str, Any]:
    with runtime.db.session() as session:
        state = get_or_create_state(session, runtime.mode)
        positions = get_open_positions(session, runtime.mode)
        return {
            "mode": runtime.mode,
            "status": state.status,
            "usdt_balance": state.usdt_balance,
            "position_value": state.position_value,
            "equity": state.last_equity,
            "high_water_mark": state.high_water_mark,
            "daily_realized_pnl": state.daily_realized_pnl,
            "consecutive_losses": state.consecutive_losses,
            "cooldown_until": state.cooldown_until.isoformat() if state.cooldown_until else None,
            "halt_reason": state.halt_reason,
            "open_positions": [
                {
                    "symbol": p.symbol,
                    "entry_price": p.entry_price,
                    "quantity": p.quantity,
                    "usdt_invested": p.usdt_invested,
                    "max_price": p.max_price,
                    "stop_price": p.stop_price,
                    "entry_time": p.entry_time.isoformat(),
                }
                for p in positions
            ],
        }


def get_balance(runtime: Runtime) -> dict[str, Any]:
    """Read real balance from Bybit account."""
    result: dict[str, Any] = {"mode": runtime.mode}

    if runtime.mode == "paper":
        with runtime.db.session() as session:
            state = get_or_create_state(session, runtime.mode)
            result["source"] = "paper_db"
            result["usdt_balance"] = state.usdt_balance
            result["position_value"] = state.position_value
            result["equity"] = state.last_equity
    else:
        try:
            balances = runtime.bybit.get_balance(["USDT", "BTC", "ETH", "SOL"])
            result["source"] = "bybit_api"
            result["balances"] = balances
            result["usdt_balance"] = balances.get("USDT", 0.0)
        except Exception as exc:
            result["source"] = "error"
            result["error"] = str(exc)[:200]

    return result


def reset_drawdown(runtime: Runtime) -> dict[str, str]:
    with runtime.db.session() as session:
        state = get_or_create_state(session, runtime.mode)
        reset_drawdown_state(state)
        session.commit()
    _notify(runtime, "Drawdown Reset", [f"Mode: {runtime.mode}", "Halt cleared by operator"])
    return {"status": "reset"}


# ---------------------------------------------------------------------------
# Healthcheck
# ---------------------------------------------------------------------------

def healthcheck(runtime: Runtime) -> dict[str, Any]:
    report: dict[str, Any] = {"config": "ok"}

    with runtime.db.session() as session:
        get_or_create_state(session, runtime.mode)
        report["database"] = "ok"

    try:
        tickers = runtime.bybit.fetch_all_tickers()
        report["bybit_public"] = {"status": "ok", "pairs_count": len(tickers)}
    except Exception as exc:
        report["bybit_public"] = {"status": "error", "error": str(exc)[:100]}

    if runtime.notifier:
        try:
            runtime.notifier.send_message(format_alert("Healthcheck", ["All systems OK"]))
            report["telegram"] = "ok"
        except Exception as exc:
            report["telegram"] = {"status": "error", "error": str(exc)[:100]}
    else:
        report["telegram"] = "skipped_no_credentials"

    return report


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _enrich_and_score(runtime, candidates, btc_change_1h, open_positions, btc_funding: float = 0.0):
    """Fetch klines, order book, and TA indicators for candidates and compute scores."""
    from sniper_bot.indicators import (
        compute_bollinger_bands,
        compute_funding_rate_signal,
        compute_macd,
        compute_obi_score,
        compute_rsi,
        compute_ta_composite,
    )
    from sniper_bot.strategy import ScoredToken, score_candidate, compute_price_changes

    held_symbols = {p.symbol for p in open_positions}
    scored: list[ScoredToken] = []

    for c in candidates:
        if c.symbol in held_symbols:
            continue
        try:
            candles_5m = runtime.bybit.fetch_klines(c.symbol, 5, limit=48)
            candles_1h = runtime.bybit.fetch_klines(c.symbol, 60, limit=30)
        except Exception:
            LOGGER.debug("kline_fetch_failed", extra={"symbol": c.symbol})
            continue

        price_changes = compute_price_changes(candles_5m, candles_1h)

        # Volume spike: compare last 12 bars (1h) vs prior bars as baseline
        if len(candles_5m) >= 24:
            recent_bars = candles_5m[-12:]
            baseline_bars = candles_5m[:-12]
            baseline_avg = sum(b["volume"] for b in baseline_bars) / len(baseline_bars) if baseline_bars else 0
            recent_avg = sum(b["volume"] for b in recent_bars) / len(recent_bars) if recent_bars else 0
            spike_ratio = (recent_avg / baseline_avg) if baseline_avg > 0 else 1.0
        else:
            bars_per_day_5m = 288
            avg_vol_per_bar = c.volume_24h / bars_per_day_5m if c.volume_24h > 0 else 0
            spike_ratio = compute_volume_spike(candles_5m[-3:] if len(candles_5m) >= 3 else candles_5m, avg_vol_per_bar)

        # --- TA indicators (from 1h candles, need ~26+ for MACD) ---
        rsi = compute_rsi(candles_1h, period=14)
        macd = compute_macd(candles_1h, fast=12, slow=26, signal=9)
        bbands = compute_bollinger_bands(candles_1h, period=20, std_dev=2.0)
        ta_composite = compute_ta_composite(rsi, macd, bbands)

        # --- Order Book Imbalance ---
        obi_value = 0.0
        try:
            ob = runtime.bybit.fetch_orderbook(c.symbol, depth=25)
            obi_value = compute_obi_score(ob["bids"], ob["asks"])
        except Exception:
            LOGGER.debug("orderbook_fetch_failed", extra={"symbol": c.symbol})

        # --- Funding Rate Signal (use BTC funding as proxy for all pairs) ---
        funding_signal = compute_funding_rate_signal(btc_funding) if btc_funding else 0.0
        # If the pair has its own perp, try to get its specific funding
        try:
            pair_funding = runtime.bybit.fetch_funding_rate(c.symbol)
            if pair_funding is not None:
                funding_signal = compute_funding_rate_signal(pair_funding)
        except Exception:
            pass

        s = score_candidate(
            c.symbol, c.last_price, spike_ratio, price_changes, btc_change_1h,
            runtime.config.strategy,
            ta_composite=ta_composite,
            obi_value=obi_value,
            funding_signal=funding_signal,
        )
        scored.append(s)

    return scored


def _find_ticker(tickers, symbol):
    for t in tickers:
        if t.symbol == symbol:
            return t
    return None


def _validate_run(config: AppConfig, mode: str, confirm_live: bool) -> None:
    if mode == "live" and not confirm_live:
        raise RuntimeError("Live mode requires --confirm-live flag")
    if mode == "live" and config.exchange.environment != "live":
        raise RuntimeError("Live mode requires exchange.environment: live in config")
    if mode == "demo" and config.exchange.environment not in {"demo", "live"}:
        raise RuntimeError("Demo mode requires exchange.environment: demo in config")


def _should_tune_now(runtime: Runtime, current_equity: float) -> tuple[bool, str]:
    """Check if an adaptive trigger fires, or if the fixed interval is reached."""
    cfg = runtime.config.auto_tune
    cycles_since_tune = runtime.cycle_count - runtime.last_tune_cycle

    # Hard cooldown — never tune more often than min_cycles_between_tunes
    if cycles_since_tune < cfg.min_cycles_between_tunes:
        return False, ""

    # Fixed interval fallback
    if runtime.cycle_count % cfg.tune_every_n_cycles == 0:
        return True, "scheduled_interval"

    # Adaptive: drawdown trigger
    with runtime.db.session() as session:
        state = get_or_create_state(session, runtime.mode)
        hwm = float(state.high_water_mark or current_equity)
        consec_losses = int(state.consecutive_losses or 0)

    if hwm > 0 and current_equity > 0:
        drawdown = (hwm - current_equity) / hwm
        if drawdown >= cfg.trigger_on_drawdown_pct:
            return True, f"drawdown_{drawdown:.2%}"

    # Adaptive: consecutive losses
    if consec_losses >= cfg.trigger_on_consecutive_losses:
        return True, f"consecutive_losses_{consec_losses}"

    return False, ""


def _maybe_auto_tune(runtime: Runtime) -> None:
    """Run AI auto-tune if enabled and a trigger fires."""
    cfg = runtime.config.auto_tune
    if not cfg.enabled:
        return
    if runtime.cycle_count == 0:
        return

    # Get current equity for rollback tracking
    with runtime.db.session() as session:
        state = get_or_create_state(session, runtime.mode)
        current_equity = float(state.last_equity or state.usdt_balance or 0)

    # Check auto-rollback every cycle (cheap DB read)
    try:
        from sniper_bot.ai_advisor import check_auto_rollback

        rollback = check_auto_rollback(
            runtime.db, runtime.mode, runtime.config, current_equity,
            rollback_drop_pct=cfg.rollback_drop_pct,
            rollback_eval_cycles=cfg.rollback_eval_cycles,
        )
        if rollback:
            LOGGER.warning("auto_rollback_triggered", extra=rollback)
            _notify(runtime, "AI Auto-Rollback", [
                f"Equity dropped {rollback['drop_pct']:.1%} after tune at cycle {rollback['trigger_cycle']}",
                f"Equity: {rollback['equity_start']:.2f} → {rollback['equity_now']:.2f}",
                "Config restored to pre-tune snapshot.",
            ])
    except Exception:
        LOGGER.exception("auto_rollback_check_error")

    # Check if we should tune now (adaptive triggers + fixed interval)
    should_tune, trigger_reason = _should_tune_now(runtime, current_equity)
    if not should_tune:
        return

    api_key = get_optional_secret("OPENAI_API_KEY")
    if not api_key:
        LOGGER.warning("auto_tune_skipped_no_key")
        return

    # Skip if a previous tune is still running in the background
    if runtime._tune_thread is not None and runtime._tune_thread.is_alive():
        LOGGER.debug("auto_tune_skipped_already_running")
        return

    cycle_at_trigger = runtime.cycle_count
    runtime.last_tune_cycle = cycle_at_trigger

    def _run_tune() -> None:
        try:
            from sniper_bot.ai_advisor import auto_tune_cycle

            LOGGER.info("auto_tune_starting", extra={"cycle": cycle_at_trigger, "trigger": trigger_reason})
            with runtime._tune_lock:
                result = auto_tune_cycle(
                    runtime.db, runtime.mode, runtime.config, api_key,
                    cycle_at_trigger, current_equity=current_equity,
                )
            LOGGER.info("auto_tune_complete", extra={"result": result})

            if result.get("status") == "applied":
                applied = result.get("applied", {})
                changes_summary = "; ".join(f"{k}: {v['old']}→{v['new']}" for k, v in applied.items())
                _notify(runtime, "AI Auto-Tune Applied", [
                    f"Cycle: {cycle_at_trigger} (trigger: {trigger_reason})",
                    f"Confidence: {result.get('confidence', '?')}",
                    f"Changes: {changes_summary}",
                ])
            elif result.get("status") == "skipped":
                LOGGER.info("auto_tune_skipped", extra={"reason": result.get("reason")})
        except Exception:
            LOGGER.exception("auto_tune_error")

    runtime._tune_thread = threading.Thread(target=_run_tune, daemon=True, name="ai-tune")
    runtime._tune_thread.start()


def _notify(runtime: Runtime, title: str, lines: list[str]) -> None:
    if runtime.notifier:
        try:
            runtime.notifier.send_message(format_alert(title, lines))
        except Exception:
            LOGGER.warning("notification_failed", extra={"title": title})
