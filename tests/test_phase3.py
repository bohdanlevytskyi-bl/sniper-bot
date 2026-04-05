"""Tests for Phase 3 features: whale detection, VWAP, multi-timeframe, microstructure."""
import pytest

from sniper_bot.indicators import (
    compute_multi_timeframe_score,
    compute_spread_signal,
    compute_trade_flow_toxicity,
    compute_vwap,
    detect_whale_trades,
)


class TestWhaleDetection:
    def test_no_whales_in_uniform_trades(self):
        trades = [{"price": 100, "qty": 1, "side": "buy", "value": 100}] * 20
        result = detect_whale_trades(trades, std_multiplier=3.0)
        assert result["whale_count"] == 0
        assert result["whale_score"] == 0.0

    def test_whale_buy_detected(self):
        trades = [{"price": 100, "qty": 1, "side": "buy", "value": 100}] * 19
        # Add one massive buy
        trades.append({"price": 100, "qty": 100, "side": "buy", "value": 10000})
        result = detect_whale_trades(trades, std_multiplier=2.0)
        assert result["whale_count"] >= 1
        assert result["whale_score"] > 0  # net buying

    def test_whale_sell_detected(self):
        trades = [{"price": 100, "qty": 1, "side": "sell", "value": 100}] * 19
        trades.append({"price": 100, "qty": 100, "side": "sell", "value": 10000})
        result = detect_whale_trades(trades, std_multiplier=2.0)
        assert result["whale_count"] >= 1
        assert result["whale_score"] < 0  # net selling

    def test_insufficient_data(self):
        trades = [{"price": 100, "qty": 1, "side": "buy", "value": 100}] * 5
        result = detect_whale_trades(trades)
        assert result["whale_score"] == 0.0

    def test_balanced_whale_activity(self):
        trades = [{"price": 100, "qty": 1, "side": "buy", "value": 100}] * 18
        trades.append({"price": 100, "qty": 100, "side": "buy", "value": 10000})
        trades.append({"price": 100, "qty": 100, "side": "sell", "value": 10000})
        result = detect_whale_trades(trades, std_multiplier=2.0)
        # Should be roughly balanced
        assert abs(result["whale_score"]) < 0.2


class TestVWAP:
    def test_basic_vwap(self):
        candles = [
            {"high": 105, "low": 95, "close": 100, "volume": 1000},
            {"high": 106, "low": 96, "close": 101, "volume": 1200},
            {"high": 107, "low": 97, "close": 102, "volume": 800},
            {"high": 108, "low": 98, "close": 103, "volume": 1100},
            {"high": 109, "low": 99, "close": 104, "volume": 900},
        ]
        result = compute_vwap(candles)
        assert result is not None
        assert result["vwap"] > 0
        assert "deviation_pct" in result

    def test_price_above_vwap(self):
        # All candles same except last one jumps up
        candles = [
            {"high": 101, "low": 99, "close": 100, "volume": 1000},
            {"high": 101, "low": 99, "close": 100, "volume": 1000},
            {"high": 101, "low": 99, "close": 100, "volume": 1000},
            {"high": 101, "low": 99, "close": 100, "volume": 1000},
            {"high": 120, "low": 100, "close": 115, "volume": 1000},
        ]
        result = compute_vwap(candles)
        assert result is not None
        assert result["deviation_pct"] > 0  # price above VWAP

    def test_insufficient_data(self):
        candles = [{"high": 10, "low": 9, "close": 9.5, "volume": 100}] * 3
        assert compute_vwap(candles) is None

    def test_zero_volume(self):
        candles = [
            {"high": 105, "low": 95, "close": 100, "volume": 0},
        ] * 5
        assert compute_vwap(candles) is None


class TestMultiTimeframe:
    def test_all_bullish(self):
        signals = {"5m": 0.8, "15m": 0.7, "1h": 0.9, "4h": 0.85}
        result = compute_multi_timeframe_score(signals)
        assert result > 0.7

    def test_all_bearish(self):
        signals = {"5m": 0.2, "15m": 0.3, "1h": 0.1, "4h": 0.15}
        result = compute_multi_timeframe_score(signals)
        assert result < 0.3

    def test_mixed_signals(self):
        signals = {"5m": 0.9, "1h": 0.2}  # short bullish, long bearish
        result = compute_multi_timeframe_score(signals)
        assert 0.2 < result < 0.8

    def test_longer_tf_weighted_more(self):
        # 4h bearish should pull score down more than 5m bullish pushes it up
        mostly_bear = {"5m": 0.9, "4h": 0.1}
        result = compute_multi_timeframe_score(mostly_bear)
        assert result < 0.5

    def test_empty_signals(self):
        assert compute_multi_timeframe_score({}) == 0.5

    def test_single_timeframe(self):
        result = compute_multi_timeframe_score({"1h": 0.7})
        assert result == pytest.approx(0.7)


class TestSpreadSignal:
    def test_tight_spread(self):
        # 0.01% spread
        score = compute_spread_signal(99.99, 100.0, 100.0)
        assert score > 0.9

    def test_wide_spread(self):
        # 1% spread
        score = compute_spread_signal(99.0, 100.0, 100.0)
        assert score < 0.5

    def test_very_wide_spread(self):
        score = compute_spread_signal(98.0, 100.0, 100.0)
        assert score == 0.1

    def test_zero_price(self):
        assert compute_spread_signal(0, 0, 0) == 0.5


class TestTradeFlowToxicity:
    def test_balanced_flow(self):
        trades = [
            {"side": "buy", "value": 100},
            {"side": "sell", "value": 100},
        ] * 10
        toxicity = compute_trade_flow_toxicity(trades)
        assert toxicity < 0.1

    def test_all_buys(self):
        trades = [{"side": "buy", "value": 100}] * 20
        toxicity = compute_trade_flow_toxicity(trades)
        assert toxicity == pytest.approx(1.0)

    def test_all_sells(self):
        trades = [{"side": "sell", "value": 100}] * 20
        toxicity = compute_trade_flow_toxicity(trades)
        assert toxicity == pytest.approx(1.0)

    def test_insufficient_data(self):
        trades = [{"side": "buy", "value": 100}] * 5
        assert compute_trade_flow_toxicity(trades) == 0.0

    def test_moderate_imbalance(self):
        trades = [{"side": "buy", "value": 100}] * 14 + [{"side": "sell", "value": 100}] * 6
        toxicity = compute_trade_flow_toxicity(trades)
        assert 0.3 < toxicity < 0.5
