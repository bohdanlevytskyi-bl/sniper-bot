from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any

from sniper_bot.config import ScannerConfig
from sniper_bot.exchange import TickerData


@dataclass(slots=True)
class TokenCandidate:
    symbol: str
    last_price: float
    volume_24h: float
    turnover_24h: float
    price_change_24h_pct: float
    high_24h: float
    low_24h: float
    volume_spike_ratio: float = 0.0


STABLECOIN_BASES = {"USDC", "DAI", "TUSD", "USDD", "FDUSD", "PYUSD", "BUSD", "USDP", "GUSD"}


def scan_market(tickers: list[TickerData], config: ScannerConfig) -> list[TokenCandidate]:
    """Filter all tickers down to candidates worth enriching."""
    candidates: list[TokenCandidate] = []

    for t in tickers:
        if not t.symbol.endswith(config.quote_asset):
            continue
        if t.symbol in config.excluded_pairs:
            continue
        base = t.symbol.removesuffix(config.quote_asset)
        if base in STABLECOIN_BASES:
            continue
        if t.volume_24h <= 0 or t.turnover_24h <= 0 or t.last_price <= 0:
            continue
        if t.turnover_24h < config.min_turnover_24h_usd:
            continue
        if t.volume_24h * t.last_price < config.min_volume_24h_usd:
            continue
        if abs(t.price_change_24h_pct) > config.max_price_change_24h_pct:
            continue

        candidates.append(
            TokenCandidate(
                symbol=t.symbol,
                last_price=t.last_price,
                volume_24h=t.volume_24h,
                turnover_24h=t.turnover_24h,
                price_change_24h_pct=t.price_change_24h_pct,
                high_24h=t.high_24h,
                low_24h=t.low_24h,
            )
        )

    # Sort by turnover descending — highest activity first
    candidates.sort(key=lambda c: c.turnover_24h, reverse=True)
    return candidates


def compute_volume_spike(
    recent_candles: list[dict[str, Any]],
    avg_24h_volume_per_bar: float,
) -> float:
    """Compute volume spike ratio: recent average volume per bar / 24h average per bar."""
    if avg_24h_volume_per_bar <= 0 or not recent_candles:
        return 0.0
    recent_avg = sum(c.get("volume", 0) for c in recent_candles) / len(recent_candles)
    return recent_avg / avg_24h_volume_per_bar
