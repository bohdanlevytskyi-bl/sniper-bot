from __future__ import annotations

from datetime import datetime, timedelta, timezone
from pathlib import Path

import pandas as pd
import pytest

from sniper_bot.app import _run_backtest, run_bot
from sniper_bot.config import AppConfig


def _frame(periods: int = 260) -> pd.DataFrame:
    rows = []
    start = datetime(2026, 1, 1, tzinfo=timezone.utc)
    price = 20_000.0
    for index in range(periods):
        price += 15
        rows.append(
            {
                "open_time": start + timedelta(hours=index),
                "open": price - 5,
                "high": price + 10,
                "low": price - 10,
                "close": price,
                "volume": 7.5,
            }
        )
    return pd.DataFrame(rows)


def test_backtest_produces_metrics() -> None:
    metrics = _run_backtest(AppConfig(), _frame())
    assert metrics.trade_count >= 0
    assert metrics.max_drawdown_pct >= 0


def test_live_mode_requires_confirm_live() -> None:
    with pytest.raises(RuntimeError, match="--confirm-live"):
        run_bot(Path("config/example.yaml"), mode="live", once=True, confirm_live=False)


def test_demo_mode_requires_demo_config(tmp_path: Path) -> None:
    config_path = tmp_path / "config.yaml"
    config_path.write_text(
        "\n".join(
            [
                "mode: paper",
                "pair: BTCUSDT",
                "timeframe_minutes: 60",
                "exchange:",
                "  environment: demo",
                "  account_type: UNIFIED",
            ]
        ),
        encoding="utf-8",
    )
    with pytest.raises(RuntimeError, match="mode: demo"):
        run_bot(config_path, mode="demo", once=True)


def test_live_mode_requires_live_environment(tmp_path: Path) -> None:
    config_path = tmp_path / "config.yaml"
    config_path.write_text(
        "\n".join(
            [
                "mode: live",
                "pair: BTCUSDT",
                "timeframe_minutes: 60",
                "exchange:",
                "  environment: demo",
                "  account_type: UNIFIED",
            ]
        ),
        encoding="utf-8",
    )
    with pytest.raises(RuntimeError, match="exchange.environment: live"):
        run_bot(config_path, mode="live", once=True, confirm_live=True)
