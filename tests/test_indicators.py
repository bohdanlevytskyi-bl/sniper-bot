import pytest

from sniper_bot.indicators import (
    compute_bollinger_bands,
    compute_funding_rate_signal,
    compute_macd,
    compute_obi_score,
    compute_rsi,
    compute_ta_composite,
)


def _candles(closes: list[float]) -> list[dict]:
    return [{"close": c, "open": c, "high": c, "low": c, "volume": 100.0} for c in closes]


class TestRSI:
    def test_insufficient_data(self):
        assert compute_rsi(_candles([1, 2, 3]), period=14) is None

    def test_all_gains(self):
        # Monotonically increasing → RSI = 100
        closes = list(range(100, 120))
        result = compute_rsi(_candles(closes), period=14)
        assert result == 100.0

    def test_all_losses(self):
        # Monotonically decreasing → RSI = 0
        closes = list(range(120, 100, -1))
        result = compute_rsi(_candles(closes), period=14)
        assert result == 0.0

    def test_mixed(self):
        closes = [100, 102, 101, 103, 102, 104, 103, 105, 104, 106, 105, 107, 106, 108, 107, 109]
        result = compute_rsi(_candles(closes), period=14)
        assert result is not None
        assert 0 < result < 100


class TestMACD:
    def test_insufficient_data(self):
        assert compute_macd(_candles([1] * 10)) is None

    def test_returns_dict(self):
        # Need 26 + 9 = 35 data points
        closes = [100 + i * 0.5 for i in range(40)]
        result = compute_macd(_candles(closes))
        assert result is not None
        assert "macd" in result
        assert "signal" in result
        assert "histogram" in result

    def test_uptrend_positive_macd(self):
        closes = [100 + i for i in range(40)]
        result = compute_macd(_candles(closes))
        assert result is not None
        assert result["macd"] > 0


class TestBollingerBands:
    def test_insufficient_data(self):
        assert compute_bollinger_bands(_candles([1] * 5)) is None

    def test_basic(self):
        closes = [100 + (i % 5) for i in range(25)]
        result = compute_bollinger_bands(_candles(closes))
        assert result is not None
        assert result["upper"] > result["middle"] > result["lower"]
        assert 0 <= result["pct_b"] <= 2  # can slightly exceed 0-1

    def test_constant_prices(self):
        result = compute_bollinger_bands(_candles([100.0] * 25))
        assert result is not None
        assert result["bandwidth"] == 0  # no volatility
        assert result["pct_b"] == 0.5  # default neutral with 0 bandwidth... actually division by zero


class TestOBI:
    def test_balanced(self):
        bids = [[100.0, 10.0], [99.0, 10.0]]
        asks = [[101.0, 10.0], [102.0, 10.0]]
        result = compute_obi_score(bids, asks)
        assert -0.1 < result < 0.1  # roughly balanced (slight price asymmetry)

    def test_heavy_bids(self):
        bids = [[100.0, 100.0]]
        asks = [[101.0, 1.0]]
        result = compute_obi_score(bids, asks)
        assert result > 0.8

    def test_heavy_asks(self):
        bids = [[100.0, 1.0]]
        asks = [[101.0, 100.0]]
        result = compute_obi_score(bids, asks)
        assert result < -0.8

    def test_empty(self):
        assert compute_obi_score([], []) == 0.0


class TestFundingRate:
    def test_positive_rate_bearish(self):
        # High positive funding → bearish signal (negative)
        result = compute_funding_rate_signal(0.001)
        assert result == -1.0

    def test_negative_rate_bullish(self):
        # High negative funding → bullish signal (positive)
        result = compute_funding_rate_signal(-0.001)
        assert result == 1.0

    def test_zero_neutral(self):
        assert compute_funding_rate_signal(0.0) == 0.0


class TestTAComposite:
    def test_all_none(self):
        assert compute_ta_composite(None, None, None) == 0.5

    def test_oversold_bullish(self):
        result = compute_ta_composite(25, {"macd": 0.1, "signal": 0.05, "histogram": 0.05}, {"pct_b": 0.15})
        assert result > 0.7

    def test_overbought_bearish(self):
        result = compute_ta_composite(75, {"macd": -0.1, "signal": 0.05, "histogram": -0.15}, {"pct_b": 0.9})
        assert result < 0.2
