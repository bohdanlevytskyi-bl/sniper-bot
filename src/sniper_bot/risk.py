from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timedelta

from sniper_bot.config import RiskConfig


@dataclass(slots=True)
class RiskCheck:
    entry_allowed: bool
    reason: str | None
    drawdown_pct: float
    daily_loss_pct: float


def sync_daily_state(state: object, now: datetime, current_equity: float) -> None:
    if getattr(state, "daily_loss_date", None) != now.date():
        state.daily_loss_date = now.date()
        state.daily_realized_pnl = 0.0
        state.daily_start_equity = current_equity


def update_equity_state(state: object, now: datetime, equity: float) -> float:
    state.last_equity = equity
    if state.high_water_mark is None or equity > state.high_water_mark:
        state.high_water_mark = equity
    state.updated_at = now
    if not state.high_water_mark:
        return 0.0
    return max(0.0, (state.high_water_mark - equity) / state.high_water_mark)


def evaluate_risk_gates(state: object, config: RiskConfig, now: datetime) -> RiskCheck:
    daily_start = state.daily_start_equity or max(state.last_equity or 0.0, 1.0)
    daily_loss_pct = 0.0
    if daily_start > 0:
        daily_loss_pct = max(0.0, (-state.daily_realized_pnl) / daily_start)

    drawdown_pct = 0.0
    if state.high_water_mark:
        drawdown_pct = max(0.0, (state.high_water_mark - (state.last_equity or 0.0)) / state.high_water_mark)

    if state.status == "HALTED":
        return RiskCheck(False, "drawdown_halted", drawdown_pct, daily_loss_pct)
    if state.cooldown_until and state.cooldown_until > now:
        return RiskCheck(False, "cooldown_active", drawdown_pct, daily_loss_pct)
    if daily_loss_pct >= config.max_daily_loss_pct:
        return RiskCheck(False, "daily_loss_limit_hit", drawdown_pct, daily_loss_pct)
    return RiskCheck(True, None, drawdown_pct, daily_loss_pct)


def check_drawdown_halt(state: object, config: RiskConfig) -> bool:
    if not state.high_water_mark or state.last_equity is None:
        return False
    drawdown_pct = (state.high_water_mark - state.last_equity) / state.high_water_mark
    return drawdown_pct >= config.max_drawdown_pct


def position_size_for_entry(
    config: RiskConfig,
    equity: float,
    price: float,
    cash_balance: float,
) -> float:
    max_notional = equity * config.max_position_pct
    affordable_notional = min(max_notional, cash_balance)
    if price <= 0 or affordable_notional <= 0:
        return 0.0
    return affordable_notional / price


def record_closed_trade(state: object, config: RiskConfig, now: datetime, pnl: float) -> bool:
    state.daily_realized_pnl = (state.daily_realized_pnl or 0.0) + pnl
    if pnl < 0:
        state.consecutive_losses = (state.consecutive_losses or 0) + 1
    else:
        state.consecutive_losses = 0
        state.cooldown_until = None
        return False

    if state.consecutive_losses >= config.cooldown_losses:
        state.cooldown_until = now + timedelta(hours=config.cooldown_hours)
        return True
    return False


def reset_drawdown_state(state: object, now: datetime) -> None:
    state.status = "IDLE"
    state.halt_reason = None
    state.halted_at = None
    state.high_water_mark = state.last_equity
    state.updated_at = now
