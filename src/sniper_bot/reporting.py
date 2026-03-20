from __future__ import annotations

from dataclasses import dataclass
from datetime import date, datetime, timedelta

import pandas as pd


@dataclass(slots=True)
class BacktestMetrics:
    net_return_pct: float
    max_drawdown_pct: float
    trade_count: int
    win_rate_pct: float
    average_trade_pct: float
    expectancy: float


def compute_backtest_metrics(equity_curve: list[float], trade_pnls: list[float], starting_cash: float) -> BacktestMetrics:
    if not equity_curve:
        return BacktestMetrics(0.0, 0.0, 0, 0.0, 0.0, 0.0)
    peak = equity_curve[0]
    max_drawdown = 0.0
    for equity in equity_curve:
        peak = max(peak, equity)
        if peak > 0:
            max_drawdown = max(max_drawdown, (peak - equity) / peak)
    net_return = (equity_curve[-1] - starting_cash) / starting_cash if starting_cash else 0.0
    wins = [pnl for pnl in trade_pnls if pnl > 0]
    avg_trade = (sum(trade_pnls) / len(trade_pnls)) / starting_cash if trade_pnls and starting_cash else 0.0
    expectancy = sum(trade_pnls) / len(trade_pnls) if trade_pnls else 0.0
    return BacktestMetrics(
        net_return_pct=net_return * 100,
        max_drawdown_pct=max_drawdown * 100,
        trade_count=len(trade_pnls),
        win_rate_pct=(len(wins) / len(trade_pnls) * 100) if trade_pnls else 0.0,
        average_trade_pct=avg_trade * 100,
        expectancy=expectancy,
    )


def summary_due(now_local: datetime, summary_hour: int, summary_minute: int, last_summary_date: date | None) -> bool:
    target_time = now_local.replace(hour=summary_hour, minute=summary_minute, second=0, microsecond=0)
    if now_local < target_time:
        return False
    previous_day = (now_local - timedelta(days=1)).date()
    return last_summary_date != previous_day


def build_summary_payload(mode: str, pair: str, state: object, latest_regime: dict | None, risk_events: list[object]) -> dict:
    return {
        "mode": mode,
        "pair": pair,
        "equity": state.last_equity,
        "daily_realized_pnl": state.daily_realized_pnl,
        "high_water_mark": state.high_water_mark,
        "cooldown_until": state.cooldown_until.isoformat() if state.cooldown_until else None,
        "halt_reason": state.halt_reason,
        "latest_regime": latest_regime,
        "risk_events": [getattr(event, "message", str(event)) for event in risk_events[-5:]],
    }


def data_frame_to_candle_payload(frame: pd.DataFrame, limit: int) -> list[dict]:
    if frame.empty:
        return []
    recent = frame.tail(limit)
    return [
        {
            "open_time": row.open_time.isoformat() if hasattr(row.open_time, "isoformat") else str(row.open_time),
            "open": float(row.open),
            "high": float(row.high),
            "low": float(row.low),
            "close": float(row.close),
            "volume": float(row.volume),
        }
        for row in recent.itertuples()
    ]
