from __future__ import annotations

from datetime import datetime, timezone

from sniper_bot.config import PositionConfig
from sniper_bot.storage import Position


def evaluate_position(
    position: Position,
    current_price: float,
    config: PositionConfig,
    now: datetime,
) -> str | None:
    """Evaluate whether a position should be exited.

    Returns exit_reason string or None if position should be held.
    Also updates position.max_price and position.stop_price (trailing stop).
    """
    # Update high water mark and trailing stop (tiered: tighter once position is up enough)
    if current_price > position.max_price:
        position.max_price = current_price

    unrealized_gain = (position.max_price - position.entry_price) / position.entry_price if position.entry_price > 0 else 0.0
    if unrealized_gain >= config.trail_tighten_gain_pct:
        trail_pct = config.trail_tightened_stop_pct
    else:
        trail_pct = config.trailing_stop_pct

    new_stop = position.max_price * (1 - trail_pct)
    if new_stop > position.stop_price:
        position.stop_price = new_stop

    # Hard stop: absolute floor from entry
    hard_stop_price = position.entry_price * (1 - config.hard_stop_pct)
    if current_price <= hard_stop_price:
        return "hard_stop"

    # Trailing stop
    if current_price <= position.stop_price:
        return "trailing_stop"

    # Take profit
    if current_price >= position.entry_price * config.take_profit_multiple:
        return "take_profit"

    # Time-based exits
    entry_time = position.entry_time
    if entry_time.tzinfo is None:
        entry_time = entry_time.replace(tzinfo=timezone.utc)
    held_seconds = (now - entry_time).total_seconds()
    held_hours = held_seconds / 3600

    # Time decay: exit early if position is losing ground
    gain_pct = (current_price - position.entry_price) / position.entry_price
    # After half the time window: exit if still negative
    if held_hours >= config.time_decay_hours / 2:
        if gain_pct < 0:
            return "time_decay_early"
    # After full time window: exit if below minimum gain threshold
    if held_hours >= config.time_decay_hours:
        if gain_pct < config.time_decay_min_gain_pct:
            return "time_decay"

    # Max hold time
    if held_hours >= config.max_hold_hours:
        return "max_hold_time"

    return None
