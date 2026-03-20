# Sniper Bot

Local-first MVP crypto trading bot for Bybit Spot. The bot trades BTCUSDT on 1h candles with a deterministic trend-following strategy, stores state in SQLite, and uses Telegram plus the OpenAI Responses API for observability.

## Quick Start

1. Install Python 3.12 and `uv`.
2. Copy `config/example.yaml` to your own config file.
3. Copy `.env.example` to `.env` and fill in your secrets, or set them as OS environment variables:
   - `BYBIT_API_KEY`
   - `BYBIT_API_SECRET`
   - `OPENAI_API_KEY`
   - `TELEGRAM_BOT_TOKEN`
   - `TELEGRAM_CHAT_ID`
4. Sync dependencies with `uv sync --extra dev`.
5. Run `sniper-bot healthcheck --config config/example.yaml`.

The app automatically loads the nearest `.env` file it finds while walking up from your config file directory. Values already set in the real environment take priority over `.env`.

## CLI

- `sniper-bot backfill`
- `sniper-bot backtest`
- `sniper-bot run-paper`
- `sniper-bot run-demo`
- `sniper-bot run-live --confirm-live`
- `sniper-bot status`
- `sniper-bot healthcheck`
- `sniper-bot send-summary`
- `sniper-bot reset-drawdown`
