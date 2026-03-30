from sniper_bot.config import ScannerConfig
from sniper_bot.exchange import TickerData
from sniper_bot.scanner import compute_volume_spike, scan_market


def _ticker(symbol="ETHUSDT", last=3000.0, vol=100000.0, turnover=300_000_000.0, change=0.05, **kw):
    return TickerData(
        symbol=symbol,
        last_price=last,
        volume_24h=vol,
        turnover_24h=turnover,
        price_change_24h_pct=change,
        high_24h=kw.get("high", last * 1.05),
        low_24h=kw.get("low", last * 0.95),
        bid=last - 0.1,
        ask=last + 0.1,
    )


def test_scan_filters_non_usdt():
    config = ScannerConfig()
    tickers = [_ticker("ETHBTC")]
    assert scan_market(tickers, config) == []


def test_scan_filters_stablecoins():
    config = ScannerConfig()
    tickers = [_ticker("USDCUSDT")]
    assert scan_market(tickers, config) == []


def test_scan_filters_excluded_pairs():
    config = ScannerConfig(excluded_pairs=["ETHUSDT"])
    tickers = [_ticker("ETHUSDT")]
    assert scan_market(tickers, config) == []


def test_scan_filters_low_volume():
    config = ScannerConfig(min_turnover_24h_usd=1_000_000)
    tickers = [_ticker("ETHUSDT", turnover=500_000)]
    assert scan_market(tickers, config) == []


def test_scan_filters_extreme_movers():
    config = ScannerConfig(max_price_change_24h_pct=0.50)
    tickers = [_ticker("ETHUSDT", change=0.60)]
    assert scan_market(tickers, config) == []


def test_scan_passes_valid_ticker():
    config = ScannerConfig()
    tickers = [_ticker("ETHUSDT")]
    result = scan_market(tickers, config)
    assert len(result) == 1
    assert result[0].symbol == "ETHUSDT"


def test_scan_sorts_by_turnover():
    config = ScannerConfig()
    tickers = [
        _ticker("AAVEUSDT", turnover=200_000),
        _ticker("ETHUSDT", turnover=500_000),
    ]
    result = scan_market(tickers, config)
    assert result[0].symbol == "ETHUSDT"


def test_volume_spike_ratio():
    candles = [{"volume": 100}, {"volume": 120}, {"volume": 110}]
    avg_per_bar = 50.0
    ratio = compute_volume_spike(candles, avg_per_bar)
    assert ratio > 2.0


def test_volume_spike_zero_baseline():
    assert compute_volume_spike([{"volume": 100}], 0.0) == 0.0


def test_volume_spike_empty_candles():
    assert compute_volume_spike([], 50.0) == 0.0
