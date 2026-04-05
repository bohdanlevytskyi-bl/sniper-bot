"""Web dashboard for monitoring the sniper bot — Flask-based single-page app.

Provides:
- Live equity curve chart
- Open positions table
- Recent trades table
- Bot status and risk metrics
- Cycle log analysis
"""
from __future__ import annotations

import json
from datetime import datetime, timezone
from pathlib import Path

from flask import Flask, jsonify, render_template_string
from sqlalchemy import desc, func, select

from sniper_bot.config import AppConfig, load_config, resolve_path
from sniper_bot.logging_config import get_logger
from sniper_bot.storage import (
    BotState,
    CycleLog,
    Database,
    EquitySnapshot,
    Position,
    RiskEvent,
    get_open_positions,
    get_or_create_state,
)

LOGGER = get_logger(__name__)

DASHBOARD_HTML = """<!DOCTYPE html>
<html lang="en">
<head>
<meta charset="UTF-8">
<meta name="viewport" content="width=device-width, initial-scale=1.0">
<title>Sniper Bot Dashboard</title>
<script src="https://cdn.jsdelivr.net/npm/chart.js@4"></script>
<style>
* { margin: 0; padding: 0; box-sizing: border-box; }
body { font-family: 'Segoe UI', system-ui, sans-serif; background: #0d1117; color: #c9d1d9; }
.header { background: #161b22; padding: 16px 24px; border-bottom: 1px solid #30363d; display: flex; justify-content: space-between; align-items: center; }
.header h1 { font-size: 20px; color: #58a6ff; }
.header .status { padding: 4px 12px; border-radius: 12px; font-size: 13px; font-weight: 600; }
.status-running { background: #238636; color: #fff; }
.status-halted { background: #da3633; color: #fff; }
.status-idle { background: #6e7681; color: #fff; }
.grid { display: grid; grid-template-columns: repeat(auto-fit, minmax(300px, 1fr)); gap: 16px; padding: 16px 24px; }
.card { background: #161b22; border: 1px solid #30363d; border-radius: 8px; padding: 16px; }
.card h2 { font-size: 14px; color: #8b949e; margin-bottom: 12px; text-transform: uppercase; letter-spacing: 0.5px; }
.metric { font-size: 28px; font-weight: 700; }
.metric.positive { color: #3fb950; }
.metric.negative { color: #f85149; }
.metric.neutral { color: #c9d1d9; }
.metric-row { display: flex; justify-content: space-between; margin: 4px 0; }
.metric-label { color: #8b949e; font-size: 13px; }
.metric-value { font-size: 13px; font-weight: 600; }
.chart-card { grid-column: 1 / -1; }
.chart-container { height: 300px; position: relative; }
table { width: 100%; border-collapse: collapse; font-size: 13px; }
th { text-align: left; color: #8b949e; font-weight: 600; padding: 8px 6px; border-bottom: 1px solid #30363d; }
td { padding: 8px 6px; border-bottom: 1px solid #21262d; }
.pnl-positive { color: #3fb950; }
.pnl-negative { color: #f85149; }
.refresh-info { text-align: center; color: #6e7681; font-size: 12px; padding: 8px; }
</style>
</head>
<body>
<div class="header">
    <h1>Sniper Bot Dashboard</h1>
    <div>
        <span class="status" id="bot-status">Loading...</span>
        <span style="color:#8b949e; font-size:12px; margin-left:12px;" id="mode-label"></span>
    </div>
</div>

<div class="grid">
    <div class="card">
        <h2>Equity</h2>
        <div class="metric" id="equity">--</div>
        <div class="metric-row"><span class="metric-label">Cash</span><span class="metric-value" id="cash">--</span></div>
        <div class="metric-row"><span class="metric-label">Positions</span><span class="metric-value" id="pos-value">--</span></div>
        <div class="metric-row"><span class="metric-label">High Water Mark</span><span class="metric-value" id="hwm">--</span></div>
    </div>
    <div class="card">
        <h2>Risk</h2>
        <div class="metric" id="drawdown">--</div>
        <div class="metric-row"><span class="metric-label">Daily P&L</span><span class="metric-value" id="daily-pnl">--</span></div>
        <div class="metric-row"><span class="metric-label">Consecutive Losses</span><span class="metric-value" id="consec-losses">--</span></div>
        <div class="metric-row"><span class="metric-label">Cooldown Until</span><span class="metric-value" id="cooldown">--</span></div>
    </div>
    <div class="card">
        <h2>Performance</h2>
        <div class="metric-row"><span class="metric-label">Total Trades</span><span class="metric-value" id="total-trades">--</span></div>
        <div class="metric-row"><span class="metric-label">Win Rate</span><span class="metric-value" id="win-rate">--</span></div>
        <div class="metric-row"><span class="metric-label">Total P&L</span><span class="metric-value" id="total-pnl">--</span></div>
        <div class="metric-row"><span class="metric-label">Profit Factor</span><span class="metric-value" id="profit-factor">--</span></div>
    </div>

    <div class="card chart-card">
        <h2>Equity Curve</h2>
        <div class="chart-container"><canvas id="equity-chart"></canvas></div>
    </div>

    <div class="card" style="grid-column: 1 / -1;">
        <h2>Open Positions</h2>
        <table>
            <thead><tr><th>Symbol</th><th>Entry</th><th>Current</th><th>Qty</th><th>Invested</th><th>Unrealized</th><th>Stop</th><th>Since</th></tr></thead>
            <tbody id="positions-body"></tbody>
        </table>
    </div>

    <div class="card" style="grid-column: 1 / -1;">
        <h2>Recent Trades</h2>
        <table>
            <thead><tr><th>Symbol</th><th>Entry</th><th>Exit</th><th>P&L</th><th>P&L %</th><th>Reason</th><th>Hold</th></tr></thead>
            <tbody id="trades-body"></tbody>
        </table>
    </div>
</div>

<div class="refresh-info">Auto-refreshes every 10 seconds</div>

<script>
let equityChart = null;

async function fetchData() {
    try {
        const [status, equity, trades, positions] = await Promise.all([
            fetch('/api/status').then(r => r.json()),
            fetch('/api/equity').then(r => r.json()),
            fetch('/api/trades').then(r => r.json()),
            fetch('/api/positions').then(r => r.json()),
        ]);

        // Status
        const statusEl = document.getElementById('bot-status');
        statusEl.textContent = status.status || 'IDLE';
        statusEl.className = 'status status-' + (status.status || 'idle').toLowerCase();
        document.getElementById('mode-label').textContent = status.mode || '';

        // Equity card
        const eq = status.equity || 0;
        const eqEl = document.getElementById('equity');
        eqEl.textContent = eq.toFixed(2) + ' USDT';
        eqEl.className = 'metric ' + (eq > (status.initial_equity || 0) ? 'positive' : eq < (status.initial_equity || 0) ? 'negative' : 'neutral');
        document.getElementById('cash').textContent = (status.cash || 0).toFixed(2);
        document.getElementById('pos-value').textContent = (status.position_value || 0).toFixed(2);
        document.getElementById('hwm').textContent = (status.hwm || 0).toFixed(2);

        // Risk card
        const dd = status.drawdown_pct || 0;
        const ddEl = document.getElementById('drawdown');
        ddEl.textContent = 'DD: ' + (dd * 100).toFixed(1) + '%';
        ddEl.className = 'metric ' + (dd > 0.10 ? 'negative' : dd > 0.05 ? 'neutral' : 'positive');
        document.getElementById('daily-pnl').textContent = (status.daily_pnl || 0).toFixed(2);
        document.getElementById('consec-losses').textContent = status.consecutive_losses || 0;
        document.getElementById('cooldown').textContent = status.cooldown_until || 'None';

        // Performance
        document.getElementById('total-trades').textContent = status.total_trades || 0;
        document.getElementById('win-rate').textContent = ((status.win_rate || 0) * 100).toFixed(1) + '%';
        const tpnl = status.total_pnl || 0;
        const tpnlEl = document.getElementById('total-pnl');
        tpnlEl.textContent = tpnl.toFixed(2);
        tpnlEl.className = 'metric-value ' + (tpnl >= 0 ? 'pnl-positive' : 'pnl-negative');
        document.getElementById('profit-factor').textContent = (status.profit_factor || 0).toFixed(2);

        // Equity chart
        if (equity.length > 0) {
            const labels = equity.map(e => e.time);
            const values = equity.map(e => e.equity);
            if (!equityChart) {
                equityChart = new Chart(document.getElementById('equity-chart'), {
                    type: 'line',
                    data: { labels, datasets: [{ label: 'Equity', data: values, borderColor: '#58a6ff', backgroundColor: 'rgba(88,166,255,0.1)', fill: true, tension: 0.3, pointRadius: 0 }] },
                    options: { responsive: true, maintainAspectRatio: false, scales: { x: { display: false }, y: { grid: { color: '#21262d' }, ticks: { color: '#8b949e' } } }, plugins: { legend: { display: false } } }
                });
            } else {
                equityChart.data.labels = labels;
                equityChart.data.datasets[0].data = values;
                equityChart.update('none');
            }
        }

        // Open positions
        const posBody = document.getElementById('positions-body');
        posBody.innerHTML = positions.map(p => {
            const unrealized = ((p.current_price - p.entry_price) / p.entry_price * 100).toFixed(2);
            const cls = unrealized >= 0 ? 'pnl-positive' : 'pnl-negative';
            return `<tr><td>${p.symbol}</td><td>${p.entry_price.toFixed(6)}</td><td>${p.current_price.toFixed(6)}</td><td>${p.quantity.toFixed(4)}</td><td>${p.usdt_invested.toFixed(2)}</td><td class="${cls}">${unrealized}%</td><td>${p.stop_price.toFixed(6)}</td><td>${p.entry_time}</td></tr>`;
        }).join('') || '<tr><td colspan="8" style="text-align:center;color:#6e7681">No open positions</td></tr>';

        // Recent trades
        const tradesBody = document.getElementById('trades-body');
        tradesBody.innerHTML = trades.map(t => {
            const cls = t.pnl >= 0 ? 'pnl-positive' : 'pnl-negative';
            return `<tr><td>${t.symbol}</td><td>${t.entry_price.toFixed(6)}</td><td>${t.exit_price.toFixed(6)}</td><td class="${cls}">${t.pnl.toFixed(2)}</td><td class="${cls}">${(t.pnl_pct * 100).toFixed(1)}%</td><td>${t.exit_reason}</td><td>${t.hold_hours.toFixed(1)}h</td></tr>`;
        }).join('') || '<tr><td colspan="7" style="text-align:center;color:#6e7681">No trades yet</td></tr>';

    } catch (err) {
        console.error('Dashboard fetch error:', err);
    }
}

fetchData();
setInterval(fetchData, 10000);
</script>
</body>
</html>"""


def create_dashboard_app(config_path: Path, mode: str = "paper") -> Flask:
    """Create and configure the Flask dashboard app."""
    app = Flask(__name__)
    config = load_config(config_path)
    base_dir = config_path.parent.resolve()
    db_path = resolve_path(base_dir, config.database_path_for_mode(mode))
    db = Database(db_path)

    @app.route("/")
    def index():
        return render_template_string(DASHBOARD_HTML)

    @app.route("/api/status")
    def api_status():
        with db.session() as session:
            state = get_or_create_state(session, mode)
            open_pos = get_open_positions(session, mode)

            # Compute performance stats
            closed = list(session.scalars(
                select(Position)
                .where(Position.mode == mode, Position.status == "closed")
                .order_by(desc(Position.exit_time))
            ).all())
            wins = [p for p in closed if (p.realized_pnl or 0) > 0]
            losses = [p for p in closed if (p.realized_pnl or 0) <= 0]
            total_pnl = sum(p.realized_pnl or 0 for p in closed)
            gross_wins = sum(p.realized_pnl or 0 for p in wins)
            gross_losses = abs(sum(p.realized_pnl or 0 for p in losses)) or 0.01

            equity = float(state.last_equity or state.usdt_balance or 0)
            hwm = float(state.high_water_mark or equity)
            dd = (hwm - equity) / hwm if hwm > 0 else 0

            return jsonify({
                "mode": mode,
                "status": state.status or "IDLE",
                "equity": equity,
                "cash": float(state.usdt_balance or 0),
                "position_value": float(state.position_value or 0),
                "hwm": hwm,
                "drawdown_pct": round(dd, 4),
                "daily_pnl": float(state.daily_realized_pnl or 0),
                "consecutive_losses": int(state.consecutive_losses or 0),
                "cooldown_until": state.cooldown_until.isoformat() if state.cooldown_until else None,
                "initial_equity": config.risk.initial_paper_cash,
                "total_trades": len(closed),
                "win_rate": round(len(wins) / len(closed), 4) if closed else 0,
                "total_pnl": round(total_pnl, 2),
                "profit_factor": round(gross_wins / gross_losses, 2) if gross_losses > 0 else 0,
                "open_positions_count": len(open_pos),
            })

    @app.route("/api/equity")
    def api_equity():
        with db.session() as session:
            snaps = list(session.scalars(
                select(EquitySnapshot)
                .where(EquitySnapshot.mode == mode)
                .order_by(EquitySnapshot.recorded_at)
                .limit(2000)
            ).all())
            return jsonify([
                {
                    "time": s.recorded_at.isoformat() if s.recorded_at else "",
                    "equity": float(s.equity or 0),
                    "drawdown": float(s.drawdown_pct or 0),
                }
                for s in snaps
            ])

    @app.route("/api/positions")
    def api_positions():
        with db.session() as session:
            positions = get_open_positions(session, mode)
            # Get current prices from latest equity context
            result = []
            for p in positions:
                entry_price = float(p.entry_price or 0)
                result.append({
                    "symbol": p.symbol,
                    "entry_price": entry_price,
                    "current_price": float(p.max_price or entry_price),  # approx
                    "quantity": float(p.quantity or 0),
                    "usdt_invested": float(p.usdt_invested or 0),
                    "stop_price": float(p.stop_price or 0),
                    "max_price": float(p.max_price or 0),
                    "entry_time": p.entry_time.isoformat() if p.entry_time else "",
                })
            return jsonify(result)

    @app.route("/api/trades")
    def api_trades():
        with db.session() as session:
            closed = list(session.scalars(
                select(Position)
                .where(Position.mode == mode, Position.status == "closed")
                .order_by(desc(Position.exit_time))
                .limit(50)
            ).all())
            return jsonify([
                {
                    "symbol": p.symbol,
                    "entry_price": float(p.entry_price or 0),
                    "exit_price": float(p.exit_price or 0),
                    "pnl": float(p.realized_pnl or 0),
                    "pnl_pct": float(p.realized_pnl_pct or 0),
                    "exit_reason": p.exit_reason or "",
                    "hold_hours": round(
                        (p.exit_time - p.entry_time).total_seconds() / 3600, 1
                    ) if p.exit_time and p.entry_time else 0,
                    "entry_time": p.entry_time.isoformat() if p.entry_time else "",
                    "exit_time": p.exit_time.isoformat() if p.exit_time else "",
                }
                for p in closed
            ])

    @app.route("/api/risk-events")
    def api_risk_events():
        with db.session() as session:
            events = list(session.scalars(
                select(RiskEvent)
                .where(RiskEvent.mode == mode)
                .order_by(desc(RiskEvent.created_at))
                .limit(20)
            ).all())
            return jsonify([
                {
                    "type": e.event_type,
                    "message": e.message,
                    "severity": e.severity,
                    "time": e.created_at.isoformat() if e.created_at else "",
                }
                for e in events
            ])

    return app
