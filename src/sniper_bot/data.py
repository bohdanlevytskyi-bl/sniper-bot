from __future__ import annotations

from datetime import datetime, timezone
from pathlib import Path

import pandas as pd

from sniper_bot.exchange import BybitClient, InstrumentInfo


def fetch_recent_closed_candles(
    client: BybitClient,
    pair: InstrumentInfo,
    timeframe_minutes: int,
    limit: int,
) -> list[dict]:
    candles, _ = client.fetch_closed_ohlc(pair, timeframe_minutes)
    return candles[-limit:]


def latest_closed_candle(candles: list[dict]) -> dict | None:
    if not candles:
        return None
    return max(candles, key=lambda item: item["open_time"])


def load_backtest_frame(csv_path: Path | None, database_frame: pd.DataFrame) -> pd.DataFrame:
    if csv_path is None:
        return database_frame.copy()
    frame = pd.read_csv(csv_path)
    if "open_time" not in frame.columns:
        raise ValueError("CSV file must contain open_time column")
    frame["open_time"] = pd.to_datetime(frame["open_time"], utc=True)
    required = {"open", "high", "low", "close", "volume"}
    missing = required - set(frame.columns)
    if missing:
        raise ValueError(f"CSV file missing columns: {sorted(missing)}")
    return frame[["open_time", "open", "high", "low", "close", "volume"]].copy()


def now_utc() -> datetime:
    return datetime.now(timezone.utc)
