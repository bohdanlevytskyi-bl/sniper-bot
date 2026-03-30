from __future__ import annotations

import json
from datetime import date, datetime, timezone
from pathlib import Path
from typing import Any

from sqlalchemy import (
    JSON,
    Column,
    DateTime,
    Float,
    Index,
    Integer,
    String,
    Text,
    create_engine,
    select,
)
from sqlalchemy.orm import DeclarativeBase, Session, sessionmaker


class Base(DeclarativeBase):
    pass


# ---------------------------------------------------------------------------
# ORM Models
# ---------------------------------------------------------------------------

class Pair(Base):
    __tablename__ = "pairs"
    id = Column(Integer, primary_key=True, autoincrement=True)
    symbol = Column(String, unique=True, nullable=False, index=True)
    base_asset = Column(String, nullable=False)
    quote_asset = Column(String, nullable=False)
    quantity_step = Column(String, nullable=False, default="0.00000001")
    price_step = Column(String, nullable=False, default="0.01")
    quantity_decimals = Column(Integer, nullable=False, default=8)
    price_decimals = Column(Integer, nullable=False, default=2)
    min_order_qty = Column(Float, nullable=False, default=0.0)
    min_order_amount = Column(Float, nullable=False, default=0.0)
    max_market_order_qty = Column(Float, nullable=False, default=0.0)
    first_seen_at = Column(DateTime, nullable=False, default=lambda: datetime.now(timezone.utc))
    updated_at = Column(DateTime, nullable=False, default=lambda: datetime.now(timezone.utc), onupdate=lambda: datetime.now(timezone.utc))


class TickerSnapshot(Base):
    __tablename__ = "ticker_snapshots"
    id = Column(Integer, primary_key=True, autoincrement=True)
    symbol = Column(String, nullable=False, index=True)
    last_price = Column(Float, nullable=False)
    volume_24h = Column(Float, nullable=False)
    turnover_24h = Column(Float, nullable=False)
    price_change_24h_pct = Column(Float, nullable=False)
    high_24h = Column(Float, nullable=False, default=0.0)
    low_24h = Column(Float, nullable=False, default=0.0)
    recorded_at = Column(DateTime, nullable=False, default=lambda: datetime.now(timezone.utc), index=True)


class Signal(Base):
    __tablename__ = "signals"
    id = Column(Integer, primary_key=True, autoincrement=True)
    mode = Column(String, nullable=False, index=True)
    symbol = Column(String, nullable=False, index=True)
    action = Column(String, nullable=False)
    reason = Column(String, nullable=False, default="")
    composite_score = Column(Float, nullable=False, default=0.0)
    volume_spike_ratio = Column(Float, nullable=False, default=0.0)
    momentum_score = Column(Float, nullable=False, default=0.0)
    relative_strength = Column(Float, nullable=False, default=0.0)
    price = Column(Float, nullable=False, default=0.0)
    created_at = Column(DateTime, nullable=False, default=lambda: datetime.now(timezone.utc), index=True)


class Order(Base):
    __tablename__ = "orders"
    id = Column(Integer, primary_key=True, autoincrement=True)
    mode = Column(String, nullable=False, index=True)
    symbol = Column(String, nullable=False, index=True)
    side = Column(String, nullable=False)
    status = Column(String, nullable=False, default="submitted")
    quantity = Column(Float, nullable=False)
    price_at_submission = Column(Float, nullable=False, default=0.0)
    fill_price = Column(Float)
    fee = Column(Float, default=0.0)
    cost = Column(Float, default=0.0)
    exchange_order_id = Column(String)
    payload = Column(JSON)
    submitted_at = Column(DateTime, nullable=False, default=lambda: datetime.now(timezone.utc))
    confirmed_at = Column(DateTime)


class Position(Base):
    __tablename__ = "positions"
    id = Column(Integer, primary_key=True, autoincrement=True)
    mode = Column(String, nullable=False, index=True)
    symbol = Column(String, nullable=False, index=True)
    status = Column(String, nullable=False, default="open", index=True)
    entry_price = Column(Float, nullable=False)
    quantity = Column(Float, nullable=False)
    usdt_invested = Column(Float, nullable=False, default=0.0)
    entry_time = Column(DateTime, nullable=False, default=lambda: datetime.now(timezone.utc))
    max_price = Column(Float, nullable=False)
    stop_price = Column(Float, nullable=False)
    exit_price = Column(Float)
    exit_time = Column(DateTime)
    usdt_received = Column(Float)
    realized_pnl = Column(Float)
    realized_pnl_pct = Column(Float)
    exit_reason = Column(String)
    notes = Column(JSON)


class BotState(Base):
    __tablename__ = "bot_state"
    mode = Column(String, primary_key=True)
    status = Column(String, nullable=False, default="IDLE")
    usdt_balance = Column(Float, nullable=False, default=0.0)
    position_value = Column(Float, nullable=False, default=0.0)
    last_equity = Column(Float, nullable=False, default=0.0)
    high_water_mark = Column(Float)
    daily_start_equity = Column(Float)
    daily_realized_pnl = Column(Float, nullable=False, default=0.0)
    daily_loss_date = Column(String)
    consecutive_losses = Column(Integer, nullable=False, default=0)
    cooldown_until = Column(DateTime)
    halted_at = Column(DateTime)
    halt_reason = Column(String)
    last_scan_at = Column(DateTime)
    updated_at = Column(DateTime, nullable=False, default=lambda: datetime.now(timezone.utc), onupdate=lambda: datetime.now(timezone.utc))


class EquitySnapshot(Base):
    __tablename__ = "equity_snapshots"
    id = Column(Integer, primary_key=True, autoincrement=True)
    mode = Column(String, nullable=False, index=True)
    recorded_at = Column(DateTime, nullable=False, default=lambda: datetime.now(timezone.utc), index=True)
    equity = Column(Float, nullable=False)
    cash_balance = Column(Float, nullable=False)
    position_value = Column(Float, nullable=False, default=0.0)
    high_water_mark = Column(Float, nullable=False, default=0.0)
    drawdown_pct = Column(Float, nullable=False, default=0.0)


class RiskEvent(Base):
    __tablename__ = "risk_events"
    id = Column(Integer, primary_key=True, autoincrement=True)
    mode = Column(String, nullable=False, index=True)
    event_type = Column(String, nullable=False)
    message = Column(Text, nullable=False, default="")
    details = Column(JSON)
    severity = Column(String, nullable=False, default="info")
    created_at = Column(DateTime, nullable=False, default=lambda: datetime.now(timezone.utc), index=True)


class CycleLog(Base):
    __tablename__ = "cycle_logs"
    id = Column(Integer, primary_key=True, autoincrement=True)
    mode = Column(String, nullable=False, index=True)
    recorded_at = Column(DateTime, nullable=False, default=lambda: datetime.now(timezone.utc), index=True)
    candidates_scanned = Column(Integer, nullable=False, default=0)
    # JSON list of {symbol, composite_score, volume_score, momentum_score, relative_strength, volume_spike_ratio, price}
    top_candidates = Column(JSON, nullable=False, default=list)
    top_score = Column(Float, nullable=False, default=0.0)
    entry_action = Column(String, nullable=False, default="none")  # entered | blocked | no_signal | below_threshold
    entry_symbol = Column(String)
    block_reason = Column(String)
    # Market context for AI analysis
    btc_price = Column(Float)
    btc_change_24h_pct = Column(Float)
    market_breadth_pct = Column(Float)    # % of pairs with positive 24h change
    total_tickers = Column(Integer)
    avg_volume_ratio = Column(Float)      # avg turnover relative to median
    equity = Column(Float)
    open_positions_count = Column(Integer)


class AITuneLog(Base):
    __tablename__ = "ai_tune_logs"
    id = Column(Integer, primary_key=True, autoincrement=True)
    mode = Column(String, nullable=False, index=True)
    recorded_at = Column(DateTime, nullable=False, default=lambda: datetime.now(timezone.utc), index=True)
    trigger_cycle = Column(Integer, nullable=False)
    model_used = Column(String, nullable=False)
    data_summary = Column(JSON)
    raw_response = Column(Text, nullable=False)
    proposed_changes = Column(JSON, nullable=False)
    applied_changes = Column(JSON, nullable=False)
    rejected_changes = Column(JSON)
    reasoning = Column(Text)
    confidence = Column(String)
    status = Column(String, nullable=False, default="applied")  # applied | skipped | error


# ---------------------------------------------------------------------------
# Database helper
# ---------------------------------------------------------------------------

class Database:
    def __init__(self, db_path: Path):
        self.db_path = db_path
        db_path.parent.mkdir(parents=True, exist_ok=True)
        self.engine = create_engine(f"sqlite:///{db_path}", echo=False)
        self._Session = sessionmaker(bind=self.engine, expire_on_commit=False)

    def create_tables(self) -> None:
        Base.metadata.create_all(self.engine)

    def session(self) -> Session:
        return self._Session()


# ---------------------------------------------------------------------------
# Query helpers
# ---------------------------------------------------------------------------

def get_or_create_state(session: Session, mode: str) -> BotState:
    state = session.get(BotState, mode)
    if state is None:
        state = BotState(mode=mode)
        session.add(state)
        session.flush()
    return state


def get_open_positions(session: Session, mode: str) -> list[Position]:
    return list(
        session.scalars(
            select(Position).where(Position.mode == mode, Position.status == "open")
        ).all()
    )


def get_open_position_for_symbol(session: Session, mode: str, symbol: str) -> Position | None:
    return session.scalar(
        select(Position).where(Position.mode == mode, Position.symbol == symbol, Position.status == "open")
    )


def record_signal(
    session: Session,
    mode: str,
    symbol: str,
    action: str,
    reason: str,
    composite_score: float,
    volume_spike_ratio: float,
    momentum_score: float,
    relative_strength: float,
    price: float,
) -> Signal:
    sig = Signal(
        mode=mode,
        symbol=symbol,
        action=action,
        reason=reason,
        composite_score=composite_score,
        volume_spike_ratio=volume_spike_ratio,
        momentum_score=momentum_score,
        relative_strength=relative_strength,
        price=price,
    )
    session.add(sig)
    session.flush()
    return sig


def record_order(
    session: Session,
    mode: str,
    symbol: str,
    side: str,
    quantity: float,
    price_at_submission: float,
    fill_price: float | None = None,
    fee: float = 0.0,
    cost: float = 0.0,
    status: str = "filled",
    exchange_order_id: str | None = None,
    payload: dict | None = None,
) -> Order:
    order = Order(
        mode=mode,
        symbol=symbol,
        side=side,
        quantity=quantity,
        price_at_submission=price_at_submission,
        fill_price=fill_price,
        fee=fee,
        cost=cost,
        status=status,
        exchange_order_id=exchange_order_id,
        payload=payload,
        confirmed_at=datetime.now(timezone.utc) if status == "filled" else None,
    )
    session.add(order)
    session.flush()
    return order


def open_position(
    session: Session,
    mode: str,
    symbol: str,
    entry_price: float,
    quantity: float,
    usdt_invested: float,
    stop_price: float,
) -> Position:
    pos = Position(
        mode=mode,
        symbol=symbol,
        entry_price=entry_price,
        quantity=quantity,
        usdt_invested=usdt_invested,
        max_price=entry_price,
        stop_price=stop_price,
    )
    session.add(pos)
    session.flush()
    return pos


def close_position(
    session: Session,
    position: Position,
    exit_price: float,
    usdt_received: float,
    exit_reason: str,
) -> float:
    position.status = "closed"
    position.exit_price = exit_price
    position.exit_time = datetime.now(timezone.utc)
    position.usdt_received = usdt_received
    position.realized_pnl = usdt_received - position.usdt_invested
    position.realized_pnl_pct = position.realized_pnl / position.usdt_invested if position.usdt_invested else 0.0
    position.exit_reason = exit_reason
    session.flush()
    return position.realized_pnl


def record_equity_snapshot(
    session: Session,
    mode: str,
    equity: float,
    cash_balance: float,
    position_value: float,
    high_water_mark: float,
    drawdown_pct: float,
) -> EquitySnapshot:
    snap = EquitySnapshot(
        mode=mode,
        equity=equity,
        cash_balance=cash_balance,
        position_value=position_value,
        high_water_mark=high_water_mark,
        drawdown_pct=drawdown_pct,
    )
    session.add(snap)
    session.flush()
    return snap


def record_risk_event(
    session: Session,
    mode: str,
    event_type: str,
    message: str,
    details: dict | None = None,
    severity: str = "info",
) -> RiskEvent:
    event = RiskEvent(
        mode=mode,
        event_type=event_type,
        message=message,
        details=details,
        severity=severity,
    )
    session.add(event)
    session.flush()
    return event


def record_cycle_log(
    session: Session,
    mode: str,
    candidates_scanned: int,
    top_candidates: list[dict],
    entry_action: str,
    entry_symbol: str | None = None,
    block_reason: str | None = None,
    market_context: dict[str, Any] | None = None,
) -> CycleLog:
    top_score = max((c["composite_score"] for c in top_candidates), default=0.0)
    ctx = market_context or {}
    log = CycleLog(
        mode=mode,
        candidates_scanned=candidates_scanned,
        top_candidates=top_candidates,
        top_score=top_score,
        entry_action=entry_action,
        entry_symbol=entry_symbol,
        block_reason=block_reason,
        btc_price=ctx.get("btc_price"),
        btc_change_24h_pct=ctx.get("btc_change_24h_pct"),
        market_breadth_pct=ctx.get("market_breadth_pct"),
        total_tickers=ctx.get("total_tickers"),
        avg_volume_ratio=ctx.get("avg_volume_ratio"),
        equity=ctx.get("equity"),
        open_positions_count=ctx.get("open_positions_count"),
    )
    session.add(log)
    session.flush()
    return log


def record_ai_tune_log(
    session: Session,
    mode: str,
    trigger_cycle: int,
    model_used: str,
    data_summary: dict | None,
    raw_response: str,
    proposed_changes: dict,
    applied_changes: dict,
    rejected_changes: dict | None = None,
    reasoning: str | None = None,
    confidence: str | None = None,
    status: str = "applied",
) -> AITuneLog:
    log = AITuneLog(
        mode=mode,
        trigger_cycle=trigger_cycle,
        model_used=model_used,
        data_summary=data_summary,
        raw_response=raw_response,
        proposed_changes=proposed_changes,
        applied_changes=applied_changes,
        rejected_changes=rejected_changes,
        reasoning=reasoning,
        confidence=confidence,
        status=status,
    )
    session.add(log)
    session.flush()
    return log


def upsert_pair(session: Session, symbol: str, info: dict[str, Any]) -> Pair:
    pair = session.scalar(select(Pair).where(Pair.symbol == symbol))
    if pair is None:
        pair = Pair(symbol=symbol, **info)
        session.add(pair)
    else:
        for k, v in info.items():
            setattr(pair, k, v)
        pair.updated_at = datetime.now(timezone.utc)
    session.flush()
    return pair
