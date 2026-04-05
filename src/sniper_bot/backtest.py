"""Backtesting engine — replay historical kline data through the scoring and position logic."""
from __future__ import annotations

import json
from dataclasses import dataclass, field
from datetime import datetime, timezone
from typing import Any

from sniper_bot.config import AppConfig, PositionConfig, StrategyConfig
from sniper_bot.exchange import BybitClient
from sniper_bot.indicators import (
    compute_atr,
    compute_bollinger_bands,
    compute_macd,
    compute_obi_score,
    compute_rsi,
    compute_ta_composite,
    compute_vwap,
    compute_multi_timeframe_score,
)
from sniper_bot.scanner import compute_volume_spike
from sniper_bot.strategy import ScoredToken, compute_price_changes, rank_candidates, score_candidate


@dataclass
class BacktestPosition:
    symbol: str
    entry_price: float
    quantity: float
    usdt_invested: float
    stop_price: float
    max_price: float
    entry_time: datetime
    atr: float | None = None
    entry_score: float = 0.0

    @property
    def hold_hours(self) -> float:
        return 0.0  # updated at exit


@dataclass
class BacktestTrade:
    symbol: str
    entry_price: float
    exit_price: float
    quantity: float
    usdt_invested: float
    pnl: float
    pnl_pct: float
    entry_time: datetime
    exit_time: datetime
    exit_reason: str
    entry_score: float
    hold_hours: float
    max_price: float


@dataclass
class BacktestResult:
    initial_equity: float
    final_equity: float
    total_trades: int
    wins: int
    losses: int
    win_rate: float
    total_pnl: float
    max_drawdown_pct: float
    avg_hold_hours: float
    profit_factor: float
    trades: list[BacktestTrade] = field(default_factory=list)
    equity_curve: list[dict[str, Any]] = field(default_factory=list)

    def summary(self) -> str:
        return (
            f"Backtest: {self.total_trades} trades | "
            f"Win rate: {self.win_rate:.1%} | "
            f"PnL: {self.total_pnl:+.2f} USDT ({(self.final_equity/self.initial_equity - 1):.1%}) | "
            f"Max DD: {self.max_drawdown_pct:.1%} | "
            f"Profit factor: {self.profit_factor:.2f} | "
            f"Avg hold: {self.avg_hold_hours:.1f}h"
        )


def run_backtest(
    client: BybitClient,
    config: AppConfig,
    symbols: list[str],
    lookback_hours: int = 168,  # 7 days
    bar_interval: int = 60,     # 1h bars
) -> BacktestResult:
    """Run a backtest over historical data for the given symbols.

    Fetches klines, simulates scoring + entry + position management.
    """
    # Fetch historical data for all symbols
    bars_needed = lookback_hours // (bar_interval // 60) + 50  # extra for indicators
    symbol_candles: dict[str, list[dict[str, Any]]] = {}

    for sym in symbols:
        try:
            candles = client.fetch_klines(sym, bar_interval, limit=min(bars_needed, 1000))
            if len(candles) >= 50:
                symbol_candles[sym] = candles
        except Exception:
            continue

    if not symbol_candles:
        return BacktestResult(
            initial_equity=config.risk.initial_paper_cash,
            final_equity=config.risk.initial_paper_cash,
            total_trades=0, wins=0, losses=0, win_rate=0,
            total_pnl=0, max_drawdown_pct=0, avg_hold_hours=0, profit_factor=0,
        )

    # Find common time range
    min_bars = min(len(c) for c in symbol_candles.values())
    warmup = 35  # enough for MACD (26+9)

    # BTC candles for relative strength
    btc_candles = symbol_candles.get("BTCUSDT", [])

    # Simulation state
    equity = config.risk.initial_paper_cash
    cash = equity
    hwm = equity
    max_dd = 0.0
    positions: list[BacktestPosition] = {}
    trades: list[BacktestTrade] = []
    equity_curve: list[dict[str, Any]] = []
    fee_rate = config.execution.fee_rate

    # Replay bar by bar
    for bar_idx in range(warmup, min_bars):
        now = symbol_candles[list(symbol_candles.keys())[0]][bar_idx]["open_time"]

        # --- Manage existing positions ---
        closed_syms = []
        for sym, pos in list(positions.items()):
            candles = symbol_candles[sym]
            current_price = candles[bar_idx]["close"]
            high = candles[bar_idx]["high"]
            low = candles[bar_idx]["low"]

            # Update max price with bar high
            if high > pos.max_price:
                pos.max_price = high

            # Evaluate exit
            exit_reason = _check_exit(pos, current_price, low, config.position, now)
            if exit_reason:
                exit_price = low if "stop" in exit_reason else current_price
                fee = exit_price * pos.quantity * fee_rate
                usdt_received = exit_price * pos.quantity - fee
                pnl = usdt_received - pos.usdt_invested
                pnl_pct = pnl / pos.usdt_invested if pos.usdt_invested > 0 else 0

                hold_h = (now - pos.entry_time).total_seconds() / 3600

                trades.append(BacktestTrade(
                    symbol=sym, entry_price=pos.entry_price, exit_price=exit_price,
                    quantity=pos.quantity, usdt_invested=pos.usdt_invested,
                    pnl=round(pnl, 4), pnl_pct=round(pnl_pct, 4),
                    entry_time=pos.entry_time, exit_time=now, exit_reason=exit_reason,
                    entry_score=pos.entry_score, hold_hours=round(hold_h, 2),
                    max_price=pos.max_price,
                ))
                cash += usdt_received
                closed_syms.append(sym)

        for sym in closed_syms:
            del positions[sym]

        # --- Score candidates ---
        if len(positions) < config.risk.max_concurrent_positions:
            scored_list: list[ScoredToken] = []

            btc_change_1h = 0.0
            if len(btc_candles) > bar_idx and bar_idx >= 1:
                prev_c = btc_candles[bar_idx - 1]["close"]
                if prev_c > 0:
                    btc_change_1h = (btc_candles[bar_idx]["close"] - prev_c) / prev_c

            for sym, candles in symbol_candles.items():
                if sym in positions or sym == "BTCUSDT":
                    continue

                window = candles[max(0, bar_idx - 47):bar_idx + 1]
                window_1h = candles[max(0, bar_idx - 29):bar_idx + 1]

                if len(window) < 12:
                    continue

                price_changes = compute_price_changes(window[-10:], window_1h[-5:])

                # Volume spike
                if len(window) >= 24:
                    recent = window[-12:]
                    baseline = window[:-12]
                    base_avg = sum(b["volume"] for b in baseline) / len(baseline) if baseline else 0
                    rec_avg = sum(b["volume"] for b in recent) / len(recent) if recent else 0
                    spike = (rec_avg / base_avg) if base_avg > 0 else 1.0
                else:
                    spike = 1.0

                # TA
                rsi = compute_rsi(window_1h, period=14)
                macd = compute_macd(window_1h, fast=12, slow=26, signal=9)
                bbands = compute_bollinger_bands(window_1h, period=20)
                ta = compute_ta_composite(rsi, macd, bbands)

                # VWAP deviation
                vwap_dev = 0.0
                vwap_data = compute_vwap(window_1h, period=20)
                if vwap_data:
                    vwap_dev = vwap_data["deviation_pct"]

                # Multi-timeframe: use 5m-equivalent and 1h signals
                tf_signals: dict[str, float] = {"1h": ta}
                change_5m = price_changes.get("5m", 0.0)
                tf_signals["5m"] = max(0.0, min(1.0, 0.5 + change_5m * 20))
                mtf = compute_multi_timeframe_score(tf_signals)

                s = score_candidate(
                    sym, candles[bar_idx]["close"], spike, price_changes,
                    btc_change_1h, config.strategy, ta_composite=ta,
                    vwap_deviation=vwap_dev, mtf_confluence=mtf,
                )
                scored_list.append(s)

            ranked = rank_candidates(scored_list, config.strategy)

            for scored in ranked[:config.strategy.max_entries_per_cycle]:
                if scored.symbol in positions:
                    continue
                if len(positions) >= config.risk.max_concurrent_positions:
                    break

                price = scored.price
                max_usdt = min(config.risk.max_position_pct * equity, cash)
                if max_usdt <= 0 or price <= 0:
                    continue

                qty = max_usdt / price
                cost = qty * price
                fee = cost * fee_rate
                invested = cost + fee
                cash -= invested

                atr = compute_atr(symbol_candles[scored.symbol][max(0, bar_idx - 15):bar_idx + 1], period=14)

                if config.position.use_atr_stops and atr and atr > 0:
                    atr_pct = (atr * config.position.atr_stop_multiplier) / price
                    stop_pct = max(config.position.atr_min_stop_pct, min(config.position.atr_max_stop_pct, atr_pct))
                else:
                    stop_pct = config.position.trailing_stop_pct

                positions[scored.symbol] = BacktestPosition(
                    symbol=scored.symbol, entry_price=price, quantity=qty,
                    usdt_invested=invested, stop_price=price * (1 - stop_pct),
                    max_price=price, entry_time=now, atr=atr,
                    entry_score=scored.composite_score,
                )

        # --- Update equity ---
        pos_value = sum(
            symbol_candles[sym][bar_idx]["close"] * pos.quantity
            for sym, pos in positions.items()
            if bar_idx < len(symbol_candles[sym])
        )
        equity = cash + pos_value
        if equity > hwm:
            hwm = equity
        dd = (hwm - equity) / hwm if hwm > 0 else 0
        if dd > max_dd:
            max_dd = dd

        equity_curve.append({"time": str(now), "equity": round(equity, 2), "drawdown": round(dd, 4)})

    # --- Final stats ---
    wins = [t for t in trades if t.pnl > 0]
    losses = [t for t in trades if t.pnl <= 0]
    gross_wins = sum(t.pnl for t in wins)
    gross_losses = abs(sum(t.pnl for t in losses)) or 0.01
    hold_hours = [t.hold_hours for t in trades]

    return BacktestResult(
        initial_equity=config.risk.initial_paper_cash,
        final_equity=round(equity, 2),
        total_trades=len(trades),
        wins=len(wins),
        losses=len(losses),
        win_rate=round(len(wins) / len(trades), 4) if trades else 0,
        total_pnl=round(sum(t.pnl for t in trades), 2),
        max_drawdown_pct=round(max_dd, 4),
        avg_hold_hours=round(sum(hold_hours) / len(hold_hours), 2) if hold_hours else 0,
        profit_factor=round(gross_wins / gross_losses, 2) if gross_losses > 0 else 0,
        trades=trades,
        equity_curve=equity_curve,
    )


def _check_exit(
    pos: BacktestPosition,
    current_price: float,
    bar_low: float,
    config: PositionConfig,
    now: datetime,
) -> str | None:
    """Simplified position exit logic for backtesting."""
    entry = pos.entry_price
    atr = pos.atr

    # Trailing stop update
    unrealized_gain = (pos.max_price - entry) / entry if entry > 0 else 0
    if config.use_atr_stops and atr and atr > 0 and entry > 0:
        atr_trail_pct = (atr * config.atr_trail_multiplier) / entry
        trail_pct = max(config.atr_min_stop_pct, min(config.atr_max_stop_pct, atr_trail_pct))
        if unrealized_gain >= config.trail_tighten_gain_pct:
            trail_pct = min(trail_pct, config.trail_tightened_stop_pct)
    else:
        trail_pct = config.trail_tightened_stop_pct if unrealized_gain >= config.trail_tighten_gain_pct else config.trailing_stop_pct

    new_stop = pos.max_price * (1 - trail_pct)
    if new_stop > pos.stop_price:
        pos.stop_price = new_stop

    # Hard stop
    if config.use_atr_stops and atr and atr > 0 and entry > 0:
        hard_pct = (atr * config.atr_stop_multiplier) / entry
        hard_pct = max(config.atr_min_stop_pct, min(config.atr_max_stop_pct, hard_pct))
    else:
        hard_pct = config.hard_stop_pct

    if bar_low <= entry * (1 - hard_pct):
        return "hard_stop"

    if bar_low <= pos.stop_price:
        return "trailing_stop"

    if current_price >= entry * config.take_profit_multiple:
        return "take_profit"

    held_hours = (now - pos.entry_time).total_seconds() / 3600
    gain_pct = (current_price - entry) / entry if entry > 0 else 0

    if held_hours >= config.time_decay_hours / 2 and gain_pct < 0:
        return "time_decay_early"
    if held_hours >= config.time_decay_hours and gain_pct < config.time_decay_min_gain_pct:
        return "time_decay"
    if held_hours >= config.max_hold_hours:
        return "max_hold_time"

    return None
