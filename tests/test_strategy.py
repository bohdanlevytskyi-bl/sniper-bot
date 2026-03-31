from sniper_bot.config import StrategyConfig
from sniper_bot.strategy import compute_price_changes, rank_candidates, score_candidate


def test_score_high_volume_spike():
    config = StrategyConfig()
    result = score_candidate("ETHUSDT", 3000.0, 8.0, {"5m": 0.02, "15m": 0.05, "60m": 0.08}, 0.01, config)
    assert result.composite_score > 0.3
    assert result.volume_score > 0.7


def test_score_zero_everything():
    config = StrategyConfig()
    result = score_candidate("XUSDT", 1.0, 0.0, {}, 0.0, config)
    # TA/OBI/funding default to neutral (0.5) so score is not fully zero
    assert result.volume_score == 0.0
    assert result.momentum_score == 0.0
    assert result.relative_strength_score == 0.0
    # Composite reflects neutral TA+OBI+funding contributions
    assert result.composite_score < 0.3


def test_score_capped_at_one():
    config = StrategyConfig()
    result = score_candidate("XUSDT", 1.0, 100.0, {"5m": 1.0, "15m": 1.0, "60m": 1.0}, -0.5, config)
    assert result.composite_score <= 1.0
    assert result.volume_score <= 1.0
    assert result.momentum_score <= 1.0


def test_rank_filters_below_threshold():
    config = StrategyConfig(min_entry_score=0.5)
    scored = [
        score_candidate("A", 1.0, 8.0, {"5m": 0.05, "15m": 0.1, "60m": 0.2}, 0.0, config),
        score_candidate("B", 1.0, 0.5, {}, 0.0, config),
    ]
    ranked = rank_candidates(scored, config)
    assert all(s.composite_score >= 0.5 for s in ranked)


def test_rank_sorts_descending():
    config = StrategyConfig(min_entry_score=0.0)
    a = score_candidate("A", 1.0, 5.0, {"5m": 0.01}, 0.0, config)
    b = score_candidate("B", 1.0, 9.0, {"5m": 0.05, "15m": 0.1, "60m": 0.2}, 0.0, config)
    ranked = rank_candidates([a, b], config)
    assert ranked[0].symbol == "B"


def test_compute_price_changes_basic():
    candles_5m = [
        {"close": 100.0},
        {"close": 101.0},
        {"close": 102.0},
        {"close": 103.0},
    ]
    candles_1h = [
        {"close": 95.0},
        {"close": 103.0},
    ]
    changes = compute_price_changes(candles_5m, candles_1h)
    assert "5m" in changes
    assert "15m" in changes
    assert "60m" in changes
    assert changes["5m"] > 0
    assert changes["60m"] > 0


def test_compute_price_changes_empty():
    assert compute_price_changes([], []) == {}
