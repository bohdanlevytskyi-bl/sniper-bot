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


def compute_pearson_correlation(x: list[float], y: list[float]) -> float | None:
    """Compute Pearson correlation coefficient between two price return series.

    Returns -1.0 to +1.0, or None if insufficient data.
    """
    n = min(len(x), len(y))
    if n < 5:
        return None
    x, y = x[-n:], y[-n:]

    mean_x = sum(x) / n
    mean_y = sum(y) / n

    cov = sum((xi - mean_x) * (yi - mean_y) for xi, yi in zip(x, y))
    var_x = sum((xi - mean_x) ** 2 for xi in x)
    var_y = sum((yi - mean_y) ** 2 for yi in y)

    denom = (var_x * var_y) ** 0.5
    if denom == 0:
        return None
    return cov / denom


def price_returns(candles: list[dict[str, Any]]) -> list[float]:
    """Compute percentage returns from candle closes."""
    if len(candles) < 2:
        return []
    closes = [c["close"] for c in candles]
    return [(closes[i] - closes[i - 1]) / closes[i - 1] for i in range(1, len(closes)) if closes[i - 1] > 0]


def compute_atr(candles: list[dict[str, Any]], period: int = 14) -> float | None:
    """Compute Average True Range from candles. Returns None if insufficient data."""
    if len(candles) < period + 1:
        return None

    true_ranges: list[float] = []
    for i in range(1, len(candles)):
        high = candles[i]["high"]
        low = candles[i]["low"]
        prev_close = candles[i - 1]["close"]
        tr = max(high - low, abs(high - prev_close), abs(low - prev_close))
        true_ranges.append(tr)

    if len(true_ranges) < period:
        return None
    return sum(true_ranges[-period:]) / period


def detect_whale_trades(
    trades: list[dict[str, Any]], std_multiplier: float = 3.0,
) -> dict[str, Any]:
    """Detect abnormally large trades (whale activity).

    trades: list of {price, qty, side, value} from recent public trades.
    Returns dict with whale_score (-1 to +1), whale_buy_volume, whale_sell_volume, whale_count.
    Positive score = net whale buying. Negative = net whale selling.
    """
    if len(trades) < 10:
        return {"whale_score": 0.0, "whale_buy_volume": 0.0, "whale_sell_volume": 0.0, "whale_count": 0}

    values = [t.get("value", 0.0) for t in trades]
    mean_val = sum(values) / len(values)
    variance = sum((v - mean_val) ** 2 for v in values) / len(values)
    std_val = variance ** 0.5

    if std_val == 0:
        return {"whale_score": 0.0, "whale_buy_volume": 0.0, "whale_sell_volume": 0.0, "whale_count": 0}
    threshold = mean_val + std_multiplier * std_val
    if threshold <= 0:
        return {"whale_score": 0.0, "whale_buy_volume": 0.0, "whale_sell_volume": 0.0, "whale_count": 0}

    whale_buy = 0.0
    whale_sell = 0.0
    whale_count = 0

    for t in trades:
        val = t.get("value", 0.0)
        if val >= threshold:
            whale_count += 1
            if t.get("side") == "buy":
                whale_buy += val
            else:
                whale_sell += val

    total_whale = whale_buy + whale_sell
    if total_whale == 0:
        return {"whale_score": 0.0, "whale_buy_volume": 0.0, "whale_sell_volume": 0.0, "whale_count": whale_count}

    # Score: net whale buying pressure, -1 to +1
    score = (whale_buy - whale_sell) / total_whale
    return {
        "whale_score": round(score, 4),
        "whale_buy_volume": round(whale_buy, 2),
        "whale_sell_volume": round(whale_sell, 2),
        "whale_count": whale_count,
    }


def compute_vwap(candles: list[dict[str, Any]], period: int | None = None) -> dict[str, float] | None:
    """Compute VWAP (Volume-Weighted Average Price) from candle data.

    Returns {vwap, deviation_pct} where deviation_pct is how far current price is from VWAP.
    Positive deviation = price above VWAP, negative = below.
    """
    if len(candles) < 5:
        return None

    subset = candles[-period:] if period and period < len(candles) else candles

    cum_vol_price = 0.0
    cum_vol = 0.0
    for c in subset:
        typical_price = (c["high"] + c["low"] + c["close"]) / 3.0
        vol = c.get("volume", 0.0)
        cum_vol_price += typical_price * vol
        cum_vol += vol

    if cum_vol == 0:
        return None

    vwap = cum_vol_price / cum_vol
    current = candles[-1]["close"]
    deviation_pct = (current - vwap) / vwap if vwap > 0 else 0.0

    return {
        "vwap": round(vwap, 8),
        "deviation_pct": round(deviation_pct, 6),
    }


def compute_multi_timeframe_score(
    tf_signals: dict[str, float],
) -> float:
    """Combine signals across multiple timeframes into a confluence score.

    tf_signals: dict of timeframe_name → score (0-1), e.g. {"5m": 0.7, "1h": 0.6, "4h": 0.8}
    Weights: shorter timeframes weighted less (noise), longer timeframes weighted more (trend).
    Returns 0-1 confluence score.
    """
    if not tf_signals:
        return 0.5

    # Weight map: longer TFs get more weight
    weight_map = {"5m": 0.10, "15m": 0.15, "1h": 0.30, "4h": 0.45}
    total_weight = 0.0
    weighted_sum = 0.0

    for tf, score in tf_signals.items():
        w = weight_map.get(tf, 0.25)
        weighted_sum += w * score
        total_weight += w

    if total_weight == 0:
        return 0.5
    return round(weighted_sum / total_weight, 4)


def compute_spread_signal(bid: float, ask: float, price: float) -> float:
    """Compute bid-ask spread as a signal. Tight spread = liquid = good.

    Returns 0.0 (very wide spread, illiquid) to 1.0 (very tight spread, liquid).
    """
    if price <= 0 or ask <= 0 or bid <= 0:
        return 0.5
    spread_pct = (ask - bid) / price
    # Map: 0% spread → 1.0, 0.5% spread → 0.5, >1% → 0.1
    if spread_pct <= 0:
        return 1.0
    if spread_pct >= 0.01:
        return 0.1
    return round(max(0.1, 1.0 - spread_pct * 100), 4)


def compute_trade_flow_toxicity(trades: list[dict[str, Any]]) -> float:
    """Estimate trade flow toxicity using buy/sell volume imbalance over recent trades.

    High toxicity = aggressive directional flow (informed traders).
    Returns 0.0 (balanced) to 1.0 (highly toxic / directional).
    """
    if len(trades) < 10:
        return 0.0

    buy_vol = sum(t.get("value", 0.0) for t in trades if t.get("side") == "buy")
    sell_vol = sum(t.get("value", 0.0) for t in trades if t.get("side") == "sell")
    total = buy_vol + sell_vol

    if total == 0:
        return 0.0

    # Toxicity = absolute imbalance ratio
    imbalance = abs(buy_vol - sell_vol) / total
    return round(imbalance, 4)


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
