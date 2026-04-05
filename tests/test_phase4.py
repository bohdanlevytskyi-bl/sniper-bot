"""Tests for Phase 4 features: WebSocket, dashboard, alerts, multi-exchange."""
import pytest
from datetime import datetime, timezone
from unittest.mock import MagicMock

from sniper_bot.alerts import TelegramNotifier, build_daily_summary, format_alert
from sniper_bot.ws_stream import TickerCache, TradeCache
from sniper_bot.exchange_base import ExchangeFactory, register_builtin_exchanges


class TestTickerCache:
    def test_update_and_get(self):
        cache = TickerCache()
        cache.update("BTCUSDT", {"last_price": 50000, "volume_24h": 1000})
        result = cache.get("BTCUSDT")
        assert result is not None
        assert result["last_price"] == 50000

    def test_get_missing(self):
        cache = TickerCache()
        assert cache.get("NOTEXIST") is None

    def test_get_all(self):
        cache = TickerCache()
        cache.update("BTCUSDT", {"price": 50000})
        cache.update("ETHUSDT", {"price": 3000})
        all_data = cache.get_all()
        assert len(all_data) == 2
        assert "BTCUSDT" in all_data
        assert "ETHUSDT" in all_data

    def test_symbol_count(self):
        cache = TickerCache()
        assert cache.symbol_count == 0
        cache.update("A", {})
        cache.update("B", {})
        assert cache.symbol_count == 2

    def test_last_update(self):
        cache = TickerCache()
        assert cache.last_update is None
        cache.update("A", {})
        assert cache.last_update is not None


class TestTradeCache:
    def test_add_and_get(self):
        cache = TradeCache()
        cache.add_trade("BTCUSDT", {"price": 50000, "qty": 1, "side": "buy"})
        cache.add_trade("BTCUSDT", {"price": 50100, "qty": 2, "side": "sell"})
        recent = cache.get_recent("BTCUSDT")
        assert len(recent) == 2

    def test_max_limit(self):
        cache = TradeCache()
        cache._max_per_symbol = 10
        for i in range(20):
            cache.add_trade("SYM", {"price": i, "qty": 1})
        recent = cache.get_recent("SYM")
        assert len(recent) == 10

    def test_get_empty(self):
        cache = TradeCache()
        assert cache.get_recent("NOTHING") == []

    def test_clear(self):
        cache = TradeCache()
        cache.add_trade("A", {"price": 1})
        cache.clear("A")
        assert cache.get_recent("A") == []


class TestFormatAlert:
    def test_basic_format(self):
        result = format_alert("Test Title", ["Line 1", "Line 2"])
        assert "<b>Test Title</b>" in result
        assert "Line 1" in result
        assert "Line 2" in result

    def test_empty_lines(self):
        result = format_alert("Title", [])
        assert "<b>Title</b>" in result


class TestExchangeFactory:
    def test_register_and_create(self):
        class MockExchange:
            def __init__(self, **kwargs):
                self.kwargs = kwargs

        ExchangeFactory.register("mock_test", MockExchange)
        client = ExchangeFactory.create("mock_test", param1="value")
        assert isinstance(client, MockExchange)

    def test_unknown_exchange_raises(self):
        with pytest.raises(ValueError, match="Unknown exchange"):
            ExchangeFactory.create("nonexistent_exchange_xyz")

    def test_available_lists_registered(self):
        ExchangeFactory.register("test_avail", type("Dummy", (), {}))
        assert "test_avail" in ExchangeFactory.available()

    def test_register_builtin(self):
        register_builtin_exchanges()
        assert "bybit" in ExchangeFactory.available()


class TestStrategyWithNewSignals:
    """Test that the 10-factor scoring works correctly."""

    def test_all_neutral_signals(self):
        from sniper_bot.config import StrategyConfig
        from sniper_bot.strategy import score_candidate

        config = StrategyConfig()
        result = score_candidate(
            "TEST", 100.0, 0.0, {}, 0.0, config,
            ta_composite=0.5, obi_value=0.0, funding_signal=0.0,
            whale_signal=0.0, vwap_deviation=0.0,
            mtf_confluence=0.5, microstructure=0.5,
        )
        # All neutral: score should be moderate
        assert 0.1 < result.composite_score < 0.6
        assert result.whale_score == 0.5  # neutral
        assert result.vwap_score == 0.5   # neutral
        assert result.mtf_score == 0.5    # neutral

    def test_all_bullish_signals(self):
        from sniper_bot.config import StrategyConfig
        from sniper_bot.strategy import score_candidate

        config = StrategyConfig()
        result = score_candidate(
            "BULL", 100.0, 10.0,
            {"5m": 0.05, "15m": 0.1, "60m": 0.2},
            -0.05, config,
            ta_composite=0.9, obi_value=0.8, funding_signal=0.8,
            whale_signal=0.9, vwap_deviation=-0.03,
            mtf_confluence=0.9, microstructure=0.9,
        )
        assert result.composite_score > 0.7

    def test_whale_score_positive_for_buying(self):
        from sniper_bot.config import StrategyConfig
        from sniper_bot.strategy import score_candidate

        config = StrategyConfig()
        result = score_candidate(
            "WHALE", 100.0, 5.0, {"60m": 0.05}, 0.0, config,
            whale_signal=0.8,
        )
        assert result.whale_score > 0.5  # positive whale = bullish

    def test_vwap_below_is_bullish(self):
        from sniper_bot.config import StrategyConfig
        from sniper_bot.strategy import score_candidate

        config = StrategyConfig()
        # Negative VWAP deviation = price below VWAP = bullish for mean reversion
        result = score_candidate(
            "VWAP", 100.0, 5.0, {"60m": 0.05}, 0.0, config,
            vwap_deviation=-0.03,
        )
        assert result.vwap_score > 0.5
