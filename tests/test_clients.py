from __future__ import annotations

import hashlib
import hmac

import httpx
import pytest

from sniper_bot.ai import _extract_output_text
from sniper_bot.alerts import TelegramNotifier
from sniper_bot.config import ExchangeConfig
from sniper_bot.exchange import BybitClient, InstrumentInfo, RetryableExchangeError


def test_telegram_notifier_sends_message() -> None:
    requests: list[httpx.Request] = []

    def handler(request: httpx.Request) -> httpx.Response:
        requests.append(request)
        return httpx.Response(200, json={"ok": True, "result": {}})

    notifier = TelegramNotifier("token", "chat-id")
    notifier.client = httpx.Client(transport=httpx.MockTransport(handler))
    notifier.send_message("hello")
    assert requests[0].url.path.endswith("/sendMessage")


def test_bybit_parse_response_retries_on_rate_limit() -> None:
    client = BybitClient(ExchangeConfig())
    response = httpx.Response(200, json={"retCode": 10006, "retMsg": "Too many visits!", "result": {}})
    with pytest.raises(RetryableExchangeError):
        client._parse_response(response)  # noqa: SLF001


def test_bybit_auth_headers_include_signature() -> None:
    config = ExchangeConfig()
    client = BybitClient(config, api_key="test-key", api_secret="test-secret")
    client._next_timestamp_ms = lambda: 1_700_000_000_000  # type: ignore[method-assign]
    payload = "category=spot&symbol=BTCUSDT"
    headers = client._auth_headers(payload)  # noqa: SLF001
    expected = hmac.new(
        b"test-secret",
        f"1700000000000test-key{config.recv_window_ms}{payload}".encode("utf-8"),
        hashlib.sha256,
    ).hexdigest()
    assert headers["X-BAPI-API-KEY"] == "test-key"
    assert headers["X-BAPI-SIGN"] == expected


def test_bybit_resolve_instrument_parses_spot_rules() -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        assert request.url.path == "/v5/market/instruments-info"
        return httpx.Response(
            200,
            json={
                "retCode": 0,
                "retMsg": "OK",
                "result": {
                    "list": [
                        {
                            "symbol": "BTCUSDT",
                            "baseCoin": "BTC",
                            "quoteCoin": "USDT",
                            "priceFilter": {"tickSize": "0.1"},
                            "lotSizeFilter": {
                                "basePrecision": "0.000001",
                                "minOrderQty": "0.00001",
                                "minOrderAmt": "5",
                                "maxMarketOrderQty": "50",
                            },
                        }
                    ]
                },
            },
        )

    client = BybitClient(ExchangeConfig())
    client.client = httpx.Client(
        base_url=client.config.base_url,
        headers={"User-Agent": client.config.user_agent},
        transport=httpx.MockTransport(handler),
    )
    instrument = client.resolve_instrument("BTCUSDT")
    assert instrument.base_asset == "BTC"
    assert instrument.quote_asset == "USDT"
    assert instrument.quantity_decimals == 6
    assert instrument.price_decimals == 1


def test_bybit_get_ticker_parses_prices() -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        assert request.url.path == "/v5/market/tickers"
        return httpx.Response(
            200,
            json={
                "retCode": 0,
                "retMsg": "OK",
                "result": {"list": [{"bid1Price": "20499.9", "ask1Price": "20500.1", "lastPrice": "20500.0"}]},
            },
        )

    client = BybitClient(ExchangeConfig())
    client.client = httpx.Client(
        base_url=client.config.base_url,
        headers={"User-Agent": client.config.user_agent},
        transport=httpx.MockTransport(handler),
    )
    quote = client.get_ticker(InstrumentInfo("BTCUSDT", "BTCUSDT", "spot", "BTC", "USDT", "0.000001", "0.1", 6, 1, 0.00001, 5.0, 50.0))
    assert quote.bid == 20499.9
    assert quote.ask == 20500.1
    assert quote.last == 20500.0


def test_bybit_get_balance_parses_unified_wallet() -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        assert request.url.path == "/v5/account/wallet-balance"
        return httpx.Response(
            200,
            json={
                "retCode": 0,
                "retMsg": "OK",
                "result": {
                    "list": [
                        {
                            "coin": [
                                {"coin": "BTC", "walletBalance": "0.02"},
                                {"coin": "USDT", "walletBalance": "1000.5"},
                            ]
                        }
                    ]
                },
            },
        )

    client = BybitClient(ExchangeConfig(), api_key="key", api_secret="secret")
    client.client = httpx.Client(
        base_url=client.config.base_url,
        headers={"User-Agent": client.config.user_agent},
        transport=httpx.MockTransport(handler),
    )
    balances = client.get_balance(["BTC", "USDT"])
    assert balances == {"BTC": 0.02, "USDT": 1000.5}


def test_bybit_wait_for_closed_order_parses_fill() -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        assert request.url.path == "/v5/order/realtime"
        return httpx.Response(
            200,
            json={
                "retCode": 0,
                "retMsg": "OK",
                "result": {
                    "list": [
                        {
                            "orderId": "abc123",
                            "orderStatus": "Filled",
                            "avgPrice": "20500.5",
                            "cumExecQty": "0.01",
                            "cumExecValue": "205.005",
                            "cumExecFee": "0.1",
                        }
                    ]
                },
            },
        )

    client = BybitClient(ExchangeConfig(), api_key="key", api_secret="secret")
    client.client = httpx.Client(
        base_url=client.config.base_url,
        headers={"User-Agent": client.config.user_agent},
        transport=httpx.MockTransport(handler),
    )

    fill = client.wait_for_closed_order(
        InstrumentInfo("BTCUSDT", "BTCUSDT", "spot", "BTC", "USDT", "0.000001", "0.1", 6, 1, 0.00001, 5.0, 50.0),
        "abc123",
        attempts=1,
        sleep_seconds=0,
    )
    assert fill.order_id == "abc123"
    assert fill.status == "filled"
    assert fill.executed_volume == 0.01


def test_extract_output_text_handles_responses_payload() -> None:
    payload = {
        "output": [
            {
                "content": [
                    {
                        "type": "output_text",
                        "text": "{\"status\":\"ok\"}",
                    }
                ]
            }
        ]
    }
    assert _extract_output_text(payload) == "{\"status\":\"ok\"}"
