from __future__ import annotations

import json
from datetime import date
from pathlib import Path

import typer

from sniper_bot import __version__
from sniper_bot.app import backfill, backtest, healthcheck, reset_drawdown, run_bot, send_summary, status


app = typer.Typer(add_completion=False, no_args_is_help=True)


@app.callback()
def main_callback() -> None:
    """Sniper bot CLI."""


@app.command("version")
def version() -> None:
    typer.echo(__version__)


@app.command("backfill")
def backfill_command(
    config: Path = typer.Option(Path("config/example.yaml"), exists=True, readable=True),
    limit: int = typer.Option(720, min=10, max=1000),
) -> None:
    typer.echo(json.dumps(backfill(config, limit), indent=2))


@app.command("backtest")
def backtest_command(
    config: Path = typer.Option(Path("config/example.yaml"), exists=True, readable=True),
    csv_path: Path | None = typer.Option(None),
) -> None:
    typer.echo(json.dumps(backtest(config, csv_path), indent=2))


@app.command("run-paper")
def run_paper_command(
    config: Path = typer.Option(Path("config/example.yaml"), exists=True, readable=True),
    once: bool = typer.Option(False),
) -> None:
    run_bot(config, mode="paper", once=once)


@app.command("run-demo")
def run_demo_command(
    config: Path = typer.Option(Path("config/example.yaml"), exists=True, readable=True),
    once: bool = typer.Option(False),
) -> None:
    run_bot(config, mode="demo", once=once)


@app.command("run-live")
def run_live_command(
    config: Path = typer.Option(Path("config/example.yaml"), exists=True, readable=True),
    confirm_live: bool = typer.Option(False, "--confirm-live"),
    once: bool = typer.Option(False),
) -> None:
    run_bot(config, mode="live", once=once, confirm_live=confirm_live)


@app.command("status")
def status_command(
    config: Path = typer.Option(Path("config/example.yaml"), exists=True, readable=True),
    mode: str = typer.Option("paper"),
) -> None:
    typer.echo(json.dumps(status(config, mode), indent=2))


@app.command("healthcheck")
def healthcheck_command(
    config: Path = typer.Option(Path("config/example.yaml"), exists=True, readable=True),
) -> None:
    typer.echo(json.dumps(healthcheck(config), indent=2))


@app.command("send-summary")
def send_summary_command(
    config: Path = typer.Option(Path("config/example.yaml"), exists=True, readable=True),
    mode: str = typer.Option("paper"),
    summary_date: str | None = typer.Option(None),
) -> None:
    parsed_date = date.fromisoformat(summary_date) if summary_date else None
    typer.echo(json.dumps(send_summary(config, mode, parsed_date), indent=2))


@app.command("reset-drawdown")
def reset_drawdown_command(
    config: Path = typer.Option(Path("config/example.yaml"), exists=True, readable=True),
    mode: str = typer.Option("paper"),
) -> None:
    typer.echo(json.dumps(reset_drawdown(config, mode), indent=2))


def main() -> None:
    app()
