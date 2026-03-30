from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timedelta, timezone

from sniper_bot.config import RiskConfig
from sniper_bot.storage import BotState, Position


@dataclass(slots=True)
class RiskCheck:
    entry_allowed: bool
    reason: str
    drawdown_pct: float
    daily_loss_pct: float


def check_portfolio_gates(
    state: BotState,
    open_positions: list[Position],
    config: RiskConfig,
    now: datetime,
) -> RiskCheck:
    """Evaluate all risk gates. Returns whether a new entry is allowed."""
    equity = state.last_equity or state.usdt_balance
    drawdown_pct = 0.0
    if state.high_water_mark and state.high_water_mark > 0:
        drawdown_pct = (state.high_water_mark - equity) / state.high_water_mark

    daily_loss_pct = 0.0
    if state.daily_start_equity and state.daily_start_equity > 0:
        daily_loss_pct = -state.daily_realized_pnl / state.daily_start_equity

    # Halted
    if state.status == "HALTED":
        return RiskCheck(False, "halted", drawdown_pct, daily_loss_pct)

    # Drawdown halt check
    if drawdown_pct >= config.max_drawdown_pct:
        return RiskCheck(False, "max_drawdown", drawdown_pct, daily_loss_pct)

    # Daily loss limit
    if daily_loss_pct >= config.max_daily_loss_pct:
        return RiskCheck(False, "daily_loss_limit", drawdown_pct, daily_loss_pct)

    # Cooldown
    cooldown_until = state.cooldown_until
    if cooldown_until and cooldown_until.tzinfo is None:
        cooldown_until = cooldown_until.replace(tzinfo=timezone.utc)
    if cooldown_until and now < cooldown_until:
        return RiskCheck(False, "cooldown", drawdown_pct, daily_loss_pct)

    # Max concurrent positions
    if len(open_positions) >= config.max_concurrent_positions:
        return RiskCheck(False, "max_positions", drawdown_pct, daily_loss_pct)

    # Max portfolio exposure
    if equity > 0:
        total_invested = sum(p.usdt_invested for p in open_positions)
        if total_invested / equity >= config.max_portfolio_exposure_pct:
            return RiskCheck(False, "max_exposure", drawdown_pct, daily_loss_pct)

    return RiskCheck(True, "ok", drawdown_pct, daily_loss_pct)


def position_size(config: RiskConfig, equity: float, price: float, available_cash: float, signal_score: float = 1.0) -> float:
    """Calculate how much base asset to buy, scaled proportionally to signal strength."""
    # Score ≥ min_score_for_full_size → 100% of max; score at 0 → 50% of max
    full_threshold = config.min_score_for_full_size
    size_scale = min(1.0, max(0.5, 0.5 + 0.5 * (signal_score / full_threshold if full_threshold > 0 else 1.0)))
    max_usdt = min(config.max_position_pct * equity * size_scale, available_cash)
    if price <= 0:
        return 0.0
    return max_usdt / price


def check_market_regime(
    config: RiskConfig,
    btc_change_1h: float,
    market_breadth_pct: float | None,
) -> tuple[bool, str]:
    """Returns (entry_allowed, reason). Blocks entries in bear market conditions."""
    if not config.regime_gate_enabled:
        return True, "regime_gate_disabled"

    if btc_change_1h <= config.regime_bear_btc_change_pct:
        return False, f"bear_regime_btc_{btc_change_1h:.2%}"

    if market_breadth_pct is not None and market_breadth_pct <= config.regime_bear_breadth_pct:
        return False, f"bear_regime_breadth_{market_breadth_pct:.2%}"

    return True, "ok"


def sync_daily_state(state: BotState, now: datetime, equity: float) -> None:
    """Reset daily counters if a new day has started."""
    today = now.strftime("%Y-%m-%d")
    if state.daily_loss_date != today:
        state.daily_loss_date = today
        state.daily_start_equity = equity
        state.daily_realized_pnl = 0.0


def update_equity_state(state: BotState, equity: float) -> float:
    """Update high water mark and return current drawdown percentage."""
    state.last_equity = equity
    if state.high_water_mark is None or equity > state.high_water_mark:
        state.high_water_mark = equity
    hwm = state.high_water_mark or equity
    return (hwm - equity) / hwm if hwm > 0 else 0.0


def check_drawdown_halt(state: BotState, config: RiskConfig) -> bool:
    """Returns True if drawdown exceeds threshold."""
    equity = state.last_equity or state.usdt_balance
    hwm = state.high_water_mark or equity
    if hwm <= 0:
        return False
    return (hwm - equity) / hwm >= config.max_drawdown_pct


def record_closed_trade(state: BotState, config: RiskConfig, now: datetime, pnl: float) -> bool:
    """Update state after a trade closes. Returns True if cooldown started."""
    state.daily_realized_pnl += pnl

    if pnl < 0:
        state.consecutive_losses += 1
    else:
        state.consecutive_losses = 0

    if state.consecutive_losses >= config.cooldown_losses:
        state.cooldown_until = now + timedelta(hours=config.cooldown_hours)
        return True
    return False


def reset_drawdown_state(state: BotState) -> None:
    """Manually reset drawdown halt."""
    state.status = "IDLE"
    state.halted_at = None
    state.halt_reason = None
    equity = state.last_equity or state.usdt_balance
    state.high_water_mark = equity
