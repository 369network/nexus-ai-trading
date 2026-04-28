# NEXUS ALPHA — System Architecture

## Overview

NEXUS ALPHA is a multi-market algorithmic trading platform that combines rule-based strategies with a 7-agent LLM ensemble. It supports crypto (Binance), forex (OANDA), Indian equities (Zerodha Kite), and US stocks (Alpaca) from a single orchestrator.

---

## High-Level Architecture

```
┌──────────────────────────────────────────────────────────────────────┐
│                        NEXUS ALPHA PLATFORM                          │
│                                                                      │
│  ┌─────────────────────────────────────────────────────────────────┐ │
│  │                    NexusAlpha (src/main.py)                     │ │
│  │  Central orchestrator: lifecycle, health checks, coordination   │ │
│  └──────────────────────────┬──────────────────────────────────────┘ │
│                             │                                        │
│  ┌──────────┬───────────────┼───────────────┬───────────────────┐   │
│  │          │               │               │                   │   │
│  ▼          ▼               ▼               ▼                   ▼   │
│ ┌──────┐ ┌──────┐ ┌──────────────┐ ┌─────────────┐ ┌────────────┐  │
│ │Market│ │Market│ │ LLM Ensemble │ │Risk Manager │ │ Execution  │  │
│ │Orch. │ │Orch. │ │  (7 Agents)  │ │ (5 Layers)  │ │  Engine    │  │
│ │Crypto│ │Forex │ │              │ │             │ │Paper/Live  │  │
│ └──┬───┘ └──┬───┘ └──────────────┘ └─────────────┘ └────────────┘  │
│    │        │                                                        │
│  ┌─┴────────┴──────────────────────────────────────────────────┐    │
│  │              Data Layer                                      │    │
│  │  WebSocket (live) + REST (historical) + Supabase (storage)  │    │
│  └─────────────────────────────────────────────────────────────┘    │
└──────────────────────────────────────────────────────────────────────┘
```

---

## Component Breakdown

### 1. NexusAlpha — Main Orchestrator (`src/main.py`)

Responsible for:
- Executing the 14-step startup sequence
- Spawning supervised background tasks per market
- Running the health check loop (every 60s)
- Handling SIGTERM/SIGINT for graceful shutdown
- Auto-restarting crashed components (max 3 restarts in 5 minutes)

**Key classes:** `NexusAlpha`, `RestartRecord`, `ComponentHealth`

---

### 2. MarketOrchestrator (`src/data/market_orchestrator.py`)

One instance per enabled market (crypto, forex, indian_stocks, us_stocks).

Responsibilities:
- Maintains WebSocket connections for all symbols
- Maintains candle cache: last 500 candles per (symbol, timeframe)
- Normalises raw exchange data via `normalizer.py`
- Checks market hours before triggering signal generation
- Calls `_on_candle_close()` callback in `NexusAlpha`

**Candle flow:**
```
Exchange WebSocket
    → raw message
    → normalizer (timestamp UTC, OHLCV float)
    → candle_queue (asyncio.Queue)
    → _process_candle()
    → cache update + DB upsert
    → [if closed] _on_candle_close() callback
```

---

### 3. Signal Generation Pipeline

Triggered on every candle close:

```
New Candle Close
    ↓
IndicatorEngine.compute()        ← RSI, MACD, ATR, BB, EMA, VWAP, etc.
    ↓
MarketRegimeDetector.update()    ← TRENDING/RANGING/VOLATILE
    ↓
Strategy.evaluate() × N          ← First LONG/SHORT wins
    ↓                              (TrendMomentum, MeanReversionBB, BreakoutVolume, …)
AgentCoordinator.debate()        ← 7 LLM agents vote
    ↓
SignalFusionEngine.fuse()        ← Weighted score aggregation + multi-TF check
    ↓
EdgeFilter.evaluate()            ← EV ≥ threshold? kelly > min_edge?
    ↓
RiskManager.evaluate()           ← 5 layers: position, daily, correlation, drawdown, tail
    ↓
ExecutionEngine.execute()        ← Paper or live order
    ↓
DB store + Telegram alert
```

---

### 4. LLM Ensemble (`src/llm/ensemble.py`)

Manages a pool of LLM models with fallback hierarchy:

| Priority | Model           | Use Case                    |
|----------|-----------------|-----------------------------|
| 1        | GPT-4o          | Primary analysis             |
| 2        | Claude 3 Sonnet | Secondary / high-cost guard |
| 3        | Gemini Pro      | Fallback                    |
| 4        | Local (Ollama)  | Cost guard emergency         |

Features:
- Daily cost tracking (`llm_cost_guard_daily_usd` config)
- Per-agent model assignment
- Deterministic test mode (`mock_llm` fixture)

---

### 5. Agent System (`src/agents/`)

7 agents, each with a unique analytical bias:

| Agent             | Bias               | Primary Indicators           |
|-------------------|--------------------|------------------------------|
| TrendFollower     | Pro-trend          | EMA, MACD, ADX               |
| MeanReversion     | Counter-trend      | RSI, Bollinger Bands, Z-score|
| BreakoutHunter    | Momentum           | Volume, ATR, Range            |
| RiskSentinel      | Conservative       | VaR, correlation, drawdown    |
| MacroAnalyst      | Fundamental bias   | News, rates, macro indicators |
| PatternRecognizer | Chart patterns     | Price action, candlesticks    |
| VolumeProfiler    | Volume analysis    | VWAP, OBV, POC, VAH/VAL      |

Each agent outputs: `vote (LONG/SHORT/NEUTRAL)`, `confidence (0–1)`, `reasoning`.

---

### 6. Risk Manager (`src/risk/manager.py`)

5 independent risk layers evaluated in sequence:

| Layer | Name              | Condition                                        | Action         |
|-------|-------------------|--------------------------------------------------|----------------|
| 1     | Position Sizing   | Single position > 10% equity (crypto)            | REJECT         |
| 2     | Correlation       | Portfolio correlation > 0.8 with open positions  | REJECT         |
| 3     | Daily Loss        | Daily loss ≥ 3% equity                           | HALT 24h       |
| 4     | Drawdown          | Drawdown ≥ 15%                                   | PAUSE trading  |
| 5     | Tail Risk         | Drawdown ≥ 25%                                   | FULL STOP      |

6 circuit breakers:
1. Flash crash (>5% drop in 1 candle)
2. Liquidity (spread > 3x normal)
3. Volume anomaly (>10x average)
4. API error rate (>5 errors/min)
5. P&L spike (>2% loss in 5 min)
6. Correlation cascade (3+ positions correlated)

---

### 7. Execution Engine (`src/execution/`)

Two modes:

**Paper (`src/execution/paper.py`):**
- Simulates fills with configurable slippage and fees
- Tracks virtual portfolio in Supabase
- Logs all would-be trades

**Live (`src/execution/live.py`):**
- Routes to correct exchange adapter
- Implements smart order routing (MARKET vs LIMIT)
- Tracks partial fills
- Handles exchange-specific order formats

---

### 8. Learning System

#### Dream Mode (`src/learning/dream_mode.py`)
- Runs nightly (configurable via cron or `run_dream_mode.py`)
- Grid searches parameter space for each strategy
- Runs fast backtests on 90 days of data
- Saves proposals to `strategy_params` table
- Auto-applies if `dream_mode_auto_evolve=true` AND improvement >5%

#### Memory Updater (`src/learning/memory.py`)
- Updates `memory_entries` table with market patterns
- Promotes/demotes entries between tiers (hot/warm/cold/archive)
- Uses cosine similarity search on `pattern_vectors` table

---

## Data Flow Diagram

```
                    LIVE DATA FLOWS
─────────────────────────────────────────────────────────

Binance WS ──→ MarketOrchestrator(crypto) ──→ candle_queue
OANDA Stream ─→ MarketOrchestrator(forex) ──→ candle_queue
Kite Stream ──→ MarketOrchestrator(india) ──→ candle_queue

                    SIGNAL FLOW
─────────────────────────────────────────────────────────

candle_queue
    → _process_candle() [cache + DB upsert]
    → _on_candle_close() [if market open]
        → IndicatorEngine
        → RegimeDetector
        → Strategy.evaluate()     ← if no signal: STOP here
        → AgentCoordinator.debate()
        → SignalFusionEngine.fuse()
        → EdgeFilter.evaluate()   ← if no edge: store + STOP
        → RiskManager.evaluate()  ← if rejected: store + STOP
        → ExecutionEngine.execute()
        → DB.store_signal() + DB.store_trade()
        → AlertManager.notify_trade()

                    STORAGE FLOWS
─────────────────────────────────────────────────────────

All candles ──────────→ market_data (partitioned table)
All signals ──────────→ signals table
Executed trades ──────→ trades table
Agent votes ──────────→ agent_decisions table
Risk events ──────────→ risk_events table
Portfolio (hourly) ───→ portfolio_snapshots (trigger)
LLM perf metrics ─────→ model_performance table
Market patterns ──────→ pattern_vectors (pgvector)
Strategy proposals ───→ strategy_params table
```

---

## Startup Sequence (14 Steps)

```
1.  load_settings()          ← settings.py + yaml + feature flags
2.  SupabaseClient.connect() ← validate DB connection
3.  init_providers()         ← REST provider per market
4.  connect_websockets()     ← live streams per market
5.  LLMEnsemble.init()       ← validate API keys, model list
6.  AgentCoordinator.init()  ← 7 agents registered
7.  RiskManager.init()       ← load circuit breaker state from DB
8.  ExecutionEngine.init()   ← paper or live executor
9.  Signal components        ← IndicatorEngine + RegimeDetector per symbol
10. DreamScheduler.init()    ← schedule overnight optimisation
11. AlertManager.init()      ← validate Telegram bot token
12. OS signal handlers       ← SIGTERM / SIGINT
13. Health registry          ← initial health snapshot
14. Background tasks         ← asyncio.gather() all loops
```

---

## Technology Stack

| Layer          | Technology                              |
|----------------|-----------------------------------------|
| Language       | Python 3.12                             |
| Async runtime  | asyncio                                 |
| DB             | Supabase (PostgreSQL 15 + pgvector)     |
| Realtime       | Supabase Realtime (WebSocket pub/sub)   |
| Cache          | asyncio.Queue + deque (in-process)      |
| LLM            | OpenAI / Anthropic / Google via ensemble|
| Technical indicators | TA-Lib (C library via Python wrapper)|
| Exchange APIs  | python-binance, oandapyV20, kiteconnect, alpaca-py |
| Dashboard      | Next.js 14 + Supabase JS client (Vercel)|
| Deployment     | Ubuntu 22.04 + systemd (Hostinger VPS)  |
| Monitoring     | Custom health_check.py + Telegram alerts|
| Vector search  | pgvector (cosine similarity on pattern_vectors)|
