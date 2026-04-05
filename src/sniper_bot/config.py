from __future__ import annotations

import os
from pathlib import Path
from typing import Any

import yaml
from pydantic import BaseModel, Field, model_validator


def _load_dotenv(start_dir: Path) -> None:
    """Load .env file from start_dir or its parents into os.environ."""
    current = start_dir.resolve()
    for _ in range(5):
        env_path = current / ".env"
        if env_path.is_file():
            for line in env_path.read_text(encoding="utf-8").splitlines():
                line = line.strip()
                if not line or line.startswith("#") or "=" not in line:
                    continue
                key, _, value = line.partition("=")
                key = key.strip()
                value = value.strip().strip("\"'")
                if key and key not in os.environ:
                    os.environ[key] = value
            return
        parent = current.parent
        if parent == current:
            break
        current = parent


def get_required_secret(name: str) -> str:
    value = os.environ.get(name)
    if not value:
        raise RuntimeError(f"Required environment variable {name} is not set")
    return value


def get_optional_secret(name: str) -> str | None:
    return os.environ.get(name) or None


# ---------------------------------------------------------------------------
# Config models
# ---------------------------------------------------------------------------

class ExchangeConfig(BaseModel):
    environment: str = "demo"
    account_type: str = "UNIFIED"
    live_base_url: str = "https://api.bybit.com"
    demo_base_url: str = "https://api-demo.bybit.com"
    timeout_seconds: int = 20
    recv_window_ms: int = 5000
    user_agent: str = "sniper-bot/2.0.0"

    @property
    def base_url(self) -> str:
        return self.live_base_url if self.environment == "live" else self.demo_base_url


class ScannerConfig(BaseModel):
    quote_asset: str = "USDT"
    min_volume_24h_usd: float = 50_000
    min_turnover_24h_usd: float = 100_000
    max_price_change_24h_pct: float = 0.50
    excluded_pairs: list[str] = Field(default_factory=lambda: ["USDCUSDT", "DAIUSDT", "TUSDUSDT"])
    max_candidates_to_enrich: int = 10


class StrategyConfig(BaseModel):
    volume_spike_threshold: float = 3.0
    momentum_windows_minutes: list[int] = Field(default_factory=lambda: [5, 15, 60])
    volume_weight: float = 0.30
    momentum_weight: float = 0.25
    relative_strength_weight: float = 0.15
    ta_weight: float = 0.15          # RSI + MACD + Bollinger
    obi_weight: float = 0.10         # Order Book Imbalance
    funding_weight: float = 0.05     # Funding rate signal
    min_entry_score: float = 0.6
    max_entries_per_cycle: int = 1

    @model_validator(mode="after")
    def _weights_sum_to_one(self) -> "StrategyConfig":
        total = (
            self.volume_weight + self.momentum_weight + self.relative_strength_weight
            + self.ta_weight + self.obi_weight + self.funding_weight
        )
        if abs(total - 1.0) > 0.01:
            raise ValueError(f"Strategy weights must sum to 1.0, got {total}")
        return self


class PositionConfig(BaseModel):
    trailing_stop_pct: float = 0.15
    hard_stop_pct: float = 0.25
    take_profit_multiple: float = 2.0
    time_decay_hours: int = 8
    time_decay_min_gain_pct: float = 0.05
    max_hold_hours: int = 72
    trail_tighten_gain_pct: float = 0.10   # tighten trail once unrealized gain reaches this
    trail_tightened_stop_pct: float = 0.07  # tighter trail (from peak) after gain threshold
    # ATR-based dynamic stops
    use_atr_stops: bool = True
    atr_stop_multiplier: float = 2.5       # stop = entry - ATR * multiplier
    atr_trail_multiplier: float = 2.0      # trailing stop = peak - ATR * multiplier
    atr_min_stop_pct: float = 0.03         # floor: never tighter than 3%
    atr_max_stop_pct: float = 0.30         # ceiling: never wider than 30%


class RiskConfig(BaseModel):
    max_position_pct: float = 0.10
    max_concurrent_positions: int = 3
    max_portfolio_exposure_pct: float = 0.40
    max_daily_loss_pct: float = 0.05
    max_drawdown_pct: float = 0.15
    cooldown_losses: int = 3
    cooldown_hours: int = 12
    initial_paper_cash: float = 1000.0
    min_score_for_full_size: float = 0.80  # score at which full max_position_pct is used
    # Correlation exposure limits
    max_correlated_positions: int = 2   # max positions with correlation > threshold
    correlation_threshold: float = 0.75  # Pearson r above this = "correlated"
    correlation_lookback_bars: int = 24  # 1h bars to compute correlation
    # Kelly Criterion sizing
    use_kelly: bool = True
    kelly_fraction: float = 0.25     # quarter-Kelly (conservative)
    kelly_min_trades: int = 20       # need this many closed trades before Kelly kicks in
    # Market regime gate
    regime_gate_enabled: bool = True
    regime_bear_btc_change_pct: float = -0.03   # BTC 1h change below this → bear, block entries
    regime_bear_breadth_pct: float = 0.35       # fewer than this % pairs green → bear, block entries


class ExecutionConfig(BaseModel):
    slippage_bps: int = 10
    fee_rate: float = 0.001
    poll_interval_seconds: int = 30
    # TWAP: split large orders into chunks across cycles
    twap_enabled: bool = True
    twap_threshold_usdt: float = 50.0   # orders above this get split
    twap_chunks: int = 3                # number of chunks to split into
    twap_chunk_interval_cycles: int = 2 # cycles between chunks


class AlertsConfig(BaseModel):
    enabled: bool = True


class PathsConfig(BaseModel):
    paper_db: str = "data/paper.sqlite"
    demo_db: str = "data/demo.sqlite"
    live_db: str = "data/live.sqlite"
    log_dir: str = "logs"


class AutoTuneConfig(BaseModel):
    enabled: bool = False
    tune_every_n_cycles: int = 100     # ~50 min at 30s intervals (max fallback)
    openai_model: str = "gpt-4o"
    secondary_model: str | None = None  # for multi-model consensus
    max_change_pct: float = 0.30       # max relative change per param per tune
    require_min_cycles: int = 200      # don't tune until enough data
    require_min_trades: int = 3        # don't tune until enough closed trades
    # Adaptive triggers — tune early if any of these fire
    trigger_on_drawdown_pct: float = 0.05       # trigger if drawdown exceeds this
    trigger_on_consecutive_losses: int = 3      # trigger after N consecutive losses
    trigger_on_regime_change: bool = True        # trigger when market regime flips
    min_cycles_between_tunes: int = 30           # cooldown: never tune more often than this
    # Auto-rollback
    rollback_drop_pct: float = 0.05             # rollback if equity drops this much post-tune
    rollback_eval_cycles: int = 50              # evaluate rollback after this many cycles


# Hard bounds for every tunable parameter — AI cannot exceed these
TUNABLE_PARAM_BOUNDS: dict[str, dict[str, tuple[float, float]]] = {
    "strategy": {
        "min_entry_score": (0.05, 0.90),
        "volume_weight": (0.05, 0.60),
        "momentum_weight": (0.05, 0.60),
        "relative_strength_weight": (0.05, 0.40),
        "ta_weight": (0.0, 0.40),
        "obi_weight": (0.0, 0.30),
        "funding_weight": (0.0, 0.20),
    },
    "position": {
        "trailing_stop_pct": (0.03, 0.30),
        "hard_stop_pct": (0.05, 0.40),
        "take_profit_multiple": (1.3, 5.0),
        "time_decay_hours": (2, 24),
        "time_decay_min_gain_pct": (0.01, 0.15),
        "max_hold_hours": (12, 168),
    },
    "risk": {
        "max_position_pct": (0.03, 0.25),
        "max_concurrent_positions": (1, 5),
        "regime_bear_btc_change_pct": (-0.10, -0.005),
        "regime_bear_breadth_pct": (0.15, 0.55),
    },
    "scanner": {
        "min_volume_24h_usd": (10_000, 500_000),
        "min_turnover_24h_usd": (10_000, 1_000_000),
        "max_candidates_to_enrich": (3, 20),
    },
}


class AppConfig(BaseModel):
    mode: str = "paper"
    exchange: ExchangeConfig = Field(default_factory=ExchangeConfig)
    scanner: ScannerConfig = Field(default_factory=ScannerConfig)
    strategy: StrategyConfig = Field(default_factory=StrategyConfig)
    position: PositionConfig = Field(default_factory=PositionConfig)
    risk: RiskConfig = Field(default_factory=RiskConfig)
    execution: ExecutionConfig = Field(default_factory=ExecutionConfig)
    alerts: AlertsConfig = Field(default_factory=AlertsConfig)
    paths: PathsConfig = Field(default_factory=PathsConfig)
    auto_tune: AutoTuneConfig = Field(default_factory=AutoTuneConfig)

    @model_validator(mode="after")
    def _validate_mode(self) -> "AppConfig":
        if self.mode not in {"paper", "demo", "live"}:
            raise ValueError(f"mode must be paper, demo, or live — got {self.mode!r}")
        return self

    def database_path_for_mode(self, mode: str | None = None) -> str:
        m = mode or self.mode
        if m == "paper":
            return self.paths.paper_db
        if m == "demo":
            return self.paths.demo_db
        return self.paths.live_db


def resolve_path(base_dir: Path, relative: str) -> Path:
    p = Path(relative)
    if p.is_absolute():
        return p
    return (base_dir / p).resolve()


def load_config(config_path: Path) -> AppConfig:
    _load_dotenv(config_path.parent)
    raw = yaml.safe_load(config_path.read_text(encoding="utf-8")) or {}
    return AppConfig.model_validate(raw)
