from __future__ import annotations

from datetime import datetime, timedelta, timezone
from types import SimpleNamespace

from sniper_bot.config import RiskConfig
from sniper_bot.risk import (
    check_drawdown_halt,
    evaluate_risk_gates,
    record_closed_trade,
    reset_drawdown_state,
    sync_daily_state,
    update_equity_state,
)


def _state() -> SimpleNamespace:
    return SimpleNamespace(
        status="IDLE",
        high_water_mark=10_000.0,
        last_equity=10_000.0,
        daily_start_equity=10_000.0,
        daily_realized_pnl=0.0,
        daily_loss_date=None,
        consecutive_losses=0,
        cooldown_until=None,
        halted_at=None,
        halt_reason=None,
        updated_at=None,
    )


def test_daily_loss_gate_blocks_new_entries() -> None:
    state = _state()
    now = datetime(2026, 3, 17, tzinfo=timezone.utc)
    sync_daily_state(state, now, 10_000.0)
    state.daily_realized_pnl = -250.0
    risk = evaluate_risk_gates(state, RiskConfig(max_daily_loss_pct=0.02), now)
    assert not risk.entry_allowed
    assert risk.reason == "daily_loss_limit_hit"


def test_cooldown_starts_after_two_losses() -> None:
    state = _state()
    config = RiskConfig(cooldown_losses=2, cooldown_hours=24)
    now = datetime(2026, 3, 17, tzinfo=timezone.utc)
    assert not record_closed_trade(state, config, now, -10.0)
    assert record_closed_trade(state, config, now, -5.0)
    assert state.cooldown_until == now + timedelta(hours=24)


def test_drawdown_halt_and_reset() -> None:
    state = _state()
    config = RiskConfig(max_drawdown_pct=0.08)
    now = datetime(2026, 3, 17, tzinfo=timezone.utc)
    update_equity_state(state, now, 9_100.0)
    assert check_drawdown_halt(state, config)
    state.status = "HALTED"
    reset_drawdown_state(state, now)
    assert state.status == "IDLE"
    assert state.high_water_mark == state.last_equity
