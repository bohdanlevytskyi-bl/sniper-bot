from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timezone

from sqlalchemy.orm import Session

from sniper_bot.config import AppConfig
from sniper_bot.exchange import BybitClient, InstrumentInfo, Quote, round_quantity
from sniper_bot.storage import Fill, Order, Position


@dataclass(slots=True)
class ExecutionResult:
    side: str
    quantity: float
    average_price: float
    fee_paid: float
    cost: float
    exchange_order_id: str | None
    payload: dict


def quote_for_side(quote: Quote, side: str) -> float:
    return quote.ask if side == "buy" else quote.bid


def apply_slippage(price: float, side: str, slippage_bps: float) -> float:
    adjustment = price * (slippage_bps / 10_000)
    return price + adjustment if side == "buy" else price - adjustment


class PaperBroker:
    def __init__(self, config: AppConfig):
        self.config = config

    def sync_initial_balances(self, state: object) -> None:
        if state.quote_balance == 0 and state.asset_balance == 0:
            state.quote_balance = self.config.risk.initial_paper_cash
            state.last_equity = self.config.risk.initial_paper_cash
            state.daily_start_equity = self.config.risk.initial_paper_cash
            state.high_water_mark = self.config.risk.initial_paper_cash

    def execute_market_order(
        self,
        session: Session,
        mode: str,
        pair: str,
        state: object,
        side: str,
        quantity: float,
        quote: Quote,
    ) -> ExecutionResult:
        fill_price = apply_slippage(quote_for_side(quote, side), side, self.config.execution.slippage_bps)
        fee_rate = self.config.execution.fee_rate
        cost = fill_price * quantity
        fee_paid = cost * fee_rate
        order = Order(
            mode=mode,
            pair=pair,
            side=side,
            order_type="market",
            status="closed",
            requested_quantity=quantity,
            filled_quantity=quantity,
            average_price=fill_price,
            fee_paid=fee_paid,
            completed_at=datetime.now(timezone.utc),
            payload={"broker": "paper"},
        )
        session.add(order)
        session.flush()
        session.add(
            Fill(
                order_id=order.id,
                quantity=quantity,
                price=fill_price,
                fee_paid=fee_paid,
                cost=cost,
                payload={"broker": "paper"},
            )
        )
        if side == "buy":
            state.quote_balance -= cost + fee_paid
            state.asset_balance += quantity
        else:
            state.asset_balance -= quantity
            state.quote_balance += cost - fee_paid
        return ExecutionResult(side, quantity, fill_price, fee_paid, cost, None, {"broker": "paper"})


class BybitBroker:
    def __init__(self, config: AppConfig, client: BybitClient, instrument: InstrumentInfo):
        self.config = config
        self.client = client
        self.instrument = instrument

    def sync_balances(self, state: object) -> None:
        balances = self.client.get_balance([self.instrument.base_asset, self.instrument.quote_asset])
        state.quote_balance = balances.get(self.instrument.quote_asset, 0.0)
        state.asset_balance = balances.get(self.instrument.base_asset, 0.0)

    def execute_market_order(
        self,
        session: Session,
        mode: str,
        pair: str,
        state: object,
        side: str,
        quantity: float,
    ) -> ExecutionResult:
        rounded_qty = round_quantity(quantity, self.instrument.quantity_step)
        order_id = self.client.add_market_order(self.instrument, side, rounded_qty)
        order = Order(
            mode=mode,
            pair=pair,
            side=side,
            order_type="market",
            status="submitted",
            exchange_order_id=order_id,
            requested_quantity=rounded_qty,
            payload={"broker": mode, "venue": "bybit"},
        )
        session.add(order)
        session.flush()
        fill = self.client.wait_for_closed_order(self.instrument, order_id)
        order.status = fill.status
        order.filled_quantity = fill.executed_volume
        order.average_price = fill.avg_price
        order.fee_paid = fill.fee
        order.completed_at = datetime.now(timezone.utc)
        order.payload = fill.raw
        session.add(
            Fill(
                order_id=order.id,
                quantity=fill.executed_volume,
                price=fill.avg_price,
                fee_paid=fill.fee,
                cost=fill.cost,
                payload=fill.raw,
            )
        )
        self.sync_balances(state)
        return ExecutionResult(
            side=side,
            quantity=fill.executed_volume,
            average_price=fill.avg_price,
            fee_paid=fill.fee,
            cost=fill.cost,
            exchange_order_id=order_id,
            payload=fill.raw,
        )


def open_position_from_fill(
    session: Session,
    mode: str,
    pair: str,
    result: ExecutionResult,
    stop_price: float,
) -> Position:
    position = Position(
        mode=mode,
        pair=pair,
        status="open",
        quantity=result.quantity,
        entry_price=result.average_price,
        entry_time=datetime.now(timezone.utc),
        stop_price=stop_price,
        max_price=result.average_price,
        notes={"entry_fee": result.fee_paid, "entry_cost": result.cost},
    )
    session.add(position)
    session.flush()
    return position


def close_position_from_fill(session: Session, position: Position, result: ExecutionResult) -> float:
    entry_fee = float((position.notes or {}).get("entry_fee", 0.0))
    gross_entry = position.entry_price * position.quantity
    gross_exit = result.average_price * result.quantity
    realized_pnl = (gross_exit - result.fee_paid) - (gross_entry + entry_fee)
    position.status = "closed"
    position.exit_price = result.average_price
    position.exit_time = datetime.now(timezone.utc)
    position.realized_pnl = realized_pnl
    return realized_pnl
