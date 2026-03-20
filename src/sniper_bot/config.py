from __future__ import annotations

import os
from pathlib import Path
from typing import Any

import yaml
from pydantic import BaseModel, Field, model_validator


class PathsConfig(BaseModel):
    paper_db: Path = Path("data/bybit-paper.sqlite")
    demo_db: Path = Path("data/bybit-demo.sqlite")
    live_db: Path = Path("data/bybit-live.sqlite")
    log_dir: Path = Path("logs")


class ExchangeConfig(BaseModel):
    environment: str = "demo"
    account_type: str = "UNIFIED"
    live_base_url: str = "https://api.bybit.com"
    demo_base_url: str = "https://api-demo.bybit.com"
    timeout_seconds: int = 20
    recv_window_ms: int = 5000
    user_agent: str = "sniper-bot/0.1.0"
    max_public_candles: int = 1000

    @property
    def base_url(self) -> str:
        return self.demo_base_url if self.environment == "demo" else self.live_base_url


class StrategyConfig(BaseModel):
    ema_fast: int = 50
    ema_slow: int = 200
    slope_lookback_bars: int = 3
    atr_period: int = 14
    atr_stop_multiple: float = 2.0
    bar_close_only: bool = True


class RiskConfig(BaseModel):
    max_position_pct: float = 0.25
    max_daily_loss_pct: float = 0.02
    max_drawdown_pct: float = 0.08
    cooldown_losses: int = 2
    cooldown_hours: int = 24
    initial_paper_cash: float = 10_000.0


class ExecutionConfig(BaseModel):
    quote_asset: str = "USDT"
    slippage_bps: float = 5.0
    fee_rate: float = 0.004
    poll_interval_seconds: int = 60


class AlertsConfig(BaseModel):
    enabled: bool = True


class AIConfig(BaseModel):
    enabled: bool = True
    model: str = "gpt-5-mini-2025-08-07"
    regime_lookback_bars: int = 48
    summary_hour: int = 0
    summary_minute: int = 5


class BacktestConfig(BaseModel):
    starting_cash: float = 10_000.0
    slippage_bps: float = 5.0
    fee_rate: float = 0.004


class AppConfig(BaseModel):
    mode: str = "paper"
    pair: str = "BTCUSDT"
    timeframe_minutes: int = 60
    timezone: str = "local"
    paths: PathsConfig = Field(default_factory=PathsConfig)
    exchange: ExchangeConfig = Field(default_factory=ExchangeConfig)
    strategy: StrategyConfig = Field(default_factory=StrategyConfig)
    risk: RiskConfig = Field(default_factory=RiskConfig)
    execution: ExecutionConfig = Field(default_factory=ExecutionConfig)
    alerts: AlertsConfig = Field(default_factory=AlertsConfig)
    ai: AIConfig = Field(default_factory=AIConfig)
    backtest: BacktestConfig = Field(default_factory=BacktestConfig)

    @model_validator(mode="after")
    def validate_values(self) -> "AppConfig":
        if self.mode not in {"paper", "demo", "live"}:
            raise ValueError("mode must be one of 'paper', 'demo', or 'live'")
        if self.exchange.environment not in {"demo", "live"}:
            raise ValueError("exchange.environment must be either 'demo' or 'live'")
        if self.exchange.account_type != "UNIFIED":
            raise ValueError("exchange.account_type must be UNIFIED for the MVP")
        if self.timeframe_minutes != 60:
            raise ValueError("timeframe_minutes must be 60 for the MVP")
        if self.pair != "BTCUSDT":
            raise ValueError("pair must be BTCUSDT for the MVP")
        return self

    @property
    def timeframe_label(self) -> str:
        return f"{self.timeframe_minutes}m"

    def database_path_for_mode(self, mode: str) -> Path:
        if mode == "demo":
            return self.paths.demo_db
        if mode == "live":
            return self.paths.live_db
        return self.paths.paper_db


def load_config(config_path: str | Path) -> AppConfig:
    path = Path(config_path)
    _load_dotenv(path.parent)
    raw = yaml.safe_load(path.read_text(encoding="utf-8")) or {}
    config = AppConfig.model_validate(raw)
    ensure_runtime_directories(config, path.parent)
    return config


def ensure_runtime_directories(config: AppConfig, root: Path) -> None:
    for path in (config.paths.paper_db, config.paths.demo_db, config.paths.live_db, config.paths.log_dir):
        target = _resolve_path(path, root)
        if target.suffix:
            target.parent.mkdir(parents=True, exist_ok=True)
        else:
            target.mkdir(parents=True, exist_ok=True)


def resolve_runtime_path(base_dir: Path, path: Path) -> Path:
    return _resolve_path(path, base_dir)


def get_required_secret(name: str) -> str:
    value = os.getenv(name)
    if not value:
        raise RuntimeError(f"Missing required environment variable: {name}")
    return value


def get_optional_secret(name: str) -> str | None:
    return os.getenv(name)


def redact_config(config: AppConfig) -> dict[str, Any]:
    return config.model_dump(mode="json")


def _resolve_path(path: Path, root: Path) -> Path:
    if path.is_absolute():
        return path
    return (root / path).resolve()


def _load_dotenv(start_dir: Path) -> None:
    dotenv_path = _find_dotenv(start_dir.resolve())
    if dotenv_path is None:
        return
    for raw_line in dotenv_path.read_text(encoding="utf-8").splitlines():
        line = raw_line.strip()
        if not line or line.startswith("#"):
            continue
        if line.startswith("export "):
            line = line[7:].strip()
        if "=" not in line:
            continue
        key, value = line.split("=", maxsplit=1)
        key = key.strip()
        value = value.strip()
        if not key or key in os.environ:
            continue
        os.environ[key] = _strip_env_quotes(value)


def _find_dotenv(start_dir: Path) -> Path | None:
    current = start_dir
    while True:
        candidate = current / ".env"
        if candidate.exists():
            return candidate
        if current.parent == current:
            return None
        current = current.parent


def _strip_env_quotes(value: str) -> str:
    if len(value) >= 2 and value[0] == value[-1] and value[0] in {'"', "'"}:
        return value[1:-1]
    return value
