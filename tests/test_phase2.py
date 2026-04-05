"""Tests for Phase 2 features: ATR stops, correlation, TWAP."""
import pytest
from datetime import datetime, timezone

from sniper_bot.config import PositionConfig, RiskConfig
from sniper_bot.indicators import compute_atr, compute_pearson_correlation, price_returns
from sniper_bot.positions import evaluate_position
from sniper_bot.risk import compute_kelly_pct
from sniper_bot.storage import Position


def _pos(**kw) -> Position:
    p = Position()
    p.mode = "paper"
    p.symbol = kw.get("symbol", "ETHUSDT")
    p.status = "open"
    p.entry_price = kw.get("entry_price", 100.0)
    p.quantity = kw.get("quantity", 1.0)
    p.usdt_invested = kw.get("usdt_invested", 100.0)
    p.max_price = kw.get("max_price", 100.0)
    p.stop_price = kw.get("stop_price", 85.0)
    p.entry_time = kw.get("entry_time", datetime.now(timezone.utc))
    p.atr_at_entry = kw.get("atr_at_entry", None)
    return p


class TestATRStops:
    def test_atr_based_stop_wider_for_volatile(self):
        """High ATR should produce a wider stop."""
        config = PositionConfig(use_atr_stops=True, atr_stop_multiplier=2.5)
        # ATR = 5 on a $100 entry → stop pct = 5*2.5/100 = 12.5%
        pos = _pos(entry_price=100.0, stop_price=80.0, atr_at_entry=5.0)
        evaluate_position(pos, 99.0, config, datetime.now(timezone.utc))
        # Hard stop at 100 * (1 - 0.125) = 87.5, should NOT trigger at 99
        assert pos.stop_price > 80.0  # stop ratcheted from initial

    def test_atr_based_stop_tighter_for_stable(self):
        """Low ATR should produce a tighter stop."""
        config = PositionConfig(use_atr_stops=True, atr_stop_multiplier=2.5, atr_min_stop_pct=0.03)
        # ATR = 0.5 on $100 → stop pct = 0.5*2.5/100 = 1.25%, clamped to min 3%
        pos = _pos(entry_price=100.0, stop_price=90.0, atr_at_entry=0.5)
        evaluate_position(pos, 100.0, config, datetime.now(timezone.utc))
        # New stop = 100 * (1 - 0.03) = 97.0
        assert pos.stop_price == pytest.approx(97.0)

    def test_fallback_to_fixed_when_no_atr(self):
        """When atr_at_entry is None, use fixed stops."""
        config = PositionConfig(use_atr_stops=True, trailing_stop_pct=0.15, trail_tighten_gain_pct=0.50)
        pos = _pos(entry_price=100.0, max_price=100.0, stop_price=80.0, atr_at_entry=None)
        evaluate_position(pos, 100.0, config, datetime.now(timezone.utc))
        assert pos.stop_price == 85.0  # 100 * 0.85


class TestATRComputation:
    def test_atr_basic(self):
        candles = [
            {"high": 105, "low": 95, "close": 100},
            {"high": 106, "low": 94, "close": 101},
            {"high": 107, "low": 93, "close": 99},
            {"high": 108, "low": 92, "close": 102},
            {"high": 104, "low": 96, "close": 100},
        ] * 4  # 20 candles
        result = compute_atr(candles, period=14)
        assert result is not None
        assert result > 0

    def test_atr_insufficient(self):
        assert compute_atr([{"high": 10, "low": 9, "close": 9.5}] * 3, period=14) is None


class TestCorrelation:
    def test_perfect_positive(self):
        x = [0.01, 0.02, -0.01, 0.03, -0.02, 0.01]
        y = [0.01, 0.02, -0.01, 0.03, -0.02, 0.01]
        assert compute_pearson_correlation(x, y) == pytest.approx(1.0)

    def test_perfect_negative(self):
        x = [0.01, 0.02, -0.01, 0.03, -0.02, 0.01]
        y = [-0.01, -0.02, 0.01, -0.03, 0.02, -0.01]
        assert compute_pearson_correlation(x, y) == pytest.approx(-1.0)

    def test_insufficient_data(self):
        assert compute_pearson_correlation([1, 2], [3, 4]) is None

    def test_price_returns(self):
        candles = [{"close": 100}, {"close": 102}, {"close": 101}]
        rets = price_returns(candles)
        assert len(rets) == 2
        assert rets[0] == pytest.approx(0.02)
        assert rets[1] == pytest.approx(-0.0098, abs=0.001)


class TestKelly:
    def test_profitable_system(self):
        # 60% win rate, avg win 2x avg loss → Kelly = 0.6 - 0.4/2 = 0.4
        # Quarter Kelly = 0.4 * 0.25 = 0.10
        result = compute_kelly_pct(0.6, 0.04, 0.02, fraction=0.25)
        assert result == pytest.approx(0.10)

    def test_losing_system(self):
        # 30% win rate, equal win/loss → Kelly = 0.3 - 0.7/1 = -0.4 → 0
        result = compute_kelly_pct(0.3, 0.02, 0.02, fraction=0.25)
        assert result == 0.0

    def test_zero_inputs(self):
        assert compute_kelly_pct(0, 0, 0) == 0.0
