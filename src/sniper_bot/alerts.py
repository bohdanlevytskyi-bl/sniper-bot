from __future__ import annotations

from datetime import datetime, timezone
from typing import Any

import httpx
from tenacity import retry, retry_if_exception_type, stop_after_attempt, wait_exponential

from sniper_bot.logging_config import get_logger

LOGGER = get_logger(__name__)


class TelegramError(RuntimeError):
    pass


class RetryableTelegramError(TelegramError):
    pass


class TelegramNotifier:
    def __init__(self, bot_token: str, chat_id: str):
        self.bot_token = bot_token
        self.chat_id = chat_id
        self.client = httpx.Client(timeout=15)

    def close(self) -> None:
        self.client.close()

    @retry(
        retry=retry_if_exception_type(RetryableTelegramError),
        wait=wait_exponential(multiplier=1, min=1, max=8),
        stop=stop_after_attempt(5),
        reraise=True,
    )
    def send_message(self, text: str) -> None:
        url = f"https://api.telegram.org/bot{self.bot_token}/sendMessage"
        response = self.client.post(url, json={"chat_id": self.chat_id, "text": text, "parse_mode": "HTML"})
        if response.status_code in {429, 500, 502, 503}:
            raise RetryableTelegramError(f"Telegram HTTP {response.status_code}")
        if response.is_error:
            LOGGER.warning("telegram_send_failed", extra={"status": response.status_code, "body": response.text[:200]})

    def send_daily_summary(self, summary: dict[str, Any]) -> None:
        """Send a rich daily performance summary."""
        pnl = summary.get("daily_pnl", 0)
        pnl_icon = "+" if pnl >= 0 else ""
        equity = summary.get("equity", 0)
        trades = summary.get("trades_today", 0)
        wins = summary.get("wins_today", 0)
        losses = summary.get("losses_today", 0)
        win_rate = (wins / trades * 100) if trades > 0 else 0
        dd = summary.get("drawdown_pct", 0)
        hwm = summary.get("hwm", 0)

        text = (
            f"<b>Daily Summary</b>\n"
            f"\n"
            f"  Equity: <b>{equity:.2f} USDT</b>\n"
            f"  Daily P&L: <b>{pnl_icon}{pnl:.2f} USDT</b>\n"
            f"  High Water Mark: {hwm:.2f}\n"
            f"  Drawdown: {dd:.1%}\n"
            f"\n"
            f"  Trades: {trades} ({wins}W / {losses}L)\n"
            f"  Win Rate: {win_rate:.0f}%\n"
        )

        open_pos = summary.get("open_positions", [])
        if open_pos:
            text += f"\n  <b>Open Positions ({len(open_pos)})</b>\n"
            for p in open_pos[:5]:
                sym = p.get("symbol", "?")
                entry = p.get("entry_price", 0)
                unrealized = p.get("unrealized_pct", 0)
                icon = "+" if unrealized >= 0 else ""
                text += f"    {sym}: {icon}{unrealized:.1%}\n"

        top = summary.get("top_scores", [])
        if top:
            text += f"\n  <b>Top Signals</b>\n"
            for t in top[:3]:
                text += f"    {t['symbol']}: {t['score']:.3f}\n"

        self.send_message(text)

    def send_trade_alert(self, trade: dict[str, Any], action: str = "opened") -> None:
        """Send a rich trade alert with full context."""
        sym = trade.get("symbol", "?")
        price = trade.get("price", 0)
        qty = trade.get("qty", 0)
        score = trade.get("score", 0)
        pnl = trade.get("pnl", 0)
        pnl_pct = trade.get("pnl_pct", 0)
        reason = trade.get("reason", "")
        hold = trade.get("hold_hours", 0)

        if action == "opened":
            stop = trade.get("stop_price", 0)
            text = (
                f"<b>Trade Opened</b>\n"
                f"\n"
                f"  Symbol: <b>{sym}</b>\n"
                f"  Price: {price:.6f}\n"
                f"  Quantity: {qty:.6f}\n"
                f"  Score: {score:.3f}\n"
                f"  Stop: {stop:.6f}\n"
            )
            signals = trade.get("signals", {})
            if signals:
                text += f"\n  <b>Signal Breakdown</b>\n"
                for k, v in signals.items():
                    text += f"    {k}: {v:.3f}\n"
        else:
            pnl_icon = "+" if pnl >= 0 else ""
            text = (
                f"<b>Trade Closed</b>\n"
                f"\n"
                f"  Symbol: <b>{sym}</b>\n"
                f"  P&L: <b>{pnl_icon}{pnl:.2f} USDT ({pnl_icon}{pnl_pct:.1%})</b>\n"
                f"  Reason: {reason}\n"
                f"  Hold: {hold:.1f}h\n"
            )
            peak = trade.get("peak_gain_pct", 0)
            if peak:
                text += f"  Peak Gain: +{peak:.1%}\n"

        self.send_message(text)

    def send_risk_alert(self, event_type: str, details: dict[str, Any]) -> None:
        """Send urgent risk event alerts."""
        severity = details.get("severity", "warning")
        icon = "!!!" if severity == "critical" else "!"
        message = details.get("message", "")
        text = (
            f"<b>{icon} Risk Alert: {event_type}</b>\n"
            f"\n"
            f"  {message}\n"
        )
        for k, v in details.items():
            if k not in ("severity", "message"):
                text += f"  {k}: {v}\n"
        self.send_message(text)


def format_alert(title: str, lines: list[str]) -> str:
    body = "\n".join(f"  {line}" for line in lines)
    return f"<b>{title}</b>\n{body}"


def build_daily_summary(db, mode: str) -> dict[str, Any]:
    """Build daily summary data from the database."""
    from sqlalchemy import desc, func, select

    from sniper_bot.storage import EquitySnapshot, Position, get_open_positions, get_or_create_state

    with db.session() as session:
        state = get_or_create_state(session, mode)
        open_pos = get_open_positions(session, mode)

        # Today's trades
        today_start = datetime.now(timezone.utc).replace(hour=0, minute=0, second=0, microsecond=0)
        closed_today = list(session.scalars(
            select(Position)
            .where(
                Position.mode == mode,
                Position.status == "closed",
                Position.exit_time >= today_start,
            )
            .order_by(desc(Position.exit_time))
        ).all())

        wins = [p for p in closed_today if (p.realized_pnl or 0) > 0]
        losses = [p for p in closed_today if (p.realized_pnl or 0) <= 0]

        equity = float(state.last_equity or state.usdt_balance or 0)
        hwm = float(state.high_water_mark or equity)
        dd = (hwm - equity) / hwm if hwm > 0 else 0

        return {
            "equity": equity,
            "daily_pnl": float(state.daily_realized_pnl or 0),
            "hwm": hwm,
            "drawdown_pct": dd,
            "trades_today": len(closed_today),
            "wins_today": len(wins),
            "losses_today": len(losses),
            "open_positions": [
                {
                    "symbol": p.symbol,
                    "entry_price": float(p.entry_price or 0),
                    "unrealized_pct": (float(p.max_price or p.entry_price or 0) - float(p.entry_price or 0)) / float(p.entry_price or 1),
                }
                for p in open_pos
            ],
            "consecutive_losses": int(state.consecutive_losses or 0),
            "status": state.status or "IDLE",
        }
