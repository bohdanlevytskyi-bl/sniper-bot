# PRD: AI-Assisted Crypto Trading Bot

Status: Finalized MVP v1.1
Date: 2026-03-18
Owner: TBD

## 1. Product Summary

Build an AI-assisted crypto trading bot for a single operator who wants to automate a disciplined trading workflow. The MVP uses Bybit Spot, trades BTCUSDT on the 1h timeframe with a deterministic trend-following strategy, runs on the operator's local machine, and sends operational alerts through Telegram.

The first release is not a "set and forget" passive-income machine. It is a risk-managed automation tool for paper trading first, then Bybit demo trading, then small-capital live trading after validation. AI is limited to advisory outputs in the MVP: daily summaries and real-time market regime classification. The AI layer cannot block trades, adjust parameters, override entries or exits, or bypass the risk engine.

## 2. Problem Statement

Manual crypto trading is time-consuming, inconsistent, and vulnerable to emotional decisions. Many existing bots are either black boxes, too complex for a solo operator, or weak on risk controls and observability.

The product should solve the following problems:

- The operator cannot monitor markets 24/7.
- Manual execution is inconsistent and slow.
- Risk is often unclear until losses accumulate.
- Existing tools make it hard to explain why a trade happened.
- AI-based trading ideas are attractive, but unsafe without hard controls.

## 3. Goals

- Launch a working MVP for one operator, one exchange, and one initial strategy.
- Support paper trading before any exchange-backed trading.
- Support Bybit demo trading before any mainnet live trading.
- Enforce deterministic risk rules outside the AI layer.
- Provide transparent logs for signals, orders, fills, balances, and PnL.
- Support AI-generated daily summaries and real-time market regime tagging as advisory outputs only.
- Make it easy to pause, resume, or stop the bot at any time.
- Halt demo or live trading automatically at the configured drawdown threshold and require manual review before reset.

## 4. Non-Goals

- Guaranteed profit or passive income.
- High-frequency trading or low-latency arbitrage.
- Multi-user SaaS in MVP.
- DeFi yield farming, lending, or staking aggregation in MVP.
- Leveraged derivatives trading in MVP.
- Fully autonomous AI that can trade without hard safety rules.

## 5. Target User

Primary user:

- A solo operator trading only their own funds.
- Comfortable with basic crypto exchange accounts and API keys.
- Wants a controlled, inspectable system instead of a black-box bot.

## 6. Assumptions and Locked Defaults

- The bot will be built in Python.
- The MVP will support one centralized exchange first: Bybit Spot.
- The MVP will start with spot trading only.
- The initial market is BTCUSDT on the 1h timeframe.
- The initial strategy is deterministic trend following.
- The operator will begin in paper trading mode, then validate in Bybit demo mode, and only enable live mainnet mode after validation is complete and the operator explicitly enables it.
- Live trading, if enabled later, will start with small position sizes.
- The runtime target for the MVP is the operator's local machine, which is a first deployment mode rather than the long-term hosting strategy.
- Telegram is the first notification channel for alerts and daily summaries.
- The AI scope for the MVP is limited to daily summaries and real-time market regime classification.
- AI outputs are observability-only and cannot block trades, adjust strategy parameters, change exits, or override risk controls.
- The Bybit account type is Unified Trading Account (`UNIFIED`).
- Bybit demo trading is the first exchange-backed environment.
- The live-trading drawdown stop is 8% peak-to-trough account-equity drawdown from the active session high-water mark.
- Hitting the drawdown stop pauses trading and requires manual review and explicit reset by the operator.

## 7. Core User Stories

- As an operator, I want to connect one Bybit account securely so the bot can read market data and place orders.
- As an operator, I want to run the bot in paper mode so I can validate behavior without risking capital.
- As an operator, I want to run the bot in Bybit demo mode so I can validate real exchange execution without using real funds.
- As an operator, I want to configure a strategy and risk limits so the bot follows clear rules.
- As an operator, I want alerts when trades happen, when limits are hit, or when the bot enters a degraded state.
- As an operator, I want daily summaries so I can quickly understand performance and current risk.
- As an operator, I want an emergency stop so I can halt the bot immediately.

## 8. MVP Scope

In scope:

- One Bybit Spot exchange integration
- One deterministic trend-following strategy on BTCUSDT 1h
- Market data ingestion for BTCUSDT 1h strategy execution and analysis
- Paper trading mode
- Bybit demo trading mode
- Basic live mainnet mode behind explicit configuration
- Local machine runtime for a single operator
- Risk engine with hard limits and 8% drawdown stop protection
- Order execution and fill tracking
- Trade and event logging
- Telegram alerts for trade events, failures, kill-switch events, and daily summaries
- Daily PnL and risk summary
- AI-generated daily summaries and real-time market regime labels as advisory outputs

Out of scope for MVP:

- Multi-exchange routing
- Multi-user account management
- Portfolio optimization across many assets
- On-chain execution
- Mobile app
- Advanced visual dashboard
- Autonomous AI strategy generation without validation
- AI-driven trade blocking, parameter tuning, or execution control
- Perpetuals or leveraged spot margin trading

## 9. Functional Requirements

### 9.1 Exchange and Market Data

- The system must connect to the Bybit Spot API for the MVP.
- The system must support Bybit demo trading as the first exchange-backed validation environment.
- The system must ingest live market data required by the BTCUSDT 1h trend-following strategy.
- The system must store enough historical data to support local backtesting or signal generation.

### 9.2 Strategy Engine

- The system must implement a deterministic trend-following strategy for BTCUSDT on the 1h timeframe.
- The system must allow configurable parameters such as thresholds and position sizing rules without changing the core strategy definition.
- The system must produce explainable signal outputs.

### 9.3 AI Module

- The AI module must generate daily summaries and real-time market regime classifications.
- The AI module must remain advisory-only.
- The AI module must not block entries, adjust parameters, change exits, or override execution decisions.
- The AI module must not bypass hard risk rules.
- The system must log all AI-generated summaries and regime labels as decision-support outputs.

### 9.4 Risk Engine

- The system must enforce maximum position size.
- The system must enforce maximum daily loss.
- The system must enforce cooldown rules after configured loss conditions.
- The system must track account-equity drawdown from the current session high-water mark.
- The system must stop trading when drawdown reaches 8% and require manual review and explicit reset before trading can resume.
- The system must stop trading on critical API or connectivity errors.
- The system must provide a manual emergency stop.

### 9.5 Execution and Logging

- The system must place, track, and reconcile orders.
- The system must record fills, fees, balances, and realized PnL.
- The system must log every signal, order attempt, failure, and state change.
- The system must separate paper, demo, and live state into distinct local databases.

### 9.6 Reporting and Alerts

- The system must send Telegram notifications for trade open, trade close, order failure, drawdown-stop events, and kill-switch events.
- The system must generate a daily summary with PnL, exposure, drawdown, bot status, and AI regime commentary.

## 10. Non-Functional Requirements

- Reliability: The bot should fail safely and stop trading if core services become unhealthy.
- Security: API secrets must not be hard-coded and should be loaded securely from environment variables or secret storage.
- Observability: Logs must be structured enough to investigate failed trades and unexpected behavior.
- Maintainability: Components should be modular so strategy, exchange, and AI layers can evolve independently.
- Auditability: Trade decisions and risk actions must be explainable after the fact.
- Deployment: The MVP should run on a single local machine without requiring cloud-specific infrastructure.

## 11. Success Metrics

- The bot can run in paper mode for 30 consecutive days without a critical failure.
- The bot can run in Bybit demo mode for 30 consecutive days without a critical failure.
- The BTCUSDT 1h trend-following strategy can produce stable signals and complete end-to-end simulated trades.
- 100% of order attempts, fills, and state transitions are logged.
- Risk controls, including the 8% drawdown stop, trigger correctly in automated tests and simulated failure scenarios.
- Telegram alerts and daily summaries are delivered consistently.
- The AI module consistently generates daily summaries and regime labels without weakening execution safety.

## 12. Risks and Mitigations

- Overfitting: Mitigate with out-of-sample testing and paper/demo trading before live trading.
- API outages or exchange instability: Mitigate with retries, health checks, and fail-safe stop behavior.
- AI hallucinations or weak predictions: Mitigate by limiting AI to advisory functions at first.
- Security leakage of API keys: Mitigate with secure secret handling and least-privilege permissions.
- Poor live performance vs. backtests: Mitigate with realistic fee/slippage modeling and gradual rollout.
- Regulatory or tax complexity: Mitigate with detailed record-keeping and market/jurisdiction review before scale-up.

## 13. Future Questions

- When should the product move from local-machine deployment to a VPS or cloud runtime?
- When should additional markets or timeframes be added after BTCUSDT 1h is validated?
- Should future versions add Bybit perpetuals after spot behavior is validated?
- Should future versions allow AI to filter entries after a separate validation phase?

## 14. Proposed Delivery Phases

Phase 1:

- Build the Bybit Spot paper-trading foundation for BTCUSDT 1h trend following
- Establish Telegram alerting and core logging

Phase 2:

- Add Bybit demo-trading execution and the live-safe risk engine
- Implement the 8% drawdown stop and manual reset flow

Phase 3:

- Add AI-generated daily summaries and real-time regime classification as observability-only outputs
- Complete paper-trading and demo-trading validation

Phase 4:

- Approve limited live rollout on the local machine
- Start with small capital and require manual review after any drawdown stop event
