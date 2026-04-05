from __future__ import annotations

from dataclasses import dataclass
from typing import Any

from sniper_bot.config import StrategyConfig


@dataclass(slots=True)
class ScoredToken:
    symbol: str
    composite_score: float
    volume_score: float
    momentum_score: float
    relative_strength_score: float
    ta_score: float
    obi_score: float
    funding_score: float
    whale_score: float
    vwap_score: float
    mtf_score: float
    microstructure_score: float
    volume_spike_ratio: float
    price: float


def score_candidate(
    symbol: str,
    price: float,
    volume_spike_ratio: float,
    price_changes: dict[str, float],
    btc_change_1h: float,
    config: StrategyConfig,
    ta_composite: float = 0.5,
    obi_value: float = 0.0,
    funding_signal: float = 0.0,
    whale_signal: float = 0.0,
    vwap_deviation: float = 0.0,
    mtf_confluence: float = 0.5,
    microstructure: float = 0.5,
) -> ScoredToken:
    """Compute composite score for a single candidate."""

    # Volume score: spike ratio normalized to 0-1, capped at 10x
    volume_score = min(volume_spike_ratio / 10.0, 1.0) if volume_spike_ratio > 0 else 0.0

    # Momentum score: weighted average of positive price changes across windows
    momentum_raw = 0.0
    for window in config.momentum_windows_minutes:
        key = f"{window}m"
        change = max(0.0, price_changes.get(key, 0.0))
        momentum_raw += change / window  # normalize by window length
    # Cap at ~2% per minute average across windows
    momentum_score = min(momentum_raw / 0.02, 1.0) if momentum_raw > 0 else 0.0

    # Relative strength vs BTC
    change_1h = price_changes.get("60m", 0.0)
    rs_raw = max(0.0, change_1h - btc_change_1h)
    relative_strength_score = min(rs_raw / 0.10, 1.0)  # cap at 10% outperformance

    # TA composite: already 0-1 from indicators module
    ta_score = max(0.0, min(1.0, ta_composite))

    # OBI: -1 to +1 → normalize to 0-1
    obi_score = max(0.0, min(1.0, (obi_value + 1.0) / 2.0))

    # Funding signal: -1 to +1 → normalize to 0-1
    funding_score = max(0.0, min(1.0, (funding_signal + 1.0) / 2.0))

    # Whale signal: -1 to +1 → normalize to 0-1 (positive = net whale buying)
    whale_score = max(0.0, min(1.0, (whale_signal + 1.0) / 2.0))

    # VWAP deviation: negative deviation (below VWAP) = bullish for mean-reversion
    # Map: -5% → 0.9, 0% → 0.5, +5% → 0.1
    vwap_score = max(0.0, min(1.0, 0.5 - vwap_deviation * 10.0))

    # Multi-timeframe confluence: already 0-1
    mtf_score = max(0.0, min(1.0, mtf_confluence))

    # Microstructure: already 0-1
    micro_score = max(0.0, min(1.0, microstructure))

    composite = (
        config.volume_weight * volume_score
        + config.momentum_weight * momentum_score
        + config.relative_strength_weight * relative_strength_score
        + config.ta_weight * ta_score
        + config.obi_weight * obi_score
        + config.funding_weight * funding_score
        + config.whale_weight * whale_score
        + config.vwap_weight * vwap_score
        + config.mtf_weight * mtf_score
        + config.microstructure_weight * micro_score
    )

    return ScoredToken(
        symbol=symbol,
        composite_score=round(composite, 4),
        volume_score=round(volume_score, 4),
        momentum_score=round(momentum_score, 4),
        relative_strength_score=round(relative_strength_score, 4),
        ta_score=round(ta_score, 4),
        obi_score=round(obi_score, 4),
        funding_score=round(funding_score, 4),
        whale_score=round(whale_score, 4),
        vwap_score=round(vwap_score, 4),
        mtf_score=round(mtf_score, 4),
        microstructure_score=round(micro_score, 4),
        volume_spike_ratio=round(volume_spike_ratio, 4),
        price=price,
    )


def rank_candidates(scored: list[ScoredToken], config: StrategyConfig) -> list[ScoredToken]:
    """Filter by min score and sort descending."""
    passing = [s for s in scored if s.composite_score >= config.min_entry_score]
    passing.sort(key=lambda s: s.composite_score, reverse=True)
    return passing


def compute_price_changes(candles_5m: list[dict], candles_1h: list[dict]) -> dict[str, float]:
    """Compute price changes over various windows from kline data.

    candles_5m: 5-minute candles (oldest first)
    candles_1h: 1-hour candles (oldest first)
    Returns dict like {"5m": 0.03, "15m": 0.05, "60m": 0.10}
    """
    changes: dict[str, float] = {}

    if candles_5m and len(candles_5m) >= 2:
        current = candles_5m[-1]["close"]
        if len(candles_5m) >= 1:
            prev_5m = candles_5m[-2]["close"] if len(candles_5m) >= 2 else current
            changes["5m"] = (current - prev_5m) / prev_5m if prev_5m else 0.0
        if len(candles_5m) >= 4:
            prev_15m = candles_5m[-4]["close"]
            changes["15m"] = (current - prev_15m) / prev_15m if prev_15m else 0.0

    if candles_1h and len(candles_1h) >= 2:
        current_1h = candles_1h[-1]["close"]
        prev_1h = candles_1h[-2]["close"]
        changes["60m"] = (current_1h - prev_1h) / prev_1h if prev_1h else 0.0

    return changes
