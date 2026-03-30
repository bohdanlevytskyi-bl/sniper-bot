from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timezone

from sniper_bot.config import ExecutionConfig
from sniper_bot.exchange import BybitClient
from sniper_bot.storage import BotState


@dataclass(slots=True)
class FillResult:
    symbol: str
    side: str
    avg_price: float
    quantity: float
    fee: float
    cost: float
    order_id: str | None = None


def apply_slippage(price: float, side: str, slippage_bps: int) -> float:
    factor = slippage_bps / 10_000
    if side == "buy":
        return price * (1 + factor)
    return price * (1 - factor)


class PaperBroker:
    """Simulates order fills using real market prices + slippage."""

    def __init__(self, config: ExecutionConfig):
        self.config = config

    def sync_initial_balances(self, state: BotState, initial_cash: float) -> None:
        if state.usdt_balance == 0 and state.last_equity == 0:
            state.usdt_balance = initial_cash
            state.last_equity = initial_cash
            state.high_water_mark = initial_cash
            state.daily_start_equity = initial_cash

    def execute_buy(self, state: BotState, symbol: str, quantity: float, market_price: float) -> FillResult:
        fill_price = apply_slippage(market_price, "buy", self.config.slippage_bps)
        cost = fill_price * quantity
        fee = cost * self.config.fee_rate
        total = cost + fee
        state.usdt_balance -= total
        return FillResult(
            symbol=symbol,
            side="buy",
            avg_price=fill_price,
            quantity=quantity,
            fee=fee,
            cost=cost,
        )

    def execute_sell(self, state: BotState, symbol: str, quantity: float, market_price: float) -> FillResult:
        fill_price = apply_slippage(market_price, "sell", self.config.slippage_bps)
        gross = fill_price * quantity
        fee = gross * self.config.fee_rate
        state.usdt_balance += gross - fee
        return FillResult(
            symbol=symbol,
            side="sell",
            avg_price=fill_price,
            quantity=quantity,
            fee=fee,
            cost=gross,
        )


class BybitBroker:
    """Executes real orders on Bybit."""

    def __init__(self, config: ExecutionConfig, client: BybitClient):
        self.config = config
        self.client = client

    def sync_balances(self, state: BotState) -> None:
        balances = self.client.get_balance(["USDT"])
        state.usdt_balance = balances.get("USDT", 0.0)

    def execute_buy(self, state: BotState, symbol: str, quantity: float, market_price: float) -> FillResult:
        order_id = self.client.place_market_order(symbol, "buy", quantity)
        fill = self.client.poll_order(symbol, order_id)
        state.usdt_balance -= fill.cost + fill.fee
        return FillResult(
            symbol=symbol,
            side="buy",
            avg_price=fill.avg_price,
            quantity=fill.executed_qty,
            fee=fill.fee,
            cost=fill.cost,
            order_id=fill.order_id,
        )

    def execute_sell(self, state: BotState, symbol: str, quantity: float, market_price: float) -> FillResult:
        order_id = self.client.place_market_order(symbol, "sell", quantity)
        fill = self.client.poll_order(symbol, order_id)
        state.usdt_balance += fill.cost - fill.fee
        return FillResult(
            symbol=symbol,
            side="sell",
            avg_price=fill.avg_price,
            quantity=fill.executed_qty,
            fee=fill.fee,
            cost=fill.cost,
            order_id=fill.order_id,
        )
