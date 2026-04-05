"""Abstract exchange interface for multi-exchange support.

Defines the protocol that all exchange clients must implement.
Currently BybitClient is the only implementation; future clients
(Binance, OKX, etc.) will implement the same interface.
"""
from __future__ import annotations

from abc import ABC, abstractmethod
from dataclasses import dataclass
from datetime import datetime
from typing import Any, Protocol, runtime_checkable


@dataclass(slots=True)
class TickerInfo:
    """Normalized ticker data across exchanges."""
    symbol: str
    last_price: float
    volume_24h: float
    turnover_24h: float
    price_change_24h_pct: float
    high_24h: float
    low_24h: float
    bid: float
    ask: float


@dataclass(slots=True)
class InstrumentSpec:
    """Normalized instrument specification across exchanges."""
    symbol: str
    base_asset: str
    quote_asset: str
    quantity_step: str
    price_step: str
    quantity_decimals: int
    price_decimals: int
    min_order_qty: float
    min_order_amount: float
    max_market_order_qty: float


@dataclass(slots=True)
class OrderResult:
    """Normalized order fill result across exchanges."""
    order_id: str
    status: str
    avg_price: float
    executed_qty: float
    fee: float
    cost: float
    raw: dict[str, Any]


@runtime_checkable
class ExchangeClient(Protocol):
    """Protocol defining the interface all exchange clients must implement."""

    def close(self) -> None: ...

    # --- Public Market Data ---

    def fetch_all_tickers(self) -> list[TickerInfo]: ...

    def fetch_all_instruments(self, force: bool = False) -> dict[str, InstrumentSpec]: ...

    def fetch_klines(
        self, symbol: str, interval_minutes: int, limit: int = 200,
    ) -> list[dict[str, Any]]: ...

    def fetch_orderbook(
        self, symbol: str, depth: int = 25,
    ) -> dict[str, list[list[float]]]: ...

    def fetch_funding_rate(self, symbol: str) -> float | None: ...

    def fetch_recent_trades(
        self, symbol: str, limit: int = 60,
    ) -> list[dict[str, Any]]: ...

    def get_ticker(self, symbol: str) -> TickerInfo: ...

    # --- Private Trading ---

    def get_balance(self, coins: list[str] | None = None) -> dict[str, float]: ...

    def place_market_order(self, symbol: str, side: str, quantity: float) -> str: ...

    def poll_order(
        self, symbol: str, order_id: str, attempts: int = 15, sleep_seconds: float = 1.5,
    ) -> OrderResult: ...


class ExchangeFactory:
    """Factory for creating exchange clients by name."""

    _registry: dict[str, type] = {}

    @classmethod
    def register(cls, name: str, client_class: type) -> None:
        cls._registry[name.lower()] = client_class

    @classmethod
    def create(cls, name: str, **kwargs: Any) -> Any:
        key = name.lower()
        if key not in cls._registry:
            available = ", ".join(cls._registry.keys()) or "none"
            raise ValueError(f"Unknown exchange '{name}'. Available: {available}")
        return cls._registry[key](**kwargs)

    @classmethod
    def available(cls) -> list[str]:
        return list(cls._registry.keys())


def register_builtin_exchanges() -> None:
    """Register built-in exchange implementations."""
    # Bybit is always available
    from sniper_bot.exchange import BybitClient
    ExchangeFactory.register("bybit", BybitClient)
