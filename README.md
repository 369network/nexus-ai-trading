# NEXUS ALPHA — Multi-Market Algorithmic Trading Bot

Production-grade algorithmic trading platform with a 7-agent LLM ensemble, multi-market support, and enterprise risk management.

---

## Architecture Overview

```
┌─────────────────────────────────────────────────────────────┐
│                     NEXUS ALPHA                             │
│                                                             │
│  ┌──────────────┐   ┌──────────────┐   ┌───────────────┐  │
│  │Market Orch.  │   │ LLM Ensemble │   │  Risk Manager │  │
│  │  (per market)│   │  (7 Agents)  │   │  (5 Layers)   │  │
│  └──────┬───────┘   └──────┬───────┘   └───────┬───────┘  │
│         │                  │                   │           │
│  ┌──────▼───────────────────▼───────────────────▼───────┐  │
│  │              Signal Generation Pipeline               │  │
│  │  Indicators → Regime → Strategy → Debate → Fuse      │  │
│  │  → Edge Filter → Risk → Execute → Store → Alert      │  │
│  └───────────────────────────────────────────────────────┘  │
│                            │                                │
│  ┌─────────────────────────▼──────────────────────────┐    │
│  │              Supabase (PostgreSQL + pgvector)       │    │
│  │  market_data │ signals │ trades │ memory │ patterns │    │
│  └────────────────────────────────────────────────────┘    │
└─────────────────────────────────────────────────────────────┘

Supported Markets:
  Crypto      → Binance (BTC, ETH, BNB, SOL, XRP)
  Forex       → OANDA (EUR/USD, GBP/USD, USD/JPY)
  India       → Zerodha Kite (RELIANCE, TCS, INFY, HDFC)
  US Stocks   → Alpaca / yfinance (AAPL, MSFT, NVDA, TSLA)
```

---

## Prerequisites

You will need accounts with:

| Service       | Purpose                    | Required  |
|---------------|----------------------------|-----------|
| Supabase      | Database + realtime        | Required  |
| Binance       | Crypto data + trading      | If using crypto |
| OANDA         | Forex data + trading       | If using forex |
| Zerodha Kite  | Indian stock data + trading| If using India |
| Alpaca        | US stock data + trading    | If using US stocks |
| OpenAI        | GPT-4o for agent ensemble  | Required (at least 1 LLM) |
| Anthropic     | Claude fallback LLM        | Optional  |
| Telegram      | Alerts and notifications   | Recommended |

---

## Quick Start (5 Steps)

### Step 1: Clone and install

```bash
git clone https://github.com/yourorg/nexus-alpha.git
cd nexus-alpha
pip install poetry
poetry install
```

### Step 2: Configure environment

```bash
cp .env.example .env
nano .env
```

Minimum required `.env`:
```bash
# Supabase
SUPABASE_URL=https://your-project.supabase.co
SUPABASE_SERVICE_KEY=eyJhbGc...

# LLM (at least one)
OPENAI_API_KEY=sk-...

# Paper mode (MUST be true for first run)
PAPER_MODE=true
```

### Step 3: Set up database

```bash
# Run against your Supabase project (get URL from Project Settings > Database)
psql "postgresql://postgres:[password]@[host]:5432/postgres" \
    -f scripts/setup_supabase.sql
```

Or use the Supabase SQL Editor: paste the contents of `scripts/setup_supabase.sql`.

### Step 4: Seed historical data

```bash
# Download 90 days of historical data (crypto by default)
poetry run python scripts/seed_historical.py --market crypto

# Seed all enabled markets
poetry run python scripts/seed_historical.py
```

### Step 5: Start paper trading

```bash
poetry run python scripts/run_paper.py
```

---

## Configuration Guide

### Market Configuration (`config/markets.yaml`)

```yaml
crypto:
  enabled: true
  symbols: ["BTCUSDT", "ETHUSDT", "BNBUSDT"]
  timeframes: ["1h", "4h"]
  market_class: crypto

forex:
  enabled: false
  symbols: ["EUR_USD", "GBP_USD"]
  timeframes: ["15m", "1h"]
  market_class: forex
```

### Strategy Configuration (`config/strategies.yaml`)

```yaml
TrendMomentum:
  enabled: true
  markets: ["crypto", "us_stocks"]
  ema_fast: 8
  ema_slow: 21
  rsi_period: 14
  rsi_threshold: 55
  atr_stop_multiplier: 1.5

MeanReversionBB:
  enabled: true
  markets: ["crypto", "forex"]
  bb_period: 20
  bb_std: 2.0
  rsi_oversold: 30
  rsi_overbought: 70
```

### Risk Configuration (`config/risk.yaml`)

```yaml
max_position_size_pct: 10.0    # Per position max (% equity)
daily_loss_limit_pct: 3.0      # Daily loss halt threshold
drawdown_pause_pct: 15.0       # Pause trading at this drawdown
drawdown_stop_pct: 25.0        # Emergency stop at this drawdown
max_open_positions: 5
risk_pct_per_trade: 1.0        # Risk 1% per trade
```

---

## Paper Trading Setup

```bash
# Start with full pre-flight checklist
python scripts/run_paper.py

# Start with debug logging
python scripts/run_paper.py --log-level DEBUG

# Check health
python scripts/health_check.py

# Run backtest on historical data
python scripts/run_backtest.py --strategy TrendMomentum --market crypto --symbol BTCUSDT --days 90
```

---

## Live Trading Setup

**Warning:** Live trading involves real financial risk. Ensure you have thoroughly backtested all strategies in paper mode before proceeding.

```bash
# 1. Set PAPER_MODE=false in .env
# 2. Configure live exchange credentials (not testnet)
# 3. Run with confirmation prompt

python scripts/run_live.py
# You must type "CONFIRM LIVE TRADING" to proceed
```

Live trading checklist:
- [ ] PAPER_MODE=false in .env
- [ ] At least 30 days of paper trading with positive results
- [ ] Live API keys configured (not testnet/practice)
- [ ] Risk limits set conservatively
- [ ] Telegram alerts working
- [ ] VPS deployed with systemd service

---

## Dashboard Deployment (Vercel)

```bash
cd dashboard
npm install

# Set environment variables in Vercel dashboard:
# NEXT_PUBLIC_SUPABASE_URL=...
# NEXT_PUBLIC_SUPABASE_ANON_KEY=...

npx vercel --prod
```

Or use the Vercel CLI:
```bash
vercel deploy --prod
```

The dashboard uses Supabase Realtime for live updates of signals, trades, and portfolio.

---

## Monitoring Setup

### Systemd (VPS)

```bash
# Run VPS setup script
bash deploy/hostinger/setup.sh

# Start services
systemctl start nexus-data.service
systemctl start nexus-bot.service
systemctl enable nexus-data.service nexus-bot.service

# Check status
systemctl status nexus-bot.service
journalctl -u nexus-bot -f
```

### Health Check Cron

```bash
# Add to crontab (every 5 minutes)
*/5 * * * * /usr/local/bin/nexus-monitor.sh >> /var/log/nexus/monitor.log 2>&1
```

### Dream Mode (Overnight Optimisation)

```bash
# Run manually
python scripts/run_dream_mode.py

# Schedule nightly at 02:00 UTC
0 2 * * * /home/nexus/nexus-alpha/.venv/bin/python /home/nexus/nexus-alpha/scripts/run_dream_mode.py >> /var/log/nexus/dream.log 2>&1
```

---

## Running Tests

```bash
# Unit tests (no external dependencies)
pytest tests/unit/ -v

# With coverage
pytest tests/unit/ --cov=src --cov-report=html

# Integration tests (requires API credentials)
pytest tests/integration/ -v -m integration

# Full test suite
pytest tests/ -v --timeout=120
```

---

## Troubleshooting

See [docs/troubleshooting.md](docs/troubleshooting.md) for:
- Kite TOTP auth failures
- TA-Lib installation on Windows/Mac/Linux
- Supabase RLS and connection issues
- High LLM costs and cost guards
- Circuit breaker false positives
- Bot not generating signals

---

## Documentation

| Document                  | Description                               |
|---------------------------|-------------------------------------------|
| [docs/architecture.md](docs/architecture.md) | System design, data flows, startup sequence |
| [docs/agents.md](docs/agents.md)           | 7-agent profiles, debate process, Brier scores |
| [docs/risk.md](docs/risk.md)               | Risk layers, circuit breakers, thresholds |
| [docs/strategies.md](docs/strategies.md)   | Strategy entry/exit conditions, backtests |
| [docs/api.md](docs/api.md)                 | Supabase tables, realtime, function signatures |
| [docs/troubleshooting.md](docs/troubleshooting.md) | Common issues and solutions |

---

## Project Structure

```
nexus-alpha/
├── src/
│   ├── main.py                  # Main orchestrator (NexusAlpha class)
│   ├── config/                  # Settings, YAML configs, feature flags
│   ├── data/
│   │   ├── market_orchestrator.py  # Per-market candle management
│   │   ├── normalizer.py           # Exchange-specific normalisation
│   │   ├── providers.py            # REST data providers
│   │   └── websockets.py           # WebSocket connectors
│   ├── indicators/              # Technical indicator computations
│   ├── strategies/              # Signal generation, regime detection, fusion
│   ├── agents/                  # 7-agent LLM coordinator
│   ├── llm/                     # LLM ensemble (OpenAI, Anthropic, Google)
│   ├── risk/                    # Risk manager, position sizer, circuit breakers
│   ├── execution/               # Paper and live execution engines
│   ├── alerts/                  # Telegram alert manager
│   ├── learning/                # Dream Mode, memory updater
│   └── db/                      # Supabase client
├── scripts/
│   ├── setup_supabase.sql       # Complete database schema
│   ├── seed_historical.py       # Historical data downloader
│   ├── run_paper.py             # Paper trading launcher
│   ├── run_live.py              # Live trading launcher (confirmation required)
│   ├── run_backtest.py          # Backtesting engine
│   ├── run_dream_mode.py        # Parameter optimisation
│   └── health_check.py          # Health monitoring (exit 0/1/2)
├── deploy/
│   ├── hostinger/
│   │   ├── setup.sh             # VPS setup script (Ubuntu 22.04)
│   │   ├── nexus-bot.service    # Systemd service (6GB RAM, 300% CPU)
│   │   ├── nexus-data.service   # Separate data ingestion service
│   │   └── monitoring.sh        # Cron health monitor with auto-restart
│   └── vercel/
│       └── vercel.json          # Next.js dashboard deployment config
├── docs/
│   ├── architecture.md          # System design
│   ├── agents.md                # Agent profiles
│   ├── risk.md                  # Risk framework
│   ├── strategies.md            # Strategy documentation
│   ├── api.md                   # Internal API reference
│   └── troubleshooting.md       # Common issues
├── tests/
│   ├── conftest.py              # Shared fixtures
│   ├── unit/
│   │   ├── test_indicators.py   # RSI, MACD, ATR, BB, volume tests
│   │   ├── test_position_sizer.py # Kelly, ATR sizing, drawdown scaling
│   │   ├── test_risk_layers.py  # 5 risk layers + 6 circuit breakers
│   │   ├── test_signal_fusion.py # Vote aggregation, edge detection, multi-TF
│   │   └── test_normalizer.py   # Timestamps, gap fill, outlier detection
│   └── integration/
│       ├── test_binance_ws.py   # Binance WebSocket (public endpoint)
│       └── test_oanda_stream.py # OANDA practice streaming
└── README.md
```

---

## License

Proprietary. All rights reserved.

---

## Disclaimer

This software is for educational and research purposes. Trading financial instruments involves significant risk of loss. Past performance does not guarantee future results. Always use paper trading mode first and never trade with money you cannot afford to lose.
