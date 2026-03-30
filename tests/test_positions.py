from datetime import datetime, timedelta, timezone

import pytest

from sniper_bot.config import PositionConfig
from sniper_bot.positions import evaluate_position
from sniper_bot.storage import Position


def _pos(entry_price=100.0, max_price=100.0, stop_price=85.0, hours_ago=0) -> Position:
    p = Position()
    p.entry_price = entry_price
    p.quantity = 1.0
    p.max_price = max_price
    p.stop_price = stop_price
    p.entry_time = datetime.now(timezone.utc) - timedelta(hours=hours_ago)
    return p


def test_hold_when_price_normal():
    config = PositionConfig()
    pos = _pos(entry_price=100.0, max_price=110.0, stop_price=93.5)
    result = evaluate_position(pos, 105.0, config, datetime.now(timezone.utc))
    assert result is None


def test_trailing_stop_triggered():
    config = PositionConfig(trailing_stop_pct=0.15)
    pos = _pos(entry_price=100.0, max_price=120.0, stop_price=102.0)
    result = evaluate_position(pos, 101.0, config, datetime.now(timezone.utc))
    assert result == "trailing_stop"


def test_hard_stop_triggered():
    config = PositionConfig(hard_stop_pct=0.25)
    pos = _pos(entry_price=100.0)
    result = evaluate_position(pos, 74.0, config, datetime.now(timezone.utc))
    assert result == "hard_stop"


def test_take_profit_triggered():
    config = PositionConfig(take_profit_multiple=2.0)
    pos = _pos(entry_price=100.0)
    result = evaluate_position(pos, 200.0, config, datetime.now(timezone.utc))
    assert result == "take_profit"


def test_time_decay_no_momentum():
    config = PositionConfig(time_decay_hours=8, time_decay_min_gain_pct=0.05)
    pos = _pos(entry_price=100.0, hours_ago=10)
    now = datetime.now(timezone.utc)
    result = evaluate_position(pos, 102.0, config, now)  # only 2% gain
    assert result == "time_decay"


def test_time_decay_with_momentum():
    config = PositionConfig(time_decay_hours=8, time_decay_min_gain_pct=0.05)
    pos = _pos(entry_price=100.0, hours_ago=10)
    now = datetime.now(timezone.utc)
    result = evaluate_position(pos, 110.0, config, now)  # 10% gain
    assert result is None  # still holding


def test_max_hold_time():
    config = PositionConfig(max_hold_hours=72)
    pos = _pos(entry_price=100.0, hours_ago=73)
    now = datetime.now(timezone.utc)
    result = evaluate_position(pos, 105.0, config, now)
    assert result == "max_hold_time"


def test_trailing_stop_ratchets_up():
    # trail_tighten_gain_pct=0.20 keeps normal 15% trail at 20% gain
    config = PositionConfig(trailing_stop_pct=0.15, trail_tighten_gain_pct=0.20)
    pos = _pos(entry_price=100.0, max_price=100.0, stop_price=85.0)

    # Price moves to 115 (15% gain < 20% tighten threshold) — uses 15% trail
    evaluate_position(pos, 115.0, config, datetime.now(timezone.utc))
    assert pos.max_price == 115.0
    assert pos.stop_price == pytest.approx(97.75)  # 115 * 0.85

    # Price drops to 110 — stop should NOT decrease
    evaluate_position(pos, 110.0, config, datetime.now(timezone.utc))
    assert pos.stop_price == pytest.approx(97.75)  # unchanged


def test_trailing_stop_tiered_tightens():
    """Once gain exceeds threshold, tighter trailing stop is applied."""
    config = PositionConfig(trailing_stop_pct=0.15, trail_tighten_gain_pct=0.10, trail_tightened_stop_pct=0.07)
    pos = _pos(entry_price=100.0, max_price=100.0, stop_price=85.0)

    # Price moves to 120 (20% gain >= 10% threshold) — uses 7% tight trail
    evaluate_position(pos, 120.0, config, datetime.now(timezone.utc))
    assert pos.max_price == 120.0
    assert pos.stop_price == pytest.approx(111.6)  # 120 * 0.93

    # Price drops to 115 — stop should NOT decrease
    evaluate_position(pos, 115.0, config, datetime.now(timezone.utc))
    assert pos.stop_price == pytest.approx(111.6)  # unchanged


def test_trailing_stop_never_decreases():
    # Use gain < tighten threshold so normal trail applies
    config = PositionConfig(trailing_stop_pct=0.15, trail_tighten_gain_pct=0.20)
    pos = _pos(entry_price=100.0, max_price=107.0, stop_price=90.95)

    # Price at 103 — below max but above stop
    result = evaluate_position(pos, 103.0, config, datetime.now(timezone.utc))
    assert result is None
    assert pos.stop_price == pytest.approx(90.95)  # unchanged, not lowered
