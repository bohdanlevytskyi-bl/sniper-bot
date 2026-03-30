import tempfile
from datetime import datetime, timezone
from pathlib import Path

from sniper_bot.storage import (
    Database,
    close_position,
    get_open_position_for_symbol,
    get_open_positions,
    get_or_create_state,
    open_position,
    record_equity_snapshot,
    record_order,
    record_risk_event,
    record_signal,
    upsert_pair,
)


def _db(tmp_path: Path) -> Database:
    db = Database(tmp_path / "test.sqlite")
    db.create_tables()
    return db


def test_create_tables(tmp_path):
    db = _db(tmp_path)
    with db.session() as s:
        state = get_or_create_state(s, "paper")
        assert state.mode == "paper"
        assert state.status == "IDLE"
        s.commit()


def test_get_or_create_state_idempotent(tmp_path):
    db = _db(tmp_path)
    with db.session() as s:
        s1 = get_or_create_state(s, "paper")
        s1.usdt_balance = 500.0
        s.commit()
    with db.session() as s:
        s2 = get_or_create_state(s, "paper")
        assert s2.usdt_balance == 500.0


def test_open_and_close_position(tmp_path):
    db = _db(tmp_path)
    with db.session() as s:
        pos = open_position(s, "paper", "ETHUSDT", 3000.0, 0.1, 300.0, 2550.0)
        assert pos.status == "open"
        assert pos.symbol == "ETHUSDT"

        pnl = close_position(s, pos, 3500.0, 345.0, "take_profit")
        assert pnl == 45.0  # 345 - 300
        assert pos.status == "closed"
        assert pos.exit_reason == "take_profit"
        s.commit()


def test_get_open_positions(tmp_path):
    db = _db(tmp_path)
    with db.session() as s:
        open_position(s, "paper", "ETHUSDT", 3000.0, 0.1, 300.0, 2550.0)
        open_position(s, "paper", "BTCUSDT", 60000.0, 0.005, 300.0, 51000.0)
        s.commit()

    with db.session() as s:
        positions = get_open_positions(s, "paper")
        assert len(positions) == 2


def test_get_open_position_for_symbol(tmp_path):
    db = _db(tmp_path)
    with db.session() as s:
        open_position(s, "paper", "ETHUSDT", 3000.0, 0.1, 300.0, 2550.0)
        s.commit()

    with db.session() as s:
        found = get_open_position_for_symbol(s, "paper", "ETHUSDT")
        assert found is not None
        assert found.symbol == "ETHUSDT"

        not_found = get_open_position_for_symbol(s, "paper", "BTCUSDT")
        assert not_found is None


def test_record_signal(tmp_path):
    db = _db(tmp_path)
    with db.session() as s:
        sig = record_signal(s, "paper", "ETHUSDT", "enter", "momentum", 0.75, 5.2, 0.8, 0.3, 3000.0)
        assert sig.id is not None
        assert sig.composite_score == 0.75
        s.commit()


def test_record_order(tmp_path):
    db = _db(tmp_path)
    with db.session() as s:
        order = record_order(s, "paper", "ETHUSDT", "buy", 0.1, 3000.0, 3003.0, 0.3, 300.3)
        assert order.id is not None
        assert order.side == "buy"
        s.commit()


def test_record_equity_snapshot(tmp_path):
    db = _db(tmp_path)
    with db.session() as s:
        snap = record_equity_snapshot(s, "paper", 1000.0, 800.0, 200.0, 1000.0, 0.0)
        assert snap.id is not None
        assert snap.equity == 1000.0
        s.commit()


def test_record_risk_event(tmp_path):
    db = _db(tmp_path)
    with db.session() as s:
        event = record_risk_event(s, "paper", "daily_loss", "Daily loss limit hit", {"pct": 0.06}, "warning")
        assert event.id is not None
        assert event.severity == "warning"
        s.commit()


def test_upsert_pair(tmp_path):
    db = _db(tmp_path)
    with db.session() as s:
        pair = upsert_pair(s, "ETHUSDT", {
            "base_asset": "ETH",
            "quote_asset": "USDT",
            "quantity_step": "0.001",
            "price_step": "0.01",
            "quantity_decimals": 3,
            "price_decimals": 2,
            "min_order_qty": 0.001,
            "min_order_amount": 1.0,
            "max_market_order_qty": 1000.0,
        })
        assert pair.symbol == "ETHUSDT"
        s.commit()

    # Update
    with db.session() as s:
        pair = upsert_pair(s, "ETHUSDT", {"min_order_qty": 0.01})
        assert pair.min_order_qty == 0.01
        s.commit()
