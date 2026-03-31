"""Technical analysis indicators computed from kline data."""
from __future__ import annotations

from typing import Any


def compute_rsi(candles: list[dict[str, Any]], period: int = 14) -> float | None:
    """Compute RSI from candle close prices. Returns 0-100 or None if insufficient data."""
    if len(candles) < period + 1:
        return None

    closes = [c["close"] for c in candles[-(period + 1):]]
    gains: list[float] = []
    losses: list[float] = []

    for i in range(1, len(closes)):
        delta = closes[i] - closes[i - 1]
        if delta > 0:
            gains.append(delta)
            losses.append(0.0)
        else:
            gains.append(0.0)
            losses.append(abs(delta))

    avg_gain = sum(gains) / period
    avg_loss = sum(losses) / period

    if avg_loss == 0:
        return 100.0
    rs = avg_gain / avg_loss
    return 100.0 - (100.0 / (1.0 + rs))


def compute_macd(
    candles: list[dict[str, Any]],
    fast: int = 12,
    slow: int = 26,
    signal: int = 9,
) -> dict[str, float] | None:
    """Compute MACD, signal line, and histogram. Returns None if insufficient data."""
    if len(candles) < slow + signal:
        return None

    closes = [c["close"] for c in candles]

    fast_ema = _ema(closes, fast)
    slow_ema = _ema(closes, slow)

    if fast_ema is None or slow_ema is None:
        return None

    # MACD line = fast EMA - slow EMA (using last values)
    fast_values = _ema_series(closes, fast)
    slow_values = _ema_series(closes, slow)

    # Align: slow_values starts later
    offset = slow - fast
    macd_values = [f - s for f, s in zip(fast_values[offset:], slow_values)]

    if len(macd_values) < signal:
        return None

    signal_values = _ema_series(macd_values, signal)

    macd_line = macd_values[-1]
    signal_line = signal_values[-1]
    histogram = macd_line - signal_line

    return {
        "macd": macd_line,
        "signal": signal_line,
        "histogram": histogram,
    }


def compute_bollinger_bands(
    candles: list[dict[str, Any]], period: int = 20, std_dev: float = 2.0,
) -> dict[str, float] | None:
    """Compute Bollinger Bands. Returns None if insufficient data."""
    if len(candles) < period:
        return None

    closes = [c["close"] for c in candles[-period:]]
    mean = sum(closes) / len(closes)
    variance = sum((c - mean) ** 2 for c in closes) / len(closes)
    std = variance ** 0.5

    upper = mean + std_dev * std
    lower = mean - std_dev * std
    current_price = closes[-1]

    # %B: where current price is relative to the bands (0 = lower, 1 = upper)
    band_width = upper - lower
    pct_b = (current_price - lower) / band_width if band_width > 0 else 0.5

    return {
        "upper": upper,
        "middle": mean,
        "lower": lower,
        "pct_b": pct_b,          # 0-1 range (can exceed)
        "bandwidth": band_width / mean if mean > 0 else 0,  # normalized width
    }


def compute_obi_score(bids: list[list[float]], asks: list[list[float]]) -> float:
    """Compute Order Book Imbalance from order book depth.

    bids/asks: [[price, qty], ...] — top N levels.
    Returns -1.0 (heavy selling) to +1.0 (heavy buying).
    """
    if not bids or not asks:
        return 0.0

    bid_value = sum(price * qty for price, qty in bids)
    ask_value = sum(price * qty for price, qty in asks)
    total = bid_value + ask_value

    if total == 0:
        return 0.0
    return (bid_value - ask_value) / total


def compute_funding_rate_signal(funding_rate: float) -> float:
    """Convert funding rate to a signal score.

    High positive rate (>0.01%) = overleveraged longs = bearish risk → negative signal
    High negative rate (<-0.01%) = contrarian buy signal → positive signal
    Returns -1.0 to +1.0
    """
    # Typical funding rate is ~0.01% per 8h
    # Extreme is >0.05% or <-0.05%
    clamped = max(-0.001, min(0.001, funding_rate))
    # Invert: high positive funding = bearish signal
    return -clamped / 0.001


def compute_ta_composite(
    rsi: float | None,
    macd: dict[str, float] | None,
    bbands: dict[str, float] | None,
) -> float:
    """Combine TA indicators into a single 0-1 score.

    Higher = more bullish signal.
    """
    signals: list[float] = []

    # RSI: bullish if 30-60 (recovering from oversold), neutral 40-60, bearish >70
    if rsi is not None:
        if rsi < 30:
            signals.append(0.8)   # oversold bounce potential
        elif rsi < 45:
            signals.append(0.7)   # recovering
        elif rsi < 60:
            signals.append(0.5)   # neutral
        elif rsi < 70:
            signals.append(0.3)   # getting hot
        else:
            signals.append(0.1)   # overbought, risky entry

    # MACD: bullish when histogram is positive and growing
    if macd is not None:
        hist = macd["histogram"]
        if hist > 0 and macd["macd"] > macd["signal"]:
            signals.append(min(0.9, 0.5 + hist * 100))  # bullish crossover
        elif hist > 0:
            signals.append(0.5)
        elif hist < 0 and macd["macd"] < macd["signal"]:
            signals.append(max(0.1, 0.5 + hist * 100))  # bearish
        else:
            signals.append(0.4)

    # Bollinger Bands: bullish if price near lower band (mean reversion)
    if bbands is not None:
        pct_b = bbands["pct_b"]
        if pct_b < 0.2:
            signals.append(0.8)   # near lower band — bounce likely
        elif pct_b < 0.4:
            signals.append(0.6)
        elif pct_b < 0.6:
            signals.append(0.5)   # middle
        elif pct_b < 0.8:
            signals.append(0.3)
        else:
            signals.append(0.1)   # near upper band — overbought

    if not signals:
        return 0.5  # neutral default
    return sum(signals) / len(signals)


# ---------------------------------------------------------------------------
# Internal EMA helpers
# ---------------------------------------------------------------------------

def _ema(values: list[float], period: int) -> float | None:
    """Simple EMA returning final value."""
    if len(values) < period:
        return None
    series = _ema_series(values, period)
    return series[-1] if series else None


def _ema_series(values: list[float], period: int) -> list[float]:
    """Compute full EMA series."""
    if len(values) < period:
        return []
    multiplier = 2.0 / (period + 1)
    # Seed with SMA of first `period` values
    sma = sum(values[:period]) / period
    result = [sma]
    for v in values[period:]:
        result.append((v - result[-1]) * multiplier + result[-1])
    return result
