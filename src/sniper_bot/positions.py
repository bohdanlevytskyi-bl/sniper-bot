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
    entry_price = position.entry_price or 0.0
    atr = getattr(position, "atr_at_entry", None)

    # Update high water mark
    if current_price > position.max_price:
        position.max_price = current_price

    # Compute trailing stop percentage (tiered or ATR-based)
    unrealized_gain = (position.max_price - entry_price) / entry_price if entry_price > 0 else 0.0

    if config.use_atr_stops and atr and atr > 0 and entry_price > 0:
        # ATR-based trailing: stop = peak - ATR * multiplier
        atr_trail_pct = (atr * config.atr_trail_multiplier) / entry_price
        # Clamp within min/max bounds
        trail_pct = max(config.atr_min_stop_pct, min(config.atr_max_stop_pct, atr_trail_pct))
        # Still tighten when in deep profit
        if unrealized_gain >= config.trail_tighten_gain_pct:
            trail_pct = min(trail_pct, config.trail_tightened_stop_pct)
    else:
        # Fixed percentage trailing (legacy)
        if unrealized_gain >= config.trail_tighten_gain_pct:
            trail_pct = config.trail_tightened_stop_pct
        else:
            trail_pct = config.trailing_stop_pct

    new_stop = position.max_price * (1 - trail_pct)
    if new_stop > position.stop_price:
        position.stop_price = new_stop

    # Hard stop: absolute floor from entry (ATR-based or fixed)
    if config.use_atr_stops and atr and atr > 0 and entry_price > 0:
        atr_hard_pct = (atr * config.atr_stop_multiplier) / entry_price
        hard_stop_pct = max(config.atr_min_stop_pct, min(config.atr_max_stop_pct, atr_hard_pct))
    else:
        hard_stop_pct = config.hard_stop_pct

    hard_stop_price = entry_price * (1 - hard_stop_pct)
    if current_price <= hard_stop_price:
        return "hard_stop"

    # Trailing stop
    if current_price <= position.stop_price:
        return "trailing_stop"

    # Take profit
    if current_price >= entry_price * config.take_profit_multiple:
        return "take_profit"

    # Time-based exits
    entry_time = position.entry_time
    if entry_time.tzinfo is None:
        entry_time = entry_time.replace(tzinfo=timezone.utc)
    held_seconds = (now - entry_time).total_seconds()
    held_hours = held_seconds / 3600

    # Time decay: exit early if position is losing ground
    gain_pct = (current_price - entry_price) / entry_price if entry_price > 0 else 0
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
