from __future__ import annotations

import hashlib
import hmac
import json
import time
import urllib.parse
from dataclasses import dataclass
from datetime import datetime, timezone
from decimal import ROUND_DOWN, Decimal
from typing import Any

import httpx
from tenacity import retry, retry_if_exception_type, stop_after_attempt, wait_exponential

from sniper_bot.config import ExchangeConfig


class ExchangeAPIError(RuntimeError):
    pass


class RetryableExchangeError(ExchangeAPIError):
    pass


@dataclass(slots=True)
class InstrumentInfo:
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
class TickerData:
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
class OrderFillInfo:
    order_id: str
    status: str
    avg_price: float
    executed_qty: float
    fee: float
    cost: float
    raw: dict[str, Any]


class BybitClient:
    def __init__(
        self,
        config: ExchangeConfig,
        api_key: str | None = None,
        api_secret: str | None = None,
    ):
        self.config = config
        self.api_key = api_key
        self.api_secret = api_secret
        self.client = httpx.Client(
            base_url=config.base_url,
            timeout=config.timeout_seconds,
            headers={"User-Agent": config.user_agent},
        )
        self._last_timestamp_ms = 0
        self._instruments_cache: dict[str, InstrumentInfo] | None = None

    def close(self) -> None:
        self.client.close()

    # ------------------------------------------------------------------
    # Public endpoints
    # ------------------------------------------------------------------

    def fetch_all_tickers(self) -> list[TickerData]:
        result = self._public_get("/v5/market/tickers", {"category": "spot"})
        tickers: list[TickerData] = []
        for item in result.get("list", []):
            tickers.append(
                TickerData(
                    symbol=item.get("symbol", ""),
                    last_price=float(item.get("lastPrice") or 0),
                    volume_24h=float(item.get("volume24h") or 0),
                    turnover_24h=float(item.get("turnover24h") or 0),
                    price_change_24h_pct=float(item.get("price24hPcnt") or 0),
                    high_24h=float(item.get("highPrice24h") or 0),
                    low_24h=float(item.get("lowPrice24h") or 0),
                    bid=float(item.get("bid1Price") or 0),
                    ask=float(item.get("ask1Price") or 0),
                )
            )
        return tickers

    def fetch_all_instruments(self, force: bool = False) -> dict[str, InstrumentInfo]:
        if self._instruments_cache is not None and not force:
            return self._instruments_cache
        result = self._public_get(
            "/v5/market/instruments-info", {"category": "spot", "limit": "1000"}
        )
        instruments: dict[str, InstrumentInfo] = {}
        for item in result.get("list", []):
            lot = item.get("lotSizeFilter", {})
            price_filter = item.get("priceFilter", {})
            qty_step = str(lot.get("basePrecision") or lot.get("qtyStep") or "0.00000001")
            p_step = str(price_filter.get("tickSize") or "0.01")
            info = InstrumentInfo(
                symbol=item.get("symbol", ""),
                base_asset=item.get("baseCoin", ""),
                quote_asset=item.get("quoteCoin", ""),
                quantity_step=qty_step,
                price_step=p_step,
                quantity_decimals=_precision_from_step(qty_step),
                price_decimals=_precision_from_step(p_step),
                min_order_qty=float(lot.get("minOrderQty") or 0),
                min_order_amount=float(lot.get("minOrderAmt") or 0),
                max_market_order_qty=float(lot.get("maxMarketOrderQty") or 0),
            )
            instruments[info.symbol] = info
        self._instruments_cache = instruments
        return instruments

    def fetch_klines(
        self, symbol: str, interval_minutes: int, limit: int = 200
    ) -> list[dict[str, Any]]:
        result = self._public_get(
            "/v5/market/kline",
            {"category": "spot", "symbol": symbol, "interval": str(interval_minutes), "limit": min(limit, 1000)},
        )
        candles: list[dict[str, Any]] = []
        for item in reversed(result.get("list", [])):
            candles.append(
                {
                    "open_time": datetime.fromtimestamp(int(item[0]) / 1000, tz=timezone.utc),
                    "open": float(item[1]),
                    "high": float(item[2]),
                    "low": float(item[3]),
                    "close": float(item[4]),
                    "volume": float(item[5]),
                }
            )
        return candles

    def fetch_orderbook(self, symbol: str, depth: int = 25) -> dict[str, list[list[float]]]:
        """Fetch order book depth. Returns {"bids": [[price, qty], ...], "asks": [...]}."""
        result = self._public_get(
            "/v5/market/orderbook",
            {"category": "spot", "symbol": symbol, "limit": min(depth, 200)},
        )
        bids = [[float(p), float(q)] for p, q in result.get("b", [])]
        asks = [[float(p), float(q)] for p, q in result.get("a", [])]
        return {"bids": bids, "asks": asks}

    def fetch_funding_rate(self, symbol: str) -> float | None:
        """Fetch current funding rate for a linear perpetual. Returns None if not available."""
        try:
            perp_symbol = symbol  # e.g. BTCUSDT works for both spot and linear
            result = self._public_get(
                "/v5/market/tickers",
                {"category": "linear", "symbol": perp_symbol},
            )
            items = result.get("list", [])
            if items:
                rate_str = items[0].get("fundingRate")
                if rate_str:
                    return float(rate_str)
        except Exception:
            pass
        return None

    def get_ticker(self, symbol: str) -> TickerData:
        result = self._public_get("/v5/market/tickers", {"category": "spot", "symbol": symbol})
        items = result.get("list", [])
        if not items:
            raise ExchangeAPIError(f"No ticker data for {symbol}")
        item = items[0]
        return TickerData(
            symbol=item.get("symbol", symbol),
            last_price=float(item.get("lastPrice") or 0),
            volume_24h=float(item.get("volume24h") or 0),
            turnover_24h=float(item.get("turnover24h") or 0),
            price_change_24h_pct=float(item.get("price24hPcnt") or 0),
            high_24h=float(item.get("highPrice24h") or 0),
            low_24h=float(item.get("lowPrice24h") or 0),
            bid=float(item.get("bid1Price") or 0),
            ask=float(item.get("ask1Price") or 0),
        )

    # ------------------------------------------------------------------
    # Private endpoints
    # ------------------------------------------------------------------

    def get_balance(self, coins: list[str] | None = None) -> dict[str, float]:
        params: dict[str, Any] = {"accountType": self.config.account_type}
        if coins:
            params["coin"] = ",".join(sorted(set(coins)))
        result = self._private_get("/v5/account/wallet-balance", params)
        accounts = result.get("list", [])
        if not accounts:
            return {}
        balances: dict[str, float] = {}
        for coin in accounts[0].get("coin", []):
            sym = coin.get("coin")
            if sym:
                balances[sym] = float(coin.get("walletBalance") or 0)
        return balances

    def place_market_order(self, symbol: str, side: str, quantity: float) -> str:
        instrument = self._get_instrument(symbol)
        rounded = round_quantity(quantity, instrument.quantity_step)
        payload = {
            "category": "spot",
            "symbol": symbol,
            "side": "Buy" if side == "buy" else "Sell",
            "orderType": "Market",
            "qty": format_decimal(rounded, instrument.quantity_decimals),
            "marketUnit": "baseCoin",
            "isLeverage": 0,
            "orderFilter": "Order",
        }
        result = self._private_post("/v5/order/create", payload)
        order_id = result.get("orderId")
        if not order_id:
            raise ExchangeAPIError("Order response missing orderId")
        return order_id

    def poll_order(
        self, symbol: str, order_id: str, attempts: int = 15, sleep_seconds: float = 1.5
    ) -> OrderFillInfo:
        for _ in range(attempts):
            result = self._private_get(
                "/v5/order/realtime",
                {"category": "spot", "symbol": symbol, "orderId": order_id},
            )
            orders = result.get("list", [])
            if not orders:
                time.sleep(sleep_seconds)
                continue
            order = orders[0]
            status = order.get("orderStatus", "Unknown")
            if status in {"Filled", "Cancelled", "PartiallyFilledCanceled", "Rejected", "Deactivated"}:
                executed = float(order.get("cumExecQty") or 0)
                cost = float(order.get("cumExecValue") or 0)
                avg = float(order.get("avgPrice") or 0) or (cost / executed if executed else 0)
                return OrderFillInfo(
                    order_id=order_id,
                    status=status.lower(),
                    avg_price=avg,
                    executed_qty=executed,
                    fee=_extract_fee(order),
                    cost=cost,
                    raw=order,
                )
            time.sleep(sleep_seconds)
        raise RetryableExchangeError(f"Timed out waiting for order {order_id}")

    # ------------------------------------------------------------------
    # Internal
    # ------------------------------------------------------------------

    def _get_instrument(self, symbol: str) -> InstrumentInfo:
        instruments = self.fetch_all_instruments()
        if symbol in instruments:
            return instruments[symbol]
        raise ExchangeAPIError(f"Unknown instrument {symbol}")

    @retry(
        retry=retry_if_exception_type(RetryableExchangeError),
        wait=wait_exponential(multiplier=1, min=1, max=8),
        stop=stop_after_attempt(5),
        reraise=True,
    )
    def _public_get(self, path: str, params: dict[str, Any]) -> dict[str, Any]:
        response = self.client.get(path, params=params)
        return self._parse(response)

    @retry(
        retry=retry_if_exception_type(RetryableExchangeError),
        wait=wait_exponential(multiplier=1, min=1, max=8),
        stop=stop_after_attempt(5),
        reraise=True,
    )
    def _private_get(self, path: str, params: dict[str, Any]) -> dict[str, Any]:
        self._ensure_auth()
        qs = _encode_query(params)
        response = self.client.get(path, params=params, headers=self._auth_headers(qs))
        return self._parse(response)

    @retry(
        retry=retry_if_exception_type(RetryableExchangeError),
        wait=wait_exponential(multiplier=1, min=1, max=8),
        stop=stop_after_attempt(5),
        reraise=True,
    )
    def _private_post(self, path: str, payload: dict[str, Any]) -> dict[str, Any]:
        self._ensure_auth()
        body = json.dumps(payload, separators=(",", ":"), sort_keys=True)
        response = self.client.post(path, content=body, headers=self._auth_headers(body))
        return self._parse(response)

    def _auth_headers(self, signature_payload: str) -> dict[str, str]:
        ts = str(self._next_ts())
        return {
            "X-BAPI-API-KEY": self.api_key or "",
            "X-BAPI-TIMESTAMP": ts,
            "X-BAPI-RECV-WINDOW": str(self.config.recv_window_ms),
            "X-BAPI-SIGN": self._sign(ts, signature_payload),
            "Content-Type": "application/json",
        }

    def _sign(self, timestamp: str, payload: str) -> str:
        msg = f"{timestamp}{self.api_key}{self.config.recv_window_ms}{payload}"
        return hmac.new(
            (self.api_secret or "").encode(), msg.encode(), hashlib.sha256
        ).hexdigest()

    def _ensure_auth(self) -> None:
        if not self.api_key or not self.api_secret:
            raise ExchangeAPIError("API key/secret not configured")

    def _next_ts(self) -> int:
        ts = int(time.time() * 1000)
        if ts <= self._last_timestamp_ms:
            ts = self._last_timestamp_ms + 1
        self._last_timestamp_ms = ts
        return ts

    def _parse(self, response: httpx.Response) -> dict[str, Any]:
        if response.status_code in {429, 500, 502, 503, 504}:
            raise RetryableExchangeError(f"HTTP {response.status_code}")
        if response.status_code == 403 and "access too frequent" in response.text.lower():
            raise RetryableExchangeError("HTTP 403 rate limited")
        if response.is_error:
            response.raise_for_status()
        data = response.json()
        code = int(data.get("retCode", 0))
        msg = str(data.get("retMsg", ""))
        if code != 0:
            if code in {10000, 10006, 10016} or any(
                m in msg.lower() for m in ("too many visits", "system busy", "timeout")
            ):
                raise RetryableExchangeError(f"{code}: {msg}")
            raise ExchangeAPIError(f"{code}: {msg}")
        return data.get("result", {})


# ---------------------------------------------------------------------------
# Utilities
# ---------------------------------------------------------------------------

def round_quantity(quantity: float, step: str) -> float:
    return float(Decimal(str(quantity)).quantize(Decimal(step), rounding=ROUND_DOWN))


def format_decimal(value: float, decimals: int) -> str:
    if decimals <= 0:
        return str(int(value))
    return f"{value:.{decimals}f}"


def _precision_from_step(step: str) -> int:
    normalized = step.rstrip("0")
    if "." not in normalized:
        return 0
    return len(normalized.split(".", maxsplit=1)[1])


def _encode_query(params: dict[str, Any]) -> str:
    return urllib.parse.urlencode(sorted((k, str(v)) for k, v in params.items() if v is not None))


def _extract_fee(order: dict[str, Any]) -> float:
    detail = order.get("cumFeeDetail")
    if isinstance(detail, dict):
        return float(sum(float(v) for v in detail.values()))
    return float(order.get("cumExecFee") or 0)
