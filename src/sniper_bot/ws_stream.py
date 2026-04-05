"""WebSocket streaming client for Bybit real-time market data.

Subscribes to ticker and trade streams, caches latest data for use by the bot loop.
Runs in a background thread so the main loop can read cached data instead of polling REST.
"""
from __future__ import annotations

import json
import threading
import time
from collections import defaultdict
from dataclasses import dataclass, field
from datetime import datetime, timezone
from typing import Any

from sniper_bot.logging_config import get_logger

LOGGER = get_logger(__name__)

BYBIT_WS_PUBLIC_SPOT = "wss://stream.bybit.com/v5/public/spot"
BYBIT_WS_PUBLIC_SPOT_DEMO = "wss://stream-demo.bybit.com/v5/public/spot"


@dataclass
class TickerCache:
    """Thread-safe cache of latest ticker data from WebSocket."""
    _data: dict[str, dict[str, Any]] = field(default_factory=dict)
    _lock: threading.Lock = field(default_factory=threading.Lock, repr=False)
    _last_update: datetime | None = None

    def update(self, symbol: str, data: dict[str, Any]) -> None:
        with self._lock:
            self._data[symbol] = data
            self._last_update = datetime.now(timezone.utc)

    def get(self, symbol: str) -> dict[str, Any] | None:
        with self._lock:
            return self._data.get(symbol)

    def get_all(self) -> dict[str, dict[str, Any]]:
        with self._lock:
            return dict(self._data)

    @property
    def last_update(self) -> datetime | None:
        with self._lock:
            return self._last_update

    @property
    def symbol_count(self) -> int:
        with self._lock:
            return len(self._data)


@dataclass
class TradeCache:
    """Thread-safe cache of recent trades for whale detection."""
    _data: dict[str, list[dict[str, Any]]] = field(default_factory=lambda: defaultdict(list))
    _lock: threading.Lock = field(default_factory=threading.Lock, repr=False)
    _max_per_symbol: int = 100

    def add_trade(self, symbol: str, trade: dict[str, Any]) -> None:
        with self._lock:
            trades = self._data[symbol]
            trades.append(trade)
            if len(trades) > self._max_per_symbol:
                self._data[symbol] = trades[-self._max_per_symbol:]

    def get_recent(self, symbol: str, limit: int = 60) -> list[dict[str, Any]]:
        with self._lock:
            return list(self._data.get(symbol, []))[-limit:]

    def clear(self, symbol: str) -> None:
        with self._lock:
            self._data.pop(symbol, None)


class BybitWebSocket:
    """Manages a WebSocket connection to Bybit public spot streams.

    Usage:
        ws = BybitWebSocket(demo=True)
        ws.subscribe_tickers(["BTCUSDT", "ETHUSDT"])
        ws.start()
        # ... later ...
        ticker = ws.ticker_cache.get("BTCUSDT")
        ws.stop()
    """

    def __init__(self, demo: bool = True):
        self.url = BYBIT_WS_PUBLIC_SPOT_DEMO if demo else BYBIT_WS_PUBLIC_SPOT
        self.ticker_cache = TickerCache()
        self.trade_cache = TradeCache()
        self._subscriptions: list[str] = []
        self._thread: threading.Thread | None = None
        self._stop_event = threading.Event()
        self._connected = False

    def subscribe_tickers(self, symbols: list[str]) -> None:
        for sym in symbols:
            topic = f"tickers.{sym}"
            if topic not in self._subscriptions:
                self._subscriptions.append(topic)

    def subscribe_trades(self, symbols: list[str]) -> None:
        for sym in symbols:
            topic = f"publicTrade.{sym}"
            if topic not in self._subscriptions:
                self._subscriptions.append(topic)

    def start(self) -> None:
        if self._thread and self._thread.is_alive():
            return
        self._stop_event.clear()
        self._thread = threading.Thread(target=self._run_loop, daemon=True, name="bybit-ws")
        self._thread.start()
        LOGGER.info("ws_started", extra={"url": self.url, "subscriptions": len(self._subscriptions)})

    def stop(self) -> None:
        self._stop_event.set()
        if self._thread:
            self._thread.join(timeout=5)
        self._connected = False
        LOGGER.info("ws_stopped")

    @property
    def is_connected(self) -> bool:
        return self._connected

    def _run_loop(self) -> None:
        """Reconnection loop — runs in background thread."""
        import websockets.sync.client as ws_client

        while not self._stop_event.is_set():
            try:
                with ws_client.connect(self.url, close_timeout=5) as ws:
                    self._connected = True
                    LOGGER.info("ws_connected", extra={"url": self.url})

                    # Send subscription
                    if self._subscriptions:
                        sub_msg = {
                            "op": "subscribe",
                            "args": self._subscriptions,
                        }
                        ws.send(json.dumps(sub_msg))

                    # Read messages
                    while not self._stop_event.is_set():
                        try:
                            ws.socket.settimeout(5.0)
                            raw = ws.recv()
                            self._handle_message(raw)
                        except TimeoutError:
                            # Send ping to keep alive
                            ws.send(json.dumps({"op": "ping"}))
                        except Exception as e:
                            if not self._stop_event.is_set():
                                LOGGER.warning("ws_recv_error", extra={"error": str(e)[:100]})
                            break

            except Exception as e:
                self._connected = False
                if not self._stop_event.is_set():
                    LOGGER.warning("ws_connection_error", extra={"error": str(e)[:100]})
                    time.sleep(3)  # backoff before reconnect

        self._connected = False

    def _handle_message(self, raw: str) -> None:
        try:
            msg = json.loads(raw)
        except json.JSONDecodeError:
            return

        topic = msg.get("topic", "")
        data = msg.get("data")
        if not data:
            return

        if topic.startswith("tickers."):
            self._handle_ticker(topic, data)
        elif topic.startswith("publicTrade."):
            self._handle_trades(topic, data)

    def _handle_ticker(self, topic: str, data: dict[str, Any]) -> None:
        symbol = data.get("symbol", topic.split(".")[-1])
        self.ticker_cache.update(symbol, {
            "symbol": symbol,
            "last_price": float(data.get("lastPrice", 0)),
            "volume_24h": float(data.get("volume24h", 0)),
            "turnover_24h": float(data.get("turnover24h", 0)),
            "price_change_24h_pct": float(data.get("price24hPcnt", 0)),
            "high_24h": float(data.get("highPrice24h", 0)),
            "low_24h": float(data.get("lowPrice24h", 0)),
            "bid": float(data.get("bid1Price", 0)),
            "ask": float(data.get("ask1Price", 0)),
            "ts": datetime.now(timezone.utc).isoformat(),
        })

    def _handle_trades(self, topic: str, data: Any) -> None:
        if isinstance(data, list):
            trades = data
        else:
            trades = [data]

        for t in trades:
            symbol = t.get("s", topic.split(".")[-1])
            trade = {
                "price": float(t.get("p", 0)),
                "qty": float(t.get("v", 0)),
                "side": "buy" if t.get("S") == "Buy" else "sell",
                "time": datetime.now(timezone.utc),
                "value": float(t.get("p", 0)) * float(t.get("v", 0)),
            }
            self.trade_cache.add_trade(symbol, trade)
