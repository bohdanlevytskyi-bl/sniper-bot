from __future__ import annotations

import json
import time
from dataclasses import dataclass
from datetime import date, datetime, timedelta
from pathlib import Path
from types import SimpleNamespace
from typing import Any
from zoneinfo import ZoneInfo

from sqlalchemy import select

from sniper_bot.ai import OpenAIObserver
from sniper_bot.alerts import TelegramNotifier, format_alert
from sniper_bot.config import AppConfig, get_optional_secret, get_required_secret, load_config, resolve_runtime_path
from sniper_bot.data import fetch_recent_closed_candles, latest_closed_candle, load_backtest_frame, now_utc
from sniper_bot.execution import (
    BybitBroker,
    PaperBroker,
    apply_slippage,
    close_position_from_fill,
    open_position_from_fill,
)
from sniper_bot.exchange import BybitClient
from sniper_bot.logging_utils import configure_logging, get_logger
from sniper_bot.reporting import build_summary_payload, compute_backtest_metrics, data_frame_to_candle_payload, summary_due
from sniper_bot.risk import (
    check_drawdown_halt,
    evaluate_risk_gates,
    position_size_for_entry,
    record_closed_trade,
    reset_drawdown_state,
    sync_daily_state,
    update_equity_state,
)
from sniper_bot.storage import (
    AIObservation,
    DailySummary,
    Database,
    RiskEvent,
    close_run,
    create_run,
    get_open_position,
    get_or_create_state,
    latest_summary,
    load_candles_frame,
    record_ai_observation,
    record_equity_snapshot,
    record_risk_event,
    record_signal,
    upsert_candles,
    upsert_daily_summary,
)
from sniper_bot.strategy import PositionSnapshot, StrategyAction, build_indicator_frame, evaluate_strategy


LOGGER = get_logger(__name__)


@dataclass(slots=True)
class Runtime:
    config: AppConfig
    config_path: Path
    mode: str
    db: Database
    bybit: BybitClient
    instrument: Any
    notifier: TelegramNotifier | None
    ai: OpenAIObserver | None
    timezone: ZoneInfo | Any

    def close(self) -> None:
        self.bybit.close()
        if self.notifier:
            self.notifier.close()
        if self.ai:
            self.ai.close()


def create_runtime(config_path: Path, mode: str | None = None, require_private: bool = False) -> Runtime:
    config = load_config(config_path)
    active_mode = mode or config.mode
    base_dir = config_path.parent.resolve()
    log_dir = resolve_runtime_path(base_dir, config.paths.log_dir)
    configure_logging(log_dir)
    db_path = resolve_runtime_path(base_dir, config.database_path_for_mode(active_mode))
    db = Database(db_path)
    db.upgrade()

    api_key = get_optional_secret("BYBIT_API_KEY") if not require_private else get_required_secret("BYBIT_API_KEY")
    api_secret = get_optional_secret("BYBIT_API_SECRET") if not require_private else get_required_secret("BYBIT_API_SECRET")
    bybit = BybitClient(config.exchange, api_key=api_key, api_secret=api_secret)
    instrument = bybit.resolve_instrument(config.pair)

    notifier = None
    if config.alerts.enabled:
        token = get_optional_secret("TELEGRAM_BOT_TOKEN")
        chat_id = get_optional_secret("TELEGRAM_CHAT_ID")
        if token and chat_id:
            notifier = TelegramNotifier(token, chat_id)
        elif require_private:
            raise RuntimeError("Telegram alerts are enabled but TELEGRAM_BOT_TOKEN or TELEGRAM_CHAT_ID is missing")

    ai = None
    if config.ai.enabled:
        api_token = get_optional_secret("OPENAI_API_KEY")
        if api_token:
            ai = OpenAIObserver(config.ai, api_token)
        elif require_private:
            raise RuntimeError("AI is enabled but OPENAI_API_KEY is missing")

    return Runtime(
        config=config,
        config_path=config_path,
        mode=active_mode,
        db=db,
        bybit=bybit,
        instrument=instrument,
        notifier=notifier,
        ai=ai,
        timezone=_runtime_timezone(config),
    )


def backfill(config_path: Path, limit: int) -> dict[str, Any]:
    runtime = create_runtime(config_path, require_private=False)
    try:
        candles = fetch_recent_closed_candles(
            runtime.bybit,
            runtime.instrument,
            runtime.config.timeframe_minutes,
            limit=min(limit, runtime.config.exchange.max_public_candles),
        )
        with runtime.db.session() as session:
            inserted = upsert_candles(session, runtime.config.pair, runtime.config.timeframe_minutes, candles)
        return {"inserted": inserted, "fetched": len(candles)}
    finally:
        runtime.close()


def backtest(config_path: Path, csv_path: Path | None = None) -> dict[str, Any]:
    runtime = create_runtime(config_path, mode="paper", require_private=False)
    try:
        with runtime.db.session() as session:
            db_frame = load_candles_frame(session, runtime.config.pair, runtime.config.timeframe_minutes, limit=10_000)
        frame = load_backtest_frame(csv_path, db_frame)
        metrics = _run_backtest(runtime.config, frame)
        return {
            "net_return_pct": metrics.net_return_pct,
            "max_drawdown_pct": metrics.max_drawdown_pct,
            "trade_count": metrics.trade_count,
            "win_rate_pct": metrics.win_rate_pct,
            "average_trade_pct": metrics.average_trade_pct,
            "expectancy": metrics.expectancy,
        }
    finally:
        runtime.close()


def run_bot(config_path: Path, mode: str, once: bool = False, confirm_live: bool = False) -> None:
    config = load_config(config_path)
    _validate_run_request(config, mode, confirm_live)

    runtime = create_runtime(config_path, mode=mode, require_private=(mode in {"demo", "live"}))
    run_id: int | None = None
    try:
        with runtime.db.session() as session:
            run = create_run(session, runtime.mode, str(runtime.config_path))
            run_id = run.id
        _notify(runtime, "Bot Started", [f"Mode: {mode}", f"Pair: {runtime.config.pair}", "Run loop online"])
        while True:
            result = _process_once(runtime)
            LOGGER.info("run_iteration", extra={"context": result})
            if once:
                break
            time.sleep(runtime.config.execution.poll_interval_seconds)
    except KeyboardInterrupt:
        _notify(runtime, "Bot Stopped", [f"Mode: {mode}", "Stopped by operator"])
    except Exception as exc:
        _notify(runtime, "Bot Error", [f"Mode: {mode}", str(exc)])
        raise
    finally:
        if run_id is not None:
            with runtime.db.session() as session:
                close_run(session, run_id, "stopped")
        runtime.close()


def status(config_path: Path, mode: str) -> dict[str, Any]:
    runtime = create_runtime(config_path, mode=mode, require_private=False)
    try:
        with runtime.db.session() as session:
            state = get_or_create_state(session, mode)
            position = get_open_position(session, mode, runtime.config.pair)
            summary = latest_summary(session, mode)
            return {
                "mode": mode,
                "status": state.status,
                "equity": state.last_equity,
                "high_water_mark": state.high_water_mark,
                "quote_balance": state.quote_balance,
                "asset_balance": state.asset_balance,
                "cooldown_until": state.cooldown_until.isoformat() if state.cooldown_until else None,
                "halt_reason": state.halt_reason,
                "last_processed_candle_at": state.last_processed_candle_at.isoformat() if state.last_processed_candle_at else None,
                "open_position": {
                    "quantity": position.quantity,
                    "entry_price": position.entry_price,
                    "stop_price": position.stop_price,
                    "entry_time": position.entry_time.isoformat(),
                }
                if position
                else None,
                "latest_summary_date": summary.summary_date.isoformat() if summary else None,
            }
    finally:
        runtime.close()


def reset_drawdown(config_path: Path, mode: str) -> dict[str, Any]:
    runtime = create_runtime(config_path, mode=mode, require_private=False)
    try:
        with runtime.db.session() as session:
            state = get_or_create_state(session, mode)
            reset_drawdown_state(state, datetime.now(tz=runtime.timezone))
        _notify(runtime, "Drawdown Reset", [f"Mode: {mode}", "Drawdown halt cleared by operator"])
        return {"status": "reset"}
    finally:
        runtime.close()


def send_summary(config_path: Path, mode: str, summary_date: date | None = None) -> dict[str, Any]:
    runtime = create_runtime(config_path, mode=mode, require_private=False)
    try:
        with runtime.db.session() as session:
            state = get_or_create_state(session, mode)
            payload = _build_summary_payload(session, runtime, state)
            target_date = summary_date or (datetime.now(tz=runtime.timezone).date() - timedelta(days=1))
            summary = _generate_summary(runtime, session, mode, target_date, payload)
            return {"summary_date": summary.summary_date.isoformat(), "text": summary.text}
    finally:
        runtime.close()


def healthcheck(config_path: Path) -> dict[str, Any]:
    runtime = create_runtime(config_path, require_private=False)
    report: dict[str, Any] = {"config": "ok"}
    try:
        with runtime.db.session() as session:
            get_or_create_state(session, runtime.mode)
            report["database"] = "ok"

        ticker = runtime.bybit.get_ticker(runtime.instrument)
        report["bybit_public"] = {"status": "ok", "last": ticker.last}

        if get_optional_secret("BYBIT_API_KEY") and get_optional_secret("BYBIT_API_SECRET"):
            runtime.bybit.get_balance([runtime.instrument.base_asset, runtime.instrument.quote_asset])
            report["bybit_private"] = "ok"
        else:
            report["bybit_private"] = "skipped_missing_secrets"

        if runtime.notifier:
            runtime.notifier.send_message(format_alert("Healthcheck", ["Telegram delivery OK"]))
            report["telegram"] = "ok"
        else:
            report["telegram"] = "skipped_missing_secrets"

        if runtime.ai:
            report["openai"] = runtime.ai.healthcheck()
        else:
            report["openai"] = "skipped_missing_secret"
        return report
    finally:
        runtime.close()


def _process_once(runtime: Runtime) -> dict[str, Any]:
    with runtime.db.session() as session:
        state = get_or_create_state(session, runtime.mode)
        now_local = datetime.now(tz=runtime.timezone)

        paper_broker = PaperBroker(runtime.config)
        if runtime.mode == "paper":
            paper_broker.sync_initial_balances(state)

        candles = fetch_recent_closed_candles(runtime.bybit, runtime.instrument, runtime.config.timeframe_minutes, 600)
        upsert_candles(session, runtime.config.pair, runtime.config.timeframe_minutes, candles)
        latest = latest_closed_candle(candles)
        if latest is None:
            return {"status": "no_candles"}
        if state.last_processed_candle_at and latest["open_time"] <= state.last_processed_candle_at:
            _maybe_send_summary(session, runtime, state, now_local)
            return {"status": "no_new_bar", "latest_open_time": latest["open_time"].isoformat()}

        quote = runtime.bybit.get_ticker(runtime.instrument)
        if runtime.mode in {"demo", "live"}:
            broker = BybitBroker(runtime.config, runtime.bybit, runtime.instrument)
            broker.sync_balances(state)
        else:
            broker = paper_broker

        equity = state.quote_balance + (state.asset_balance * quote.last)
        sync_daily_state(state, now_local, equity)
        drawdown_pct = update_equity_state(state, now_local, equity)
        record_equity_snapshot(
            session,
            runtime.mode,
            now_utc(),
            equity,
            state.quote_balance,
            state.asset_balance,
            state.high_water_mark or equity,
            drawdown_pct,
        )

        open_position = get_open_position(session, runtime.mode, runtime.config.pair)
        if check_drawdown_halt(state, runtime.config.risk):
            state.status = "HALTED"
            state.halted_at = now_utc()
            state.halt_reason = "max_drawdown"
            if open_position is not None:
                result = (
                    broker.execute_market_order(session, runtime.mode, runtime.config.pair, state, "sell", open_position.quantity, quote)
                    if runtime.mode == "paper"
                    else broker.execute_market_order(session, runtime.mode, runtime.config.pair, state, "sell", open_position.quantity)
                )
                pnl = close_position_from_fill(session, open_position, result)
                record_closed_trade(state, runtime.config.risk, now_local, pnl)
            record_risk_event(
                session,
                runtime.mode,
                "drawdown_halt",
                "Trading halted because max drawdown reached 8%",
                {"equity": equity, "drawdown_pct": drawdown_pct},
                severity="critical",
            )
            _notify(runtime, "Drawdown Halt", [f"Mode: {runtime.mode}", f"Equity: {equity:.2f}", f"Drawdown: {drawdown_pct:.2%}"])
            state.last_processed_candle_at = latest["open_time"]
            return {"status": "halted", "drawdown_pct": drawdown_pct}

        risk_gate = evaluate_risk_gates(state, runtime.config.risk, now_local)
        frame = load_candles_frame(session, runtime.config.pair, runtime.config.timeframe_minutes, limit=600)
        indicators = build_indicator_frame(frame, runtime.config.strategy)
        position_snapshot = (
            PositionSnapshot(
                quantity=open_position.quantity,
                entry_price=open_position.entry_price,
                stop_price=open_position.stop_price,
                max_price=open_position.max_price,
            )
            if open_position
            else None
        )
        decision = evaluate_strategy(indicators, runtime.config.strategy, position_snapshot, risk_gate.entry_allowed)
        record_signal(session, runtime.mode, runtime.config.pair, runtime.config.timeframe_minutes, latest["open_time"], decision)

        if runtime.ai:
            latest_regime = runtime.ai.classify_regime(
                runtime.config.pair,
                data_frame_to_candle_payload(indicators, runtime.config.ai.regime_lookback_bars),
                {
                    "ema_fast": decision.ema_fast,
                    "ema_slow": decision.ema_slow,
                    "atr": decision.atr,
                    "close_price": decision.close_price,
                },
                {
                    "entry_allowed": risk_gate.entry_allowed,
                    "reason": risk_gate.reason,
                    "drawdown_pct": risk_gate.drawdown_pct,
                    "daily_loss_pct": risk_gate.daily_loss_pct,
                },
            )
            record_ai_observation(
                session,
                runtime.mode,
                "regime",
                runtime.config.ai.model,
                latest["open_time"],
                latest_regime.get("label"),
                latest_regime.get("confidence"),
                latest_regime.get("rationale"),
                latest_regime.get("risk_notes"),
                latest_regime,
            )

        if open_position and decision.next_stop is not None and decision.action == StrategyAction.HOLD:
            open_position.stop_price = decision.next_stop
            open_position.max_price = max(open_position.max_price, decision.close_price)

        action_result: dict[str, Any] = {"signal": decision.action.value}
        if decision.action == StrategyAction.ENTER and open_position is None and risk_gate.entry_allowed:
            quantity = position_size_for_entry(runtime.config.risk, equity, quote.ask, state.quote_balance)
            if quantity > 0:
                result = (
                    broker.execute_market_order(session, runtime.mode, runtime.config.pair, state, "buy", quantity, quote)
                    if runtime.mode == "paper"
                    else broker.execute_market_order(session, runtime.mode, runtime.config.pair, state, "buy", quantity)
                )
                open_position_from_fill(session, runtime.mode, runtime.config.pair, result, decision.next_stop or decision.close_price)
                _notify(
                    runtime,
                    "Trade Opened",
                    [f"Mode: {runtime.mode}", f"Qty: {result.quantity:.6f}", f"Price: {result.average_price:.2f}", f"Reason: {decision.reason}"],
                )
                action_result["entered"] = result.quantity
        elif decision.action == StrategyAction.EXIT and open_position is not None:
            result = (
                broker.execute_market_order(session, runtime.mode, runtime.config.pair, state, "sell", open_position.quantity, quote)
                if runtime.mode == "paper"
                else broker.execute_market_order(session, runtime.mode, runtime.config.pair, state, "sell", open_position.quantity)
            )
            pnl = close_position_from_fill(session, open_position, result)
            cooldown_started = record_closed_trade(state, runtime.config.risk, now_local, pnl)
            _notify(
                runtime,
                "Trade Closed",
                [f"Mode: {runtime.mode}", f"PnL: {pnl:.2f}", f"Price: {result.average_price:.2f}", f"Reason: {decision.reason}"],
            )
            if cooldown_started:
                record_risk_event(
                    session,
                    runtime.mode,
                    "cooldown_started",
                    "Cooldown started after consecutive losses",
                    {"cooldown_until": state.cooldown_until.isoformat() if state.cooldown_until else None},
                    severity="warning",
                )
                _notify(runtime, "Cooldown Started", [f"Mode: {runtime.mode}", f"Until: {state.cooldown_until}"])
            action_result["closed"] = pnl

        if not risk_gate.entry_allowed and risk_gate.reason in {"daily_loss_limit_hit", "cooldown_active"}:
            record_risk_event(
                session,
                runtime.mode,
                risk_gate.reason,
                f"Risk gate active: {risk_gate.reason}",
                {"drawdown_pct": risk_gate.drawdown_pct, "daily_loss_pct": risk_gate.daily_loss_pct},
                severity="warning",
            )

        state.last_processed_candle_at = latest["open_time"]
        _maybe_send_summary(session, runtime, state, now_local)
        return {
            "status": "processed",
            "latest_open_time": latest["open_time"].isoformat(),
            "equity": equity,
            "drawdown_pct": drawdown_pct,
            **action_result,
        }


def _maybe_send_summary(session: Any, runtime: Runtime, state: object, now_local: datetime) -> None:
    if not runtime.ai:
        return
    if not summary_due(now_local, runtime.config.ai.summary_hour, runtime.config.ai.summary_minute, state.last_summary_date):
        return
    target_date = (now_local - timedelta(days=1)).date()
    payload = _build_summary_payload(session, runtime, state)
    summary = _generate_summary(runtime, session, runtime.mode, target_date, payload)
    state.last_summary_date = summary.summary_date


def _build_summary_payload(session: Any, runtime: Runtime, state: object) -> dict[str, Any]:
    latest_regime = session.scalar(
        select(AIObservation)
        .where(AIObservation.mode == runtime.mode, AIObservation.kind == "regime")
        .order_by(AIObservation.observed_at.desc())
        .limit(1)
    )
    recent_risk_events = session.scalars(
        select(RiskEvent)
        .where(RiskEvent.mode == runtime.mode)
        .order_by(RiskEvent.created_at.desc())
        .limit(5)
    ).all()
    return build_summary_payload(
        runtime.mode,
        runtime.config.pair,
        state,
        {
            "label": latest_regime.label,
            "confidence": latest_regime.confidence,
            "rationale": latest_regime.rationale,
            "risk_notes": latest_regime.risk_notes,
        }
        if latest_regime
        else None,
        recent_risk_events,
    )


def _generate_summary(runtime: Runtime, session: Any, mode: str, summary_date: date, payload: dict[str, Any]) -> DailySummary:
    if runtime.ai:
        summary_payload = runtime.ai.generate_daily_summary(summary_date, payload)
    else:
        summary_payload = {
            "summary_text": json.dumps(payload, ensure_ascii=True),
            "regime_recap": "AI disabled",
            "pnl_recap": f"Equity {payload.get('equity')}",
            "notable_risks": payload.get("halt_reason") or "none",
        }
    summary = upsert_daily_summary(
        session,
        mode,
        summary_date,
        summary_payload["summary_text"],
        summary_payload["regime_recap"],
        summary_payload["pnl_recap"],
        summary_payload["notable_risks"],
    )
    summary.sent_at = now_utc()
    if runtime.notifier:
        runtime.notifier.send_message(
            format_alert(
                "Daily Summary",
                [
                    f"Date: {summary_date.isoformat()}",
                    summary.text,
                    f"Regime: {summary.regime_recap}",
                    f"PnL: {summary.pnl_recap}",
                    f"Risks: {summary.notable_risks}",
                ],
            )
        )
    return summary


def _notify(runtime: Runtime, title: str, lines: list[str]) -> None:
    if runtime.notifier:
        runtime.notifier.send_message(format_alert(title, lines))


def _runtime_timezone(config: AppConfig) -> ZoneInfo | Any:
    if config.timezone == "local":
        return datetime.now().astimezone().tzinfo
    return ZoneInfo(config.timezone)


def _validate_run_request(config: AppConfig, mode: str, confirm_live: bool) -> None:
    if mode == "live" and not confirm_live:
        raise RuntimeError("Live mode requires --confirm-live")
    if config.mode != mode:
        raise RuntimeError(f"{mode.capitalize()} mode requires mode: {mode} in the config file")
    if mode == "demo" and config.exchange.environment != "demo":
        raise RuntimeError("Demo mode requires exchange.environment: demo")
    if mode == "live" and config.exchange.environment != "live":
        raise RuntimeError("Live mode requires exchange.environment: live")


def _run_backtest(config: AppConfig, frame: Any) -> Any:
    if frame.empty:
        raise RuntimeError("No candles available for backtest")
    indicators = build_indicator_frame(frame, config.strategy)
    state = SimpleNamespace(
        status="IDLE",
        high_water_mark=config.backtest.starting_cash,
        last_equity=config.backtest.starting_cash,
        daily_start_equity=config.backtest.starting_cash,
        daily_realized_pnl=0.0,
        daily_loss_date=None,
        consecutive_losses=0,
        cooldown_until=None,
        quote_balance=config.backtest.starting_cash,
        asset_balance=0.0,
        halted_at=None,
        halt_reason=None,
    )
    open_position: dict[str, float] | None = None
    equity_curve = [config.backtest.starting_cash]
    trade_pnls: list[float] = []

    for index in range(max(config.strategy.ema_slow, config.strategy.atr_period) + config.strategy.slope_lookback_bars, len(indicators) - 1):
        now_local = indicators.iloc[index]["open_time"].to_pydatetime()
        close_price = float(indicators.iloc[index]["close"])
        equity = state.quote_balance + (state.asset_balance * close_price)
        sync_daily_state(state, now_local, equity)
        update_equity_state(state, now_local, equity)
        if check_drawdown_halt(state, config.risk):
            state.status = "HALTED"
            state.halt_reason = "max_drawdown"
            break
        risk_gate = evaluate_risk_gates(state, config.risk, now_local)
        position_snapshot = (
            PositionSnapshot(
                quantity=open_position["quantity"],
                entry_price=open_position["entry_price"],
                stop_price=open_position["stop_price"],
                max_price=open_position["max_price"],
            )
            if open_position
            else None
        )
        decision = evaluate_strategy(indicators.iloc[: index + 1], config.strategy, position_snapshot, risk_gate.entry_allowed)
        next_open = float(indicators.iloc[index + 1]["open"])
        if open_position and decision.next_stop is not None and decision.action == StrategyAction.HOLD:
            open_position["stop_price"] = decision.next_stop
            open_position["max_price"] = max(open_position["max_price"], close_price)
        if decision.action == StrategyAction.ENTER and open_position is None and risk_gate.entry_allowed:
            quantity = position_size_for_entry(config.risk, equity, next_open, state.quote_balance)
            if quantity > 0:
                fill_price = apply_slippage(next_open, "buy", config.backtest.slippage_bps)
                cost = fill_price * quantity
                fee = cost * config.backtest.fee_rate
                state.quote_balance -= cost + fee
                state.asset_balance += quantity
                open_position = {
                    "quantity": quantity,
                    "entry_price": fill_price,
                    "stop_price": decision.next_stop or fill_price,
                    "max_price": fill_price,
                    "entry_fee": fee,
                }
        elif decision.action == StrategyAction.EXIT and open_position is not None:
            fill_price = apply_slippage(next_open, "sell", config.backtest.slippage_bps)
            gross = fill_price * open_position["quantity"]
            fee = gross * config.backtest.fee_rate
            pnl = (gross - fee) - ((open_position["entry_price"] * open_position["quantity"]) + open_position["entry_fee"])
            state.quote_balance += gross - fee
            state.asset_balance = 0.0
            trade_pnls.append(pnl)
            record_closed_trade(state, config.risk, now_local, pnl)
            open_position = None
        equity_curve.append(state.quote_balance + (state.asset_balance * close_price))

    return compute_backtest_metrics(equity_curve, trade_pnls, config.backtest.starting_cash)
