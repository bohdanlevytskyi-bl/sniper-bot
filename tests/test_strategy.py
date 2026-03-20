from __future__ import annotations

from datetime import datetime, timedelta, timezone

import pandas as pd

from sniper_bot.config import StrategyConfig
from sniper_bot.strategy import PositionSnapshot, StrategyAction, build_indicator_frame, evaluate_strategy


def _trend_frame(periods: int = 260, final_drop: bool = False) -> pd.DataFrame:
    rows = []
    start = datetime(2026, 1, 1, tzinfo=timezone.utc)
    price = 10_000.0
    for index in range(periods):
        price += 20
        open_price = price - 10
        close_price = price
        high = price + 15
        low = price - 15
        if final_drop and index == periods - 1:
            close_price = price - 3_000
            low = close_price - 20
        rows.append(
            {
                "open_time": start + timedelta(hours=index),
                "open": open_price,
                "high": high,
                "low": low,
                "close": close_price,
                "volume": 5.0,
            }
        )
    return pd.DataFrame(rows)


def test_strategy_enters_when_trend_is_confirmed() -> None:
    frame = build_indicator_frame(_trend_frame(), StrategyConfig())
    decision = evaluate_strategy(frame, StrategyConfig(), position=None, entry_allowed=True)
    assert decision.action == StrategyAction.ENTER
    assert decision.reason == "ema_trend_confirmed"
    assert decision.next_stop is not None


def test_strategy_exits_when_close_below_fast_ema() -> None:
    config = StrategyConfig()
    frame = build_indicator_frame(_trend_frame(final_drop=True), config)
    position = PositionSnapshot(quantity=0.1, entry_price=10_000, stop_price=9_800, max_price=12_000)
    decision = evaluate_strategy(frame, config, position=position, entry_allowed=True)
    assert decision.action == StrategyAction.EXIT
    assert decision.reason in {"close_below_ema_fast", "atr_stop_hit"}


def test_strategy_updates_trailing_stop_while_holding() -> None:
    config = StrategyConfig()
    frame = build_indicator_frame(_trend_frame(), config)
    latest_close = float(frame.iloc[-1]["close"])
    position = PositionSnapshot(quantity=0.1, entry_price=10_000, stop_price=latest_close - 200, max_price=latest_close - 50)
    decision = evaluate_strategy(frame, config, position=position, entry_allowed=True)
    assert decision.action == StrategyAction.HOLD
    assert decision.next_stop is not None
    assert decision.next_stop >= position.stop_price
