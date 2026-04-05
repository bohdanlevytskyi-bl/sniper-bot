from __future__ import annotations

import json
from pathlib import Path

import typer

app = typer.Typer(name="sniper-bot", help="Autonomous Bybit multi-pair momentum trading bot.")


@app.command()
def scan(
    config: Path = typer.Option("config/example.yaml", "--config", "-c", help="Path to config YAML"),
) -> None:
    """One-shot scan: show top momentum pairs with scores."""
    from sniper_bot.app import create_runtime, scan_once

    runtime = create_runtime(config)
    try:
        results = scan_once(runtime)
        if not results:
            typer.echo("No momentum signals found.")
            return
        typer.echo(f"{'Symbol':<14} {'Score':>6} {'Vol':>6} {'Mom':>6} {'RS':>6} {'Spike':>7} {'Price':>12}")
        typer.echo("-" * 70)
        for r in results:
            typer.echo(
                f"{r['symbol']:<14} {r['score']:>6.3f} {r['volume_score']:>6.3f} "
                f"{r['momentum_score']:>6.3f} {r['rs_score']:>6.3f} {r['spike_ratio']:>7.2f} "
                f"{r['price']:>12.6f}"
            )
    finally:
        runtime.close()


@app.command()
def run(
    config: Path = typer.Option("config/example.yaml", "--config", "-c", help="Path to config YAML"),
    live: bool = typer.Option(False, "--live", help="Run in live mode"),
    confirm_live: bool = typer.Option(False, "--confirm-live", help="Confirm live trading"),
    demo: bool = typer.Option(False, "--demo", help="Run in demo mode"),
    once: bool = typer.Option(False, "--once", help="Run a single cycle then exit"),
) -> None:
    """Start the trading bot (default: paper mode)."""
    from sniper_bot.app import run_bot

    mode = "live" if live else "demo" if demo else "paper"
    run_bot(config, mode=mode, once=once, confirm_live=confirm_live)


@app.command()
def status(
    config: Path = typer.Option("config/example.yaml", "--config", "-c", help="Path to config YAML"),
    mode: str = typer.Option("paper", "--mode", "-m", help="Mode: paper, demo, live"),
) -> None:
    """Show current bot status, positions, and P&L."""
    from sniper_bot.app import create_runtime, get_status

    runtime = create_runtime(config, mode=mode)
    try:
        result = get_status(runtime)
        typer.echo(json.dumps(result, indent=2, default=str))
    finally:
        runtime.close()


@app.command()
def healthcheck(
    config: Path = typer.Option("config/example.yaml", "--config", "-c", help="Path to config YAML"),
) -> None:
    """Test Bybit + Telegram connectivity."""
    from sniper_bot.app import create_runtime, healthcheck as hc

    runtime = create_runtime(config)
    try:
        report = hc(runtime)
        typer.echo(json.dumps(report, indent=2, default=str))
    finally:
        runtime.close()


@app.command()
def balance(
    config: Path = typer.Option("config/example.yaml", "--config", "-c", help="Path to config YAML"),
    mode: str = typer.Option("paper", "--mode", "-m", help="Mode: paper, demo, live"),
) -> None:
    """Show real account balance from Bybit (demo/live) or paper DB."""
    from sniper_bot.app import create_runtime, get_balance

    require_private = mode in {"demo", "live"}
    runtime = create_runtime(config, mode=mode, require_private=require_private)
    try:
        result = get_balance(runtime)
        typer.echo(json.dumps(result, indent=2, default=str))
    finally:
        runtime.close()


@app.command(name="reset-drawdown")
def reset_drawdown_cmd(
    config: Path = typer.Option("config/example.yaml", "--config", "-c", help="Path to config YAML"),
    mode: str = typer.Option("paper", "--mode", "-m", help="Mode: paper, demo, live"),
) -> None:
    """Clear drawdown halt and resume trading."""
    from sniper_bot.app import create_runtime, reset_drawdown

    runtime = create_runtime(config, mode=mode)
    try:
        result = reset_drawdown(runtime)
        typer.echo(json.dumps(result, indent=2))
    finally:
        runtime.close()


@app.command()
def report(
    config: Path = typer.Option("config/example.yaml", "--config", "-c", help="Path to config YAML"),
    mode: str = typer.Option("paper", "--mode", "-m", help="Mode: paper, demo, live"),
    cycles: int = typer.Option(100, "--cycles", "-n", help="Number of recent cycles to analyze"),
) -> None:
    """Analyze recent scan cycles to help tune strategy thresholds."""
    from sniper_bot.app import create_runtime
    from sniper_bot.storage import CycleLog
    from sqlalchemy import desc, select

    runtime = create_runtime(config, mode=mode)
    try:
        with runtime.db.session() as session:
            rows = list(session.scalars(
                select(CycleLog)
                .where(CycleLog.mode == mode)
                .order_by(desc(CycleLog.recorded_at))
                .limit(cycles)
            ).all())

        if not rows:
            typer.echo("No cycle logs found. Run the bot first.")
            return

        scores = [r.top_score for r in rows]
        actions: dict[str, int] = {}
        symbol_freq: dict[str, int] = {}

        for r in rows:
            actions[r.entry_action] = actions.get(r.entry_action, 0) + 1
            for c in (r.top_candidates or []):
                sym = c["symbol"]
                symbol_freq[sym] = symbol_freq.get(sym, 0) + 1

        avg_score = sum(scores) / len(scores)
        max_score = max(scores)
        min_score = min(scores)
        above_06 = sum(1 for s in scores if s >= 0.6)
        above_03 = sum(1 for s in scores if s >= 0.3)

        typer.echo(f"\n=== Strategy Report ({len(rows)} cycles) ===\n")
        typer.echo(f"Top score seen:       {max_score:.4f}")
        typer.echo(f"Average top score:    {avg_score:.4f}")
        typer.echo(f"Min top score:        {min_score:.4f}")
        typer.echo(f"Cycles score >= 0.6:  {above_06} ({above_06/len(rows):.0%})")
        typer.echo(f"Cycles score >= 0.3:  {above_03} ({above_03/len(rows):.0%})")

        typer.echo(f"\nEntry actions:")
        for action, count in sorted(actions.items(), key=lambda x: -x[1]):
            typer.echo(f"  {action:<20} {count:>5} ({count/len(rows):.0%})")

        top_symbols = sorted(symbol_freq.items(), key=lambda x: -x[1])[:10]
        typer.echo(f"\nTop 10 most-seen candidates:")
        typer.echo(f"  {'Symbol':<14} {'Appearances':>12}")
        for sym, freq in top_symbols:
            typer.echo(f"  {sym:<14} {freq:>12}")

        typer.echo(f"\nSuggested min_entry_score: {max(0.05, avg_score * 0.8):.2f}")
        typer.echo(f"  (80% of average top score — adjust to taste)\n")
    finally:
        runtime.close()


@app.command()
def analyze(
    config: Path = typer.Option("config/example.yaml", "--config", "-c", help="Path to config YAML"),
    mode: str = typer.Option("paper", "--mode", "-m", help="Mode: paper, demo, live"),
    cycles: int = typer.Option(500, "--cycles", "-n", help="Number of recent cycles to analyze"),
) -> None:
    """AI-powered strategy analysis using OpenAI. Requires OPENAI_API_KEY in .env."""
    from sniper_bot.ai_advisor import gather_and_analyze
    from sniper_bot.app import create_runtime
    from sniper_bot.config import get_optional_secret

    runtime = create_runtime(config, mode=mode)
    try:
        api_key = get_optional_secret("OPENAI_API_KEY")
        if not api_key:
            typer.echo("Error: OPENAI_API_KEY not found in .env")
            typer.echo("Add OPENAI_API_KEY=sk-... to your .env file")
            raise typer.Exit(1)

        typer.echo("Gathering trading data...")

        current_config = {
            "strategy": runtime.config.strategy.model_dump(),
            "position": runtime.config.position.model_dump(),
            "risk": runtime.config.risk.model_dump(),
            "scanner": runtime.config.scanner.model_dump(),
        }

        model = runtime.config.auto_tune.openai_model
        typer.echo(f"Sending to OpenAI ({model}) for analysis...")
        result = gather_and_analyze(runtime.db, mode, api_key, current_config, model, cycles)

        if "error" in result:
            typer.echo(f"Error: {result['error']}")
            raise typer.Exit(1)

        typer.echo(f"\n--- Data: {result['data_summary']['cycles_analyzed']} cycles, "
                   f"{result['data_summary']['trades_analyzed']} trades ---\n")

        health = result.get("health_score", {})
        if health:
            typer.echo(f"Strategy Health Score: {health.get('composite', 0)}/100")
            comps = health.get("components", {})
            typer.echo(f"  Profit Factor: {health.get('profit_factor', 0)} ({comps.get('profit_factor_pts', 0)}/25 pts)")
            typer.echo(f"  Risk-Adjusted: {health.get('risk_adjusted', 0)} ({comps.get('risk_adjusted_pts', 0)}/25 pts)")
            typer.echo(f"  Capture Eff:   {health.get('capture_efficiency', 0):.1%} ({comps.get('capture_pts', 0)}/20 pts)")
            typer.echo(f"  Trade Freq:    {health.get('trade_frequency_per_100_cycles', 0)}/100cyc ({comps.get('frequency_pts', 0)}/15 pts)")
            typer.echo(f"  DD Penalty:    {health.get('max_drawdown_pct', 0):.1%} (-{comps.get('drawdown_penalty', 0)} pts)")
            typer.echo()

        typer.echo(f"Assessment: {result.get('assessment', 'N/A')}\n")
        typer.echo(f"Confidence: {result.get('confidence', 'N/A')}\n")

        proposed = result.get("proposed_changes", {})
        if proposed:
            typer.echo("Proposed changes:")
            for key, change in proposed.items():
                current = change.get("current", "?")
                new = change.get("proposed", "?")
                reason = change.get("reason", "")
                typer.echo(f"  {key}: {current} → {new}")
                typer.echo(f"    Reason: {reason}")
        else:
            typer.echo("No parameter changes proposed.")

        warnings = result.get("warnings", [])
        if warnings:
            typer.echo(f"\nWarnings:")
            for w in warnings:
                typer.echo(f"  - {w}")
    finally:
        runtime.close()


@app.command(name="tune-history")
def tune_history_cmd(
    config: Path = typer.Option("config/example.yaml", "--config", "-c", help="Path to config YAML"),
    mode: str = typer.Option("paper", "--mode", "-m", help="Mode: paper, demo, live"),
    limit: int = typer.Option(10, "--limit", "-n", help="Number of recent tune events"),
) -> None:
    """Show recent AI auto-tune decisions."""
    from sqlalchemy import desc, select

    from sniper_bot.app import create_runtime
    from sniper_bot.storage import AITuneLog

    runtime = create_runtime(config, mode=mode)
    try:
        with runtime.db.session() as session:
            rows = list(session.scalars(
                select(AITuneLog)
                .where(AITuneLog.mode == mode)
                .order_by(desc(AITuneLog.recorded_at))
                .limit(limit)
            ).all())

        if not rows:
            typer.echo("No auto-tune history found.")
            return

        for r in rows:
            typer.echo(f"\n{'='*60}")
            typer.echo(f"Cycle {r.trigger_cycle} | {r.recorded_at} | {r.status} | {r.confidence}")
            typer.echo(f"Model: {r.model_used}")
            if r.reasoning:
                typer.echo(f"Assessment: {r.reasoning[:200]}")
            applied = r.applied_changes or {}
            if applied:
                typer.echo(f"Applied changes:")
                for key, change in applied.items():
                    typer.echo(f"  {key}: {change.get('old')} → {change.get('new')}")
            rejected = r.rejected_changes or {}
            if rejected:
                typer.echo(f"Rejected: {list(rejected.keys())}")
    finally:
        runtime.close()


@app.command()
def rollback(
    config: Path = typer.Option("config/example.yaml", "--config", "-c", help="Path to config YAML"),
    mode: str = typer.Option("paper", "--mode", "-m", help="Mode: paper, demo, live"),
) -> None:
    """Rollback to the last pre-tune config snapshot."""
    from sqlalchemy import desc, select

    from sniper_bot.ai_advisor import restore_config_from_snapshot
    from sniper_bot.app import create_runtime
    from sniper_bot.storage import PreTuneSnapshot

    runtime = create_runtime(config, mode=mode)
    try:
        with runtime.db.session() as session:
            snap = session.scalar(
                select(PreTuneSnapshot)
                .where(PreTuneSnapshot.mode == mode, PreTuneSnapshot.rolled_back == 0)
                .order_by(desc(PreTuneSnapshot.recorded_at))
                .limit(1)
            )
            if snap is None:
                typer.echo("No pre-tune snapshot found to rollback to.")
                raise typer.Exit(1)

            typer.echo(f"Snapshot from cycle {snap.trigger_cycle} ({snap.recorded_at})")
            typer.echo(f"Config: {json.dumps(snap.config_snapshot, indent=2)}")

            if not typer.confirm("Restore this config?"):
                typer.echo("Cancelled.")
                raise typer.Exit(0)

            restore_config_from_snapshot(runtime.config, snap.config_snapshot)
            snap.rolled_back = 1
            session.commit()

        typer.echo("Config restored from snapshot. Restart the bot to apply.")
    finally:
        runtime.close()


@app.command()
def export(
    config: Path = typer.Option("config/example.yaml", "--config", "-c", help="Path to config YAML"),
    mode: str = typer.Option("paper", "--mode", "-m", help="Mode: paper, demo, live"),
    output: Path = typer.Option("export", "--output", "-o", help="Output directory for CSV files"),
) -> None:
    """Export cycle logs, positions, and equity snapshots to CSV for analysis."""
    import csv

    from sqlalchemy import select

    from sniper_bot.app import create_runtime
    from sniper_bot.storage import CycleLog, EquitySnapshot, Position

    runtime = create_runtime(config, mode=mode)
    try:
        output.mkdir(parents=True, exist_ok=True)

        with runtime.db.session() as session:
            # Export cycle logs
            cycle_rows = list(session.scalars(
                select(CycleLog).where(CycleLog.mode == mode).order_by(CycleLog.recorded_at)
            ).all())

            if cycle_rows:
                cycle_path = output / "cycle_logs.csv"
                with open(cycle_path, "w", newline="", encoding="utf-8") as f:
                    writer = csv.writer(f)
                    writer.writerow([
                        "recorded_at", "candidates_scanned", "top_score", "entry_action",
                        "entry_symbol", "block_reason", "btc_price", "btc_change_24h_pct",
                        "market_breadth_pct", "total_tickers", "equity", "open_positions_count",
                        "top_3_candidates",
                    ])
                    for r in cycle_rows:
                        top3 = r.top_candidates[:3] if r.top_candidates else []
                        top3_str = "; ".join(
                            f"{c.get('symbol', '?')}={c.get('composite_score', 0):.4f}"
                            for c in top3
                        )
                        writer.writerow([
                            r.recorded_at, r.candidates_scanned, r.top_score, r.entry_action,
                            r.entry_symbol or "", r.block_reason or "",
                            r.btc_price or "", r.btc_change_24h_pct or "",
                            r.market_breadth_pct or "", r.total_tickers or "",
                            r.equity or "", r.open_positions_count or "",
                            top3_str,
                        ])
                typer.echo(f"Exported {len(cycle_rows)} cycle logs to {cycle_path}")

            # Export positions
            pos_rows = list(session.scalars(
                select(Position).where(Position.mode == mode).order_by(Position.entry_time)
            ).all())

            if pos_rows:
                pos_path = output / "positions.csv"
                with open(pos_path, "w", newline="", encoding="utf-8") as f:
                    writer = csv.writer(f)
                    writer.writerow([
                        "symbol", "status", "entry_price", "exit_price", "quantity",
                        "usdt_invested", "usdt_received", "realized_pnl", "realized_pnl_pct",
                        "exit_reason", "entry_time", "exit_time", "notes",
                    ])
                    for p in pos_rows:
                        writer.writerow([
                            p.symbol, p.status, p.entry_price, p.exit_price or "",
                            p.quantity, p.usdt_invested, p.usdt_received or "",
                            p.realized_pnl or "", p.realized_pnl_pct or "",
                            p.exit_reason or "", p.entry_time, p.exit_time or "",
                            json.dumps(p.notes) if p.notes else "",
                        ])
                typer.echo(f"Exported {len(pos_rows)} positions to {pos_path}")

            # Export equity snapshots
            eq_rows = list(session.scalars(
                select(EquitySnapshot).where(EquitySnapshot.mode == mode).order_by(EquitySnapshot.recorded_at)
            ).all())

            if eq_rows:
                eq_path = output / "equity.csv"
                with open(eq_path, "w", newline="", encoding="utf-8") as f:
                    writer = csv.writer(f)
                    writer.writerow([
                        "recorded_at", "equity", "cash_balance", "position_value",
                        "high_water_mark", "drawdown_pct",
                    ])
                    for e in eq_rows:
                        writer.writerow([
                            e.recorded_at, e.equity, e.cash_balance,
                            e.position_value, e.high_water_mark, e.drawdown_pct,
                        ])
                typer.echo(f"Exported {len(eq_rows)} equity snapshots to {eq_path}")

        if not any([cycle_rows, pos_rows, eq_rows]):
            typer.echo("No data to export. Run the bot first.")
    finally:
        runtime.close()


@app.command()
def backtest(
    config: Path = typer.Option("config/example.yaml", "--config", "-c", help="Path to config YAML"),
    hours: int = typer.Option(168, "--hours", "-H", help="Lookback period in hours (default: 7 days)"),
    symbols: str = typer.Option(
        "BTCUSDT,ETHUSDT,SOLUSDT,XRPUSDT,DOGEUSDT,AVAXUSDT,LINKUSDT,ADAUSDT",
        "--symbols", "-s", help="Comma-separated symbols to backtest",
    ),
) -> None:
    """Run a backtest over historical data using current config."""
    from sniper_bot.app import create_runtime
    from sniper_bot.backtest import run_backtest

    runtime = create_runtime(config)
    try:
        symbol_list = [s.strip() for s in symbols.split(",") if s.strip()]
        typer.echo(f"Backtesting {len(symbol_list)} symbols over {hours}h...")

        result = run_backtest(runtime.bybit, runtime.config, symbol_list, lookback_hours=hours)

        typer.echo(f"\n{'='*60}")
        typer.echo(result.summary())
        typer.echo(f"{'='*60}")

        typer.echo(f"\nEquity: {result.initial_equity:.2f} → {result.final_equity:.2f}")
        typer.echo(f"Trades: {result.wins}W / {result.losses}L")

        if result.trades:
            typer.echo(f"\nTrade details:")
            typer.echo(f"  {'Symbol':<12} {'PnL':>8} {'PnL%':>7} {'Exit':>15} {'Hold':>6} {'Score':>6}")
            typer.echo(f"  {'-'*58}")
            for t in result.trades[:30]:
                typer.echo(
                    f"  {t.symbol:<12} {t.pnl:>+8.2f} {t.pnl_pct:>+6.1%} "
                    f"{t.exit_reason:>15} {t.hold_hours:>5.1f}h {t.entry_score:>6.3f}"
                )

        # Exit analysis
        if result.trades:
            exit_reasons: dict[str, list[float]] = {}
            for t in result.trades:
                exit_reasons.setdefault(t.exit_reason, []).append(t.pnl)
            typer.echo(f"\nExit analysis:")
            for reason, pnls in sorted(exit_reasons.items(), key=lambda x: sum(x[1]), reverse=True):
                typer.echo(
                    f"  {reason:<20} {len(pnls):>3} trades  "
                    f"PnL: {sum(pnls):>+8.2f}  "
                    f"Avg: {sum(pnls)/len(pnls):>+6.2f}"
                )
    finally:
        runtime.close()


@app.command()
def dashboard(
    config: Path = typer.Option("config/example.yaml", "--config", "-c", help="Path to config YAML"),
    mode: str = typer.Option("paper", "--mode", "-m", help="Mode: paper, demo, live"),
    port: int = typer.Option(8080, "--port", "-p", help="Dashboard port"),
    host: str = typer.Option("127.0.0.1", "--host", help="Dashboard host"),
) -> None:
    """Launch the web dashboard for monitoring."""
    from sniper_bot.dashboard import create_dashboard_app

    flask_app = create_dashboard_app(config, mode=mode)
    typer.echo(f"Dashboard running at http://{host}:{port}")
    flask_app.run(host=host, port=port, debug=False)


@app.command()
def version() -> None:
    """Show version."""
    from sniper_bot import __version__
    typer.echo(f"sniper-bot {__version__}")


def main() -> None:
    app()
