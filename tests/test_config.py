from __future__ import annotations

from pathlib import Path

from sniper_bot.config import get_optional_secret, load_config


def test_load_config_reads_dotenv_from_parent_directory(tmp_path: Path, monkeypatch: object) -> None:
    monkeypatch.delenv("BYBIT_API_KEY", raising=False)
    repo_dir = tmp_path / "repo"
    config_dir = repo_dir / "config"
    config_dir.mkdir(parents=True)
    (repo_dir / ".env").write_text("BYBIT_API_KEY=from_dotenv\n", encoding="utf-8")
    (config_dir / "live.yaml").write_text(
        "mode: paper\npair: BTCUSDT\ntimeframe_minutes: 60\n",
        encoding="utf-8",
    )

    load_config(config_dir / "live.yaml")

    assert get_optional_secret("BYBIT_API_KEY") == "from_dotenv"


def test_load_config_does_not_override_existing_environment(tmp_path: Path, monkeypatch: object) -> None:
    monkeypatch.setenv("BYBIT_API_KEY", "from_env")
    repo_dir = tmp_path / "repo"
    config_dir = repo_dir / "config"
    config_dir.mkdir(parents=True)
    (repo_dir / ".env").write_text("BYBIT_API_KEY=from_dotenv\n", encoding="utf-8")
    (config_dir / "live.yaml").write_text(
        "mode: paper\npair: BTCUSDT\ntimeframe_minutes: 60\n",
        encoding="utf-8",
    )

    load_config(config_dir / "live.yaml")

    assert get_optional_secret("BYBIT_API_KEY") == "from_env"
