from __future__ import annotations

from contextlib import contextmanager
from datetime import date, datetime, timezone
from pathlib import Path
from typing import Iterator

import pandas as pd
from alembic import command
from alembic.config import Config
from sqlalchemy import JSON, Date, DateTime, Float, Integer, String, Text, UniqueConstraint, create_engine, select
from sqlalchemy.orm import DeclarativeBase, Mapped, Session, mapped_column, sessionmaker


class Base(DeclarativeBase):
    pass


class Candle(Base):
    __tablename__ = "candles"
    __table_args__ = (UniqueConstraint("pair", "timeframe", "open_time", name="uq_candles_key"),)

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    pair: Mapped[str] = mapped_column(String(32), index=True)
    timeframe: Mapped[int] = mapped_column(Integer, index=True)
    open_time: Mapped[datetime] = mapped_column(DateTime(timezone=True), index=True)
    open: Mapped[float] = mapped_column(Float)
    high: Mapped[float] = mapped_column(Float)
    low: Mapped[float] = mapped_column(Float)
    close: Mapped[float] = mapped_column(Float)
    volume: Mapped[float] = mapped_column(Float)
    trades: Mapped[int] = mapped_column(Integer, default=0)
    source: Mapped[str] = mapped_column(String(16), default="bybit")
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=lambda: datetime.now(timezone.utc))


class Signal(Base):
    __tablename__ = "signals"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    mode: Mapped[str] = mapped_column(String(16), index=True)
    pair: Mapped[str] = mapped_column(String(32), index=True)
    timeframe: Mapped[int] = mapped_column(Integer)
    action: Mapped[str] = mapped_column(String(16))
    reason: Mapped[str] = mapped_column(String(64))
    close_price: Mapped[float] = mapped_column(Float)
    ema_fast: Mapped[float] = mapped_column(Float)
    ema_slow: Mapped[float] = mapped_column(Float)
    atr: Mapped[float] = mapped_column(Float)
    next_stop: Mapped[float | None] = mapped_column(Float, nullable=True)
    candle_time: Mapped[datetime] = mapped_column(DateTime(timezone=True), index=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=lambda: datetime.now(timezone.utc))


class Order(Base):
    __tablename__ = "orders"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    mode: Mapped[str] = mapped_column(String(16), index=True)
    pair: Mapped[str] = mapped_column(String(32), index=True)
    side: Mapped[str] = mapped_column(String(8))
    order_type: Mapped[str] = mapped_column(String(16), default="market")
    status: Mapped[str] = mapped_column(String(32), default="submitted")
    exchange_order_id: Mapped[str | None] = mapped_column(String(128), nullable=True)
    client_order_id: Mapped[str | None] = mapped_column(String(128), nullable=True)
    requested_quantity: Mapped[float] = mapped_column(Float)
    filled_quantity: Mapped[float | None] = mapped_column(Float, nullable=True)
    average_price: Mapped[float | None] = mapped_column(Float, nullable=True)
    fee_paid: Mapped[float | None] = mapped_column(Float, nullable=True)
    error_message: Mapped[str | None] = mapped_column(Text, nullable=True)
    payload: Mapped[dict | None] = mapped_column(JSON, nullable=True)
    submitted_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=lambda: datetime.now(timezone.utc))
    completed_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)


class Fill(Base):
    __tablename__ = "fills"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    order_id: Mapped[int] = mapped_column(Integer, index=True)
    quantity: Mapped[float] = mapped_column(Float)
    price: Mapped[float] = mapped_column(Float)
    fee_paid: Mapped[float] = mapped_column(Float)
    cost: Mapped[float] = mapped_column(Float)
    fill_time: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=lambda: datetime.now(timezone.utc))
    payload: Mapped[dict | None] = mapped_column(JSON, nullable=True)


class Position(Base):
    __tablename__ = "positions"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    mode: Mapped[str] = mapped_column(String(16), index=True)
    pair: Mapped[str] = mapped_column(String(32), index=True)
    status: Mapped[str] = mapped_column(String(16), index=True)
    quantity: Mapped[float] = mapped_column(Float)
    entry_price: Mapped[float] = mapped_column(Float)
    entry_time: Mapped[datetime] = mapped_column(DateTime(timezone=True))
    stop_price: Mapped[float] = mapped_column(Float)
    max_price: Mapped[float] = mapped_column(Float)
    exit_price: Mapped[float | None] = mapped_column(Float, nullable=True)
    exit_time: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    realized_pnl: Mapped[float | None] = mapped_column(Float, nullable=True)
    notes: Mapped[dict | None] = mapped_column(JSON, nullable=True)


class EquitySnapshot(Base):
    __tablename__ = "equity_snapshots"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    mode: Mapped[str] = mapped_column(String(16), index=True)
    recorded_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), index=True)
    equity: Mapped[float] = mapped_column(Float)
    cash_balance: Mapped[float] = mapped_column(Float)
    asset_balance: Mapped[float] = mapped_column(Float)
    high_water_mark: Mapped[float] = mapped_column(Float)
    drawdown_pct: Mapped[float] = mapped_column(Float)


class RiskEvent(Base):
    __tablename__ = "risk_events"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    mode: Mapped[str] = mapped_column(String(16), index=True)
    event_type: Mapped[str] = mapped_column(String(32), index=True)
    severity: Mapped[str] = mapped_column(String(16), default="info")
    message: Mapped[str] = mapped_column(Text)
    payload: Mapped[dict | None] = mapped_column(JSON, nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=lambda: datetime.now(timezone.utc))


class BotRun(Base):
    __tablename__ = "bot_runs"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    mode: Mapped[str] = mapped_column(String(16), index=True)
    status: Mapped[str] = mapped_column(String(16), default="running")
    started_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=lambda: datetime.now(timezone.utc))
    ended_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    config_path: Mapped[str] = mapped_column(String(512))


class AIObservation(Base):
    __tablename__ = "ai_observations"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    mode: Mapped[str] = mapped_column(String(16), index=True)
    kind: Mapped[str] = mapped_column(String(32), index=True)
    model: Mapped[str] = mapped_column(String(64))
    label: Mapped[str | None] = mapped_column(String(64), nullable=True)
    confidence: Mapped[float | None] = mapped_column(Float, nullable=True)
    rationale: Mapped[str | None] = mapped_column(Text, nullable=True)
    risk_notes: Mapped[str | None] = mapped_column(Text, nullable=True)
    payload: Mapped[dict | None] = mapped_column(JSON, nullable=True)
    observed_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), index=True)


class DailySummary(Base):
    __tablename__ = "daily_summaries"
    __table_args__ = (UniqueConstraint("mode", "summary_date", name="uq_daily_summary"),)

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    mode: Mapped[str] = mapped_column(String(16), index=True)
    summary_date: Mapped[date] = mapped_column(Date, index=True)
    text: Mapped[str] = mapped_column(Text)
    regime_recap: Mapped[str] = mapped_column(Text)
    pnl_recap: Mapped[str] = mapped_column(Text)
    notable_risks: Mapped[str] = mapped_column(Text)
    sent_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=lambda: datetime.now(timezone.utc))


class BotState(Base):
    __tablename__ = "bot_state"

    mode: Mapped[str] = mapped_column(String(16), primary_key=True)
    status: Mapped[str] = mapped_column(String(16), default="IDLE")
    high_water_mark: Mapped[float | None] = mapped_column(Float, nullable=True)
    last_equity: Mapped[float | None] = mapped_column(Float, nullable=True)
    daily_start_equity: Mapped[float | None] = mapped_column(Float, nullable=True)
    daily_realized_pnl: Mapped[float] = mapped_column(Float, default=0.0)
    daily_loss_date: Mapped[date | None] = mapped_column(Date, nullable=True)
    consecutive_losses: Mapped[int] = mapped_column(Integer, default=0)
    cooldown_until: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    quote_balance: Mapped[float] = mapped_column(Float, default=0.0)
    asset_balance: Mapped[float] = mapped_column(Float, default=0.0)
    halted_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    halt_reason: Mapped[str | None] = mapped_column(String(64), nullable=True)
    last_processed_candle_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    last_summary_date: Mapped[date | None] = mapped_column(Date, nullable=True)
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=lambda: datetime.now(timezone.utc))


def database_url(path: Path) -> str:
    return f"sqlite:///{path.as_posix()}"


class Database:
    def __init__(self, path: Path):
        self.path = path
        self.url = database_url(path)
        self.engine = create_engine(self.url, future=True)
        self.SessionLocal = sessionmaker(bind=self.engine, expire_on_commit=False, future=True)

    def upgrade(self) -> None:
        self.path.parent.mkdir(parents=True, exist_ok=True)
        config = Config(str(_project_root() / "alembic.ini"))
        config.set_main_option("script_location", str(_project_root() / "migrations"))
        config.set_main_option("sqlalchemy.url", self.url)
        command.upgrade(config, "head")

    @contextmanager
    def session(self) -> Iterator[Session]:
        session = self.SessionLocal()
        try:
            yield session
            session.commit()
        except Exception:
            session.rollback()
            raise
        finally:
            session.close()


def get_or_create_state(session: Session, mode: str) -> BotState:
    state = session.get(BotState, mode)
    if state is None:
        state = BotState(mode=mode)
        session.add(state)
        session.flush()
    return state


def get_open_position(session: Session, mode: str, pair: str) -> Position | None:
    stmt = (
        select(Position)
        .where(Position.mode == mode, Position.pair == pair, Position.status == "open")
        .order_by(Position.entry_time.desc())
    )
    return session.scalar(stmt)


def load_candles_frame(session: Session, pair: str, timeframe: int, limit: int = 500) -> pd.DataFrame:
    stmt = (
        select(Candle)
        .where(Candle.pair == pair, Candle.timeframe == timeframe)
        .order_by(Candle.open_time.desc())
        .limit(limit)
    )
    rows = list(reversed(session.scalars(stmt).all()))
    if not rows:
        return pd.DataFrame(columns=["open_time", "open", "high", "low", "close", "volume"])
    return pd.DataFrame(
        [
            {
                "open_time": row.open_time,
                "open": row.open,
                "high": row.high,
                "low": row.low,
                "close": row.close,
                "volume": row.volume,
            }
            for row in rows
        ]
    )


def upsert_candles(session: Session, pair: str, timeframe: int, candles: list[dict]) -> int:
    inserted = 0
    for candle in candles:
        stmt = select(Candle).where(
            Candle.pair == pair,
            Candle.timeframe == timeframe,
            Candle.open_time == candle["open_time"],
        )
        existing = session.scalar(stmt)
        if existing is not None:
            existing.open = candle["open"]
            existing.high = candle["high"]
            existing.low = candle["low"]
            existing.close = candle["close"]
            existing.volume = candle["volume"]
            existing.trades = candle.get("trades", 0)
            continue
        session.add(
            Candle(
                pair=pair,
                timeframe=timeframe,
                open_time=candle["open_time"],
                open=candle["open"],
                high=candle["high"],
                low=candle["low"],
                close=candle["close"],
                volume=candle["volume"],
                trades=candle.get("trades", 0),
                source=candle.get("source", "bybit"),
            )
        )
        inserted += 1
    session.flush()
    return inserted


def record_signal(session: Session, mode: str, pair: str, timeframe: int, candle_time: datetime, decision: object) -> Signal:
    signal = Signal(
        mode=mode,
        pair=pair,
        timeframe=timeframe,
        action=decision.action.value,
        reason=decision.reason,
        close_price=decision.close_price,
        ema_fast=decision.ema_fast,
        ema_slow=decision.ema_slow,
        atr=decision.atr,
        next_stop=decision.next_stop,
        candle_time=candle_time,
    )
    session.add(signal)
    session.flush()
    return signal


def record_equity_snapshot(
    session: Session,
    mode: str,
    recorded_at: datetime,
    equity: float,
    cash_balance: float,
    asset_balance: float,
    high_water_mark: float,
    drawdown_pct: float,
) -> None:
    session.add(
        EquitySnapshot(
            mode=mode,
            recorded_at=recorded_at,
            equity=equity,
            cash_balance=cash_balance,
            asset_balance=asset_balance,
            high_water_mark=high_water_mark,
            drawdown_pct=drawdown_pct,
        )
    )


def record_risk_event(
    session: Session,
    mode: str,
    event_type: str,
    message: str,
    payload: dict | None = None,
    severity: str = "info",
) -> RiskEvent:
    event = RiskEvent(
        mode=mode,
        event_type=event_type,
        severity=severity,
        message=message,
        payload=payload,
    )
    session.add(event)
    session.flush()
    return event


def create_run(session: Session, mode: str, config_path: str) -> BotRun:
    run = BotRun(mode=mode, config_path=config_path)
    session.add(run)
    session.flush()
    return run


def close_run(session: Session, run_id: int, status: str) -> None:
    run = session.get(BotRun, run_id)
    if run:
        run.status = status
        run.ended_at = datetime.now(timezone.utc)


def record_ai_observation(
    session: Session,
    mode: str,
    kind: str,
    model: str,
    observed_at: datetime,
    label: str | None,
    confidence: float | None,
    rationale: str | None,
    risk_notes: str | None,
    payload: dict | None = None,
) -> AIObservation:
    observation = AIObservation(
        mode=mode,
        kind=kind,
        model=model,
        label=label,
        confidence=confidence,
        rationale=rationale,
        risk_notes=risk_notes,
        payload=payload,
        observed_at=observed_at,
    )
    session.add(observation)
    session.flush()
    return observation


def upsert_daily_summary(
    session: Session,
    mode: str,
    summary_date: date,
    text: str,
    regime_recap: str,
    pnl_recap: str,
    notable_risks: str,
) -> DailySummary:
    stmt = select(DailySummary).where(DailySummary.mode == mode, DailySummary.summary_date == summary_date)
    summary = session.scalar(stmt)
    if summary is None:
        summary = DailySummary(
            mode=mode,
            summary_date=summary_date,
            text=text,
            regime_recap=regime_recap,
            pnl_recap=pnl_recap,
            notable_risks=notable_risks,
        )
        session.add(summary)
    else:
        summary.text = text
        summary.regime_recap = regime_recap
        summary.pnl_recap = pnl_recap
        summary.notable_risks = notable_risks
    session.flush()
    return summary


def latest_summary(session: Session, mode: str) -> DailySummary | None:
    stmt = select(DailySummary).where(DailySummary.mode == mode).order_by(DailySummary.summary_date.desc()).limit(1)
    return session.scalar(stmt)


def latest_candle_time(session: Session, pair: str, timeframe: int) -> datetime | None:
    stmt = select(Candle.open_time).where(Candle.pair == pair, Candle.timeframe == timeframe).order_by(Candle.open_time.desc())
    return session.scalar(stmt)


def position_snapshot(position: Position | None) -> dict | None:
    if position is None:
        return None
    return {
        "quantity": position.quantity,
        "entry_price": position.entry_price,
        "stop_price": position.stop_price,
        "max_price": position.max_price,
        "entry_time": position.entry_time.isoformat(),
    }


def _project_root() -> Path:
    return Path(__file__).resolve().parents[2]
