from sniper_bot.config import ExecutionConfig
from sniper_bot.execution import PaperBroker, apply_slippage
from sniper_bot.storage import BotState


def _state() -> BotState:
    s = BotState(mode="paper")
    s.usdt_balance = 1000.0
    s.last_equity = 1000.0
    s.high_water_mark = 1000.0
    s.daily_start_equity = 1000.0
    s.daily_realized_pnl = 0.0
    return s


def test_apply_slippage_buy():
    result = apply_slippage(100.0, "buy", 10)
    assert result > 100.0
    assert abs(result - 100.10) < 0.01


def test_apply_slippage_sell():
    result = apply_slippage(100.0, "sell", 10)
    assert result < 100.0
    assert abs(result - 99.90) < 0.01


def test_paper_broker_buy():
    config = ExecutionConfig(slippage_bps=10, fee_rate=0.001)
    broker = PaperBroker(config)
    state = _state()

    fill = broker.execute_buy(state, "ETHUSDT", 0.1, 3000.0)

    assert fill.symbol == "ETHUSDT"
    assert fill.side == "buy"
    assert fill.quantity == 0.1
    assert fill.avg_price > 3000.0  # slippage applied
    assert fill.fee > 0
    assert state.usdt_balance < 1000.0


def test_paper_broker_sell():
    config = ExecutionConfig(slippage_bps=10, fee_rate=0.001)
    broker = PaperBroker(config)
    state = _state()
    state.usdt_balance = 500.0  # already bought something

    fill = broker.execute_sell(state, "ETHUSDT", 0.1, 3000.0)

    assert fill.side == "sell"
    assert fill.avg_price < 3000.0  # slippage
    assert state.usdt_balance > 500.0


def test_paper_broker_round_trip():
    config = ExecutionConfig(slippage_bps=10, fee_rate=0.001)
    broker = PaperBroker(config)
    state = _state()
    initial = state.usdt_balance

    buy_fill = broker.execute_buy(state, "ETHUSDT", 0.1, 3000.0)
    sell_fill = broker.execute_sell(state, "ETHUSDT", 0.1, 3000.0)

    # Should lose a bit to slippage + fees
    assert state.usdt_balance < initial


def test_paper_sync_initial_balances():
    config = ExecutionConfig()
    broker = PaperBroker(config)
    state = BotState(mode="paper")
    state.usdt_balance = 0.0
    state.last_equity = 0.0
    state.high_water_mark = None
    state.daily_start_equity = None

    broker.sync_initial_balances(state, 1000.0)

    assert state.usdt_balance == 1000.0
    assert state.last_equity == 1000.0
    assert state.high_water_mark == 1000.0
