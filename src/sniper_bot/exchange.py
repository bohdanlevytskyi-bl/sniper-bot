from __future__ import annotations

import hashlib
import hmac
import json
import time
import urllib.parse
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
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
    display_name: str
    symbol: str
    category: str
    base_asset: str
    quote_asset: str
    quantity_step: str
    price_step: str
    quantity_decimals: int
    price_decimals: int
    min_order_quantity: float
    min_order_amount: float
    max_market_order_quantity: float


@dataclass(slots=True)
class Quote:
    bid: float
    ask: float
    last: float
    as_of: datetime


@dataclass(slots=True)
class OrderFillInfo:
    order_id: str
    status: str
    avg_price: float
    executed_volume: float
    fee: float
    cost: float
    raw: dict[str, Any]


class BybitClient:
    def __init__(self, config: ExchangeConfig, api_key: str | None = None, api_secret: str | None = None):
        self.config = config
        self.api_key = api_key
        self.api_secret = api_secret
        self.client = httpx.Client(
            base_url=config.base_url,
            timeout=config.timeout_seconds,
            headers={"User-Agent": config.user_agent},
        )
        self._last_timestamp_ms = 0

    def close(self) -> None:
        self.client.close()

    def resolve_instrument(self, display_symbol: str) -> InstrumentInfo:
        result = self._public_get(
            "/v5/market/instruments-info",
            {"category": "spot", "symbol": display_symbol.upper()},
        )
        instruments = result.get("list", [])
        if not instruments:
            raise ExchangeAPIError(f"Unable to resolve Bybit instrument for {display_symbol}")
        instrument = instruments[0]
        lot_size = instrument.get("lotSizeFilter", {})
        price_filter = instrument.get("priceFilter", {})
        quantity_step = str(lot_size.get("basePrecision") or lot_size.get("qtyStep") or "0.00000001")
        price_step = str(price_filter.get("tickSize") or "0.01")
        return InstrumentInfo(
            display_name=display_symbol.upper(),
            symbol=instrument.get("symbol", display_symbol.upper()),
            category="spot",
            base_asset=instrument.get("baseCoin", "BTC"),
            quote_asset=instrument.get("quoteCoin", "USDT"),
            quantity_step=quantity_step,
            price_step=price_step,
            quantity_decimals=_precision_from_step(quantity_step),
            price_decimals=_precision_from_step(price_step),
            min_order_quantity=float(lot_size.get("minOrderQty") or 0.0),
            min_order_amount=float(lot_size.get("minOrderAmt") or 0.0),
            max_market_order_quantity=float(lot_size.get("maxMarketOrderQty") or 0.0),
        )

    def fetch_ohlc(self, instrument: InstrumentInfo, interval_minutes: int, since: int | None = None) -> tuple[list[dict], str | None]:
        params: dict[str, Any] = {
            "category": instrument.category,
            "symbol": instrument.symbol,
            "interval": str(interval_minutes),
            "limit": min(self.config.max_public_candles, 1_000),
        }
        if since is not None:
            params["start"] = since
        result = self._public_get("/v5/market/kline", params)
        candles_raw = result.get("list", [])
        candles: list[dict[str, Any]] = []
        for item in reversed(candles_raw):
            open_time = datetime.fromtimestamp(int(item[0]) / 1000, tz=timezone.utc)
            candles.append(
                {
                    "open_time": open_time,
                    "open": float(item[1]),
                    "high": float(item[2]),
                    "low": float(item[3]),
                    "close": float(item[4]),
                    "volume": float(item[5]),
                    "trades": 0,
                    "source": "bybit",
                }
            )
        return candles, result.get("nextPageCursor")

    def fetch_closed_ohlc(self, instrument: InstrumentInfo, interval_minutes: int, since: int | None = None) -> tuple[list[dict], str | None]:
        candles, cursor = self.fetch_ohlc(instrument, interval_minutes, since)
        now = datetime.now(timezone.utc)
        closed = [
            candle
            for candle in candles
            if candle["open_time"] + timedelta(minutes=interval_minutes) <= now
        ]
        return closed, cursor

    def get_ticker(self, instrument: InstrumentInfo) -> Quote:
        result = self._public_get(
            "/v5/market/tickers",
            {"category": instrument.category, "symbol": instrument.symbol},
        )
        tickers = result.get("list", [])
        if not tickers:
            raise ExchangeAPIError(f"Ticker data missing for {instrument.symbol}")
        ticker = tickers[0]
        return Quote(
            bid=float(ticker.get("bid1Price") or 0.0),
            ask=float(ticker.get("ask1Price") or 0.0),
            last=float(ticker.get("lastPrice") or 0.0),
            as_of=datetime.now(timezone.utc),
        )

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
            symbol = coin.get("coin")
            if not symbol:
                continue
            balances[symbol] = float(coin.get("walletBalance") or 0.0)
        return balances

    def add_market_order(self, instrument: InstrumentInfo, side: str, quantity: float) -> str:
        rounded_qty = round_quantity(quantity, instrument.quantity_step)
        payload = {
            "category": instrument.category,
            "symbol": instrument.symbol,
            "side": "Buy" if side == "buy" else "Sell",
            "orderType": "Market",
            "qty": format_decimal(rounded_qty, instrument.quantity_decimals),
            "marketUnit": "baseCoin",
            "isLeverage": 0,
            "orderFilter": "Order",
        }
        result = self._private_post("/v5/order/create", payload)
        order_id = result.get("orderId")
        if not order_id:
            raise ExchangeAPIError("Bybit order response did not include orderId")
        return order_id

    def wait_for_closed_order(
        self,
        instrument: InstrumentInfo,
        order_id: str,
        attempts: int = 15,
        sleep_seconds: float = 1.5,
    ) -> OrderFillInfo:
        for _ in range(attempts):
            result = self._private_get(
                "/v5/order/realtime",
                {"category": instrument.category, "symbol": instrument.symbol, "orderId": order_id},
            )
            orders = result.get("list", [])
            if not orders:
                time.sleep(sleep_seconds)
                continue
            order = orders[0]
            status = order.get("orderStatus", "Unknown")
            if status in {"Filled", "Cancelled", "PartiallyFilledCanceled", "Rejected", "Deactivated"}:
                executed_volume = float(order.get("cumExecQty") or 0.0)
                cost = float(order.get("cumExecValue") or 0.0)
                avg_price = float(order.get("avgPrice") or 0.0) or (cost / executed_volume if executed_volume else 0.0)
                return OrderFillInfo(
                    order_id=order_id,
                    status=status.lower(),
                    avg_price=avg_price,
                    executed_volume=executed_volume,
                    fee=_extract_fee(order),
                    cost=cost,
                    raw=order,
                )
            time.sleep(sleep_seconds)
        raise RetryableExchangeError(f"Timed out waiting for Bybit order {order_id} to close")

    @retry(
        retry=retry_if_exception_type(RetryableExchangeError),
        wait=wait_exponential(multiplier=1, min=1, max=8),
        stop=stop_after_attempt(5),
        reraise=True,
    )
    def _public_get(self, path: str, params: dict[str, Any]) -> dict[str, Any]:
        response = self.client.get(path, params=params)
        return self._parse_response(response)

    @retry(
        retry=retry_if_exception_type(RetryableExchangeError),
        wait=wait_exponential(multiplier=1, min=1, max=8),
        stop=stop_after_attempt(5),
        reraise=True,
    )
    def _private_get(self, path: str, params: dict[str, Any]) -> dict[str, Any]:
        self._ensure_private_config()
        query_string = _encode_query(params)
        response = self.client.get(path, params=params, headers=self._auth_headers(query_string))
        return self._parse_response(response)

    @retry(
        retry=retry_if_exception_type(RetryableExchangeError),
        wait=wait_exponential(multiplier=1, min=1, max=8),
        stop=stop_after_attempt(5),
        reraise=True,
    )
    def _private_post(self, path: str, payload: dict[str, Any]) -> dict[str, Any]:
        self._ensure_private_config()
        body = json.dumps(payload, separators=(",", ":"), sort_keys=True)
        response = self.client.post(path, content=body, headers=self._auth_headers(body))
        return self._parse_response(response)

    def _auth_headers(self, signature_payload: str) -> dict[str, str]:
        timestamp = str(self._next_timestamp_ms())
        sign = self._sign(timestamp, signature_payload)
        return {
            "X-BAPI-API-KEY": self.api_key or "",
            "X-BAPI-TIMESTAMP": timestamp,
            "X-BAPI-RECV-WINDOW": str(self.config.recv_window_ms),
            "X-BAPI-SIGN": sign,
            "Content-Type": "application/json",
        }

    def _sign(self, timestamp: str, signature_payload: str) -> str:
        if not self.api_secret or not self.api_key:
            raise ExchangeAPIError("Bybit private API key/secret not configured")
        payload = f"{timestamp}{self.api_key}{self.config.recv_window_ms}{signature_payload}"
        return hmac.new(
            self.api_secret.encode("utf-8"),
            payload.encode("utf-8"),
            hashlib.sha256,
        ).hexdigest()

    def _ensure_private_config(self) -> None:
        if not self.api_key or not self.api_secret:
            raise ExchangeAPIError("Bybit private API key/secret not configured")

    def _next_timestamp_ms(self) -> int:
        timestamp = int(time.time() * 1000)
        if timestamp <= self._last_timestamp_ms:
            timestamp = self._last_timestamp_ms + 1
        self._last_timestamp_ms = timestamp
        return timestamp

    def _parse_response(self, response: httpx.Response) -> dict[str, Any]:
        if response.status_code in {429, 500, 502, 503, 504}:
            raise RetryableExchangeError(f"Bybit HTTP {response.status_code}")
        if response.status_code == 403 and "access too frequent" in response.text.lower():
            raise RetryableExchangeError("Bybit HTTP 403 access too frequent")
        if response.is_error:
            response.raise_for_status()
        payload = response.json()
        ret_code = int(payload.get("retCode", 0))
        ret_msg = str(payload.get("retMsg", ""))
        if ret_code != 0:
            if ret_code in {10000, 10006, 10016} or any(
                marker in ret_msg.lower()
                for marker in ("too many visits", "service is restarting", "system busy", "timeout")
            ):
                raise RetryableExchangeError(f"{ret_code}: {ret_msg}")
            raise ExchangeAPIError(f"{ret_code}: {ret_msg}")
        return payload.get("result", {})


def round_quantity(quantity: float, step: str) -> float:
    quantized = _decimal_quantize(Decimal(str(quantity)), Decimal(step))
    return float(quantized)


def format_decimal(value: float, decimals: int) -> str:
    if decimals <= 0:
        return str(int(value))
    return f"{value:.{decimals}f}"


def dump_payload(data: dict[str, Any]) -> str:
    return json.dumps(data, ensure_ascii=True, sort_keys=True)


def _precision_from_step(step: str) -> int:
    normalized = step.rstrip("0")
    if "." not in normalized:
        return 0
    return len(normalized.split(".", maxsplit=1)[1])


def _decimal_quantize(value: Decimal, step: Decimal) -> Decimal:
    return value.quantize(step, rounding=ROUND_DOWN)


def _encode_query(params: dict[str, Any]) -> str:
    items = sorted((key, str(value)) for key, value in params.items() if value is not None)
    return urllib.parse.urlencode(items)


def _extract_fee(order: dict[str, Any]) -> float:
    fee_detail = order.get("cumFeeDetail")
    if isinstance(fee_detail, dict):
        return float(sum(float(value) for value in fee_detail.values()))
    return float(order.get("cumExecFee") or 0.0)
