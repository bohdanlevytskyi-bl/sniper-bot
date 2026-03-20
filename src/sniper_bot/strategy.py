from __future__ import annotations

from dataclasses import dataclass
from enum import StrEnum

import pandas as pd

from sniper_bot.config import StrategyConfig


class StrategyAction(StrEnum):
    ENTER = "enter"
    EXIT = "exit"
    HOLD = "hold"


@dataclass(slots=True)
class PositionSnapshot:
    quantity: float
    entry_price: float
    stop_price: float
    max_price: float


@dataclass(slots=True)
class StrategyDecision:
    action: StrategyAction
    reason: str
    close_price: float
    ema_fast: float
    ema_slow: float
    atr: float
    next_stop: float | None


def build_indicator_frame(candles: pd.DataFrame, config: StrategyConfig) -> pd.DataFrame:
    frame = candles.copy()
    frame["ema_fast"] = frame["close"].ewm(span=config.ema_fast, adjust=False).mean()
    frame["ema_slow"] = frame["close"].ewm(span=config.ema_slow, adjust=False).mean()
    prev_close = frame["close"].shift(1)
    true_range = pd.concat(
        [
            frame["high"] - frame["low"],
            (frame["high"] - prev_close).abs(),
            (frame["low"] - prev_close).abs(),
        ],
        axis=1,
    ).max(axis=1)
    frame["atr"] = true_range.ewm(alpha=1 / config.atr_period, adjust=False).mean()
    return frame


def evaluate_strategy(
    indicator_frame: pd.DataFrame,
    config: StrategyConfig,
    position: PositionSnapshot | None,
    entry_allowed: bool,
) -> StrategyDecision:
    if len(indicator_frame) < max(config.ema_slow, config.atr_period) + config.slope_lookback_bars:
        latest = indicator_frame.iloc[-1]
        return StrategyDecision(
            action=StrategyAction.HOLD,
            reason="insufficient_history",
            close_price=float(latest["close"]),
            ema_fast=float(latest.get("ema_fast", latest["close"])),
            ema_slow=float(latest.get("ema_slow", latest["close"])),
            atr=float(latest.get("atr", 0.0)),
            next_stop=position.stop_price if position else None,
        )

    latest = indicator_frame.iloc[-1]
    slope_reference = indicator_frame.iloc[-1 - config.slope_lookback_bars]
    close_price = float(latest["close"])
    ema_fast = float(latest["ema_fast"])
    ema_slow = float(latest["ema_slow"])
    atr = float(latest["atr"])

    if position is None:
        if not entry_allowed:
            return StrategyDecision(
                action=StrategyAction.HOLD,
                reason="risk_gate_closed",
                close_price=close_price,
                ema_fast=ema_fast,
                ema_slow=ema_slow,
                atr=atr,
                next_stop=None,
            )
        if close_price > ema_slow and ema_fast > float(slope_reference["ema_fast"]):
            next_stop = close_price - (config.atr_stop_multiple * atr)
            return StrategyDecision(
                action=StrategyAction.ENTER,
                reason="ema_trend_confirmed",
                close_price=close_price,
                ema_fast=ema_fast,
                ema_slow=ema_slow,
                atr=atr,
                next_stop=next_stop,
            )
        return StrategyDecision(
            action=StrategyAction.HOLD,
            reason="trend_not_confirmed",
            close_price=close_price,
            ema_fast=ema_fast,
            ema_slow=ema_slow,
            atr=atr,
            next_stop=None,
        )

    trailing_stop = max(position.stop_price, close_price - (config.atr_stop_multiple * atr))
    if float(latest["low"]) <= position.stop_price:
        return StrategyDecision(
            action=StrategyAction.EXIT,
            reason="atr_stop_hit",
            close_price=close_price,
            ema_fast=ema_fast,
            ema_slow=ema_slow,
            atr=atr,
            next_stop=position.stop_price,
        )
    if close_price < ema_fast:
        return StrategyDecision(
            action=StrategyAction.EXIT,
            reason="close_below_ema_fast",
            close_price=close_price,
            ema_fast=ema_fast,
            ema_slow=ema_slow,
            atr=atr,
            next_stop=position.stop_price,
        )
    return StrategyDecision(
        action=StrategyAction.HOLD,
        reason="position_open",
        close_price=close_price,
        ema_fast=ema_fast,
        ema_slow=ema_slow,
        atr=atr,
        next_stop=trailing_stop,
    )
