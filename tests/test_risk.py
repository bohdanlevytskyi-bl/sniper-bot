from datetime import datetime, timedelta, timezone
from types import SimpleNamespace

from sniper_bot.config import RiskConfig
from sniper_bot.risk import (
    check_drawdown_halt,
    check_portfolio_gates,
    position_size,
    record_closed_trade,
    reset_drawdown_state,
    sync_daily_state,
    update_equity_state,
)
from sniper_bot.storage import BotState, Position


def _state(**overrides) -> BotState:
    s = BotState(mode="paper")
    s.status = "IDLE"
    s.usdt_balance = 1000.0
    s.position_value = 0.0
    s.last_equity = 1000.0
    s.high_water_mark = 1000.0
    s.daily_start_equity = 1000.0
    s.daily_realized_pnl = 0.0
    s.daily_loss_date = None
    s.consecutive_losses = 0
    s.cooldown_until = None
    s.halted_at = None
    s.halt_reason = None
    for k, v in overrides.items():
        setattr(s, k, v)
    return s


def _position(**kw) -> Position:
    p = Position()
    p.mode = "paper"
    p.symbol = kw.get("symbol", "ETHUSDT")
    p.status = "open"
    p.usdt_invested = kw.get("usdt_invested", 100.0)
    p.quantity = kw.get("quantity", 0.033)
    p.entry_price = kw.get("entry_price", 3000.0)
    p.max_price = kw.get("max_price", 3000.0)
    p.stop_price = kw.get("stop_price", 2550.0)
    return p


def test_gates_allow_when_clear():
    state = _state()
    config = RiskConfig()
    now = datetime.now(timezone.utc)
    result = check_portfolio_gates(state, [], config, now)
    assert result.entry_allowed is True
    assert result.reason == "ok"


def test_gates_block_when_halted():
    state = _state(status="HALTED")
    config = RiskConfig()
    now = datetime.now(timezone.utc)
    result = check_portfolio_gates(state, [], config, now)
    assert result.entry_allowed is False
    assert result.reason == "halted"


def test_gates_block_max_positions():
    state = _state()
    config = RiskConfig(max_concurrent_positions=2)
    positions = [_position(symbol="A"), _position(symbol="B")]
    now = datetime.now(timezone.utc)
    result = check_portfolio_gates(state, positions, config, now)
    assert result.entry_allowed is False
    assert result.reason == "max_positions"


def test_gates_block_daily_loss():
    state = _state(daily_realized_pnl=-60.0, daily_start_equity=1000.0)
    config = RiskConfig(max_daily_loss_pct=0.05)
    now = datetime.now(timezone.utc)
    result = check_portfolio_gates(state, [], config, now)
    assert result.entry_allowed is False
    assert result.reason == "daily_loss_limit"


def test_gates_block_cooldown():
    now = datetime.now(timezone.utc)
    state = _state(cooldown_until=now + timedelta(hours=6))
    config = RiskConfig()
    result = check_portfolio_gates(state, [], config, now)
    assert result.entry_allowed is False
    assert result.reason == "cooldown"


def test_gates_block_max_exposure():
    state = _state(last_equity=1000.0)
    config = RiskConfig(max_portfolio_exposure_pct=0.40)
    positions = [_position(usdt_invested=450.0)]
    now = datetime.now(timezone.utc)
    result = check_portfolio_gates(state, positions, config, now)
    assert result.entry_allowed is False
    assert result.reason == "max_exposure"


def test_position_size_calculation():
    config = RiskConfig(max_position_pct=0.10)
    qty = position_size(config, 1000.0, 50.0, 500.0)
    assert qty == 2.0  # 10% of 1000 = 100 USDT / 50 price = 2.0


def test_position_size_limited_by_cash():
    config = RiskConfig(max_position_pct=0.50)
    qty = position_size(config, 1000.0, 50.0, 100.0)
    assert qty == 2.0  # min(500, 100) = 100 USDT / 50 = 2.0


def test_drawdown_halt_detection():
    state = _state(last_equity=800.0, high_water_mark=1000.0)
    config = RiskConfig(max_drawdown_pct=0.15)
    assert check_drawdown_halt(state, config) is True


def test_drawdown_no_halt():
    state = _state(last_equity=900.0, high_water_mark=1000.0)
    config = RiskConfig(max_drawdown_pct=0.15)
    assert check_drawdown_halt(state, config) is False


def test_record_loss_triggers_cooldown():
    state = _state(consecutive_losses=2)
    config = RiskConfig(cooldown_losses=3)
    now = datetime.now(timezone.utc)
    started = record_closed_trade(state, config, now, -50.0)
    assert started is True
    assert state.cooldown_until is not None


def test_record_win_resets_losses():
    state = _state(consecutive_losses=2)
    config = RiskConfig()
    now = datetime.now(timezone.utc)
    record_closed_trade(state, config, now, 50.0)
    assert state.consecutive_losses == 0


def test_sync_daily_state_resets_on_new_day():
    state = _state(daily_loss_date="2026-03-23", daily_realized_pnl=-50.0)
    now = datetime(2026, 3, 24, 12, 0, tzinfo=timezone.utc)
    sync_daily_state(state, now, 950.0)
    assert state.daily_loss_date == "2026-03-24"
    assert state.daily_realized_pnl == 0.0
    assert state.daily_start_equity == 950.0


def test_update_equity_tracks_hwm():
    state = _state(high_water_mark=1000.0)
    dd = update_equity_state(state, 1050.0)
    assert state.high_water_mark == 1050.0
    assert dd == 0.0


def test_reset_drawdown():
    state = _state(status="HALTED", halt_reason="max_drawdown", last_equity=850.0)
    reset_drawdown_state(state)
    assert state.status == "IDLE"
    assert state.halt_reason is None
    assert state.high_water_mark == 850.0
