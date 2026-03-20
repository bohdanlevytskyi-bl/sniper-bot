from __future__ import annotations

from datetime import datetime, timezone
from pathlib import Path

from sniper_bot.storage import Database, get_or_create_state, load_candles_frame, upsert_candles


def test_database_upgrade_and_candle_roundtrip(tmp_path: Path) -> None:
    db = Database(tmp_path / "paper.sqlite")
    db.upgrade()

    with db.session() as session:
        state = get_or_create_state(session, "paper")
        assert state.mode == "paper"
        inserted = upsert_candles(
            session,
            "BTCUSDT",
            60,
            [
                {
                    "open_time": datetime(2026, 3, 17, tzinfo=timezone.utc),
                    "open": 100.0,
                    "high": 105.0,
                    "low": 99.0,
                    "close": 104.0,
                    "volume": 12.0,
                    "trades": 5,
                }
            ],
        )
        assert inserted == 1

    with db.session() as session:
        frame = load_candles_frame(session, "BTCUSDT", 60, limit=10)
        assert len(frame) == 1
        assert float(frame.iloc[0]["close"]) == 104.0
