# NEXUS ALPHA — Internal API Documentation

## Supabase Tables

### market_data (partitioned)

**Partitions:** `market_data_crypto`, `market_data_forex`, `market_data_indian_stocks`, `market_data_us_stocks`

| Column        | Type             | Description                        |
|---------------|------------------|------------------------------------|
| id            | BIGSERIAL        | Primary key                        |
| market        | market_class     | Enum: crypto/forex/indian_stocks/us_stocks |
| symbol        | VARCHAR(32)      | e.g. "BTCUSDT", "EUR_USD"          |
| timeframe     | timeframe_enum   | e.g. "1h", "4h", "1d"             |
| timestamp     | TIMESTAMPTZ      | Candle open time (UTC)             |
| open/high/low/close | NUMERIC(20,8) | OHLC prices                   |
| volume        | NUMERIC(28,8)    | Base asset volume                  |
| is_closed     | BOOLEAN          | True if candle is complete         |

**Upsert key:** `(market, symbol, timeframe, timestamp)`

**Query example:**
```python
result = sb.table("market_data") \
    .select("*") \
    .eq("market", "crypto") \
    .eq("symbol", "BTCUSDT") \
    .eq("timeframe", "1h") \
    .order("timestamp", desc=True) \
    .limit(500) \
    .execute()
```

---

### signals

| Column              | Type             | Description                          |
|---------------------|------------------|--------------------------------------|
| id                  | UUID             | Primary key                          |
| created_at          | TIMESTAMPTZ      | Signal creation time                 |
| market/symbol/timeframe | enums       | Market context                       |
| strategy_name       | VARCHAR(64)      | Which strategy generated it          |
| direction           | signal_direction | LONG/SHORT/NEUTRAL                   |
| confidence          | NUMERIC(5,4)     | 0.0–1.0                              |
| expected_value      | NUMERIC(10,6)    | Estimated EV                         |
| edge_detected       | BOOLEAN          | Did edge filter approve?             |
| entry_price         | NUMERIC(20,8)    | Price at signal time                 |
| stop_loss           | NUMERIC(20,8)    | Stop loss level                      |
| take_profit_1/2/3   | NUMERIC(20,8)    | Three-tier TP levels                 |
| atr_at_signal       | NUMERIC(20,8)    | ATR when signal generated            |
| regime              | regime_type      | Market regime at signal time         |
| multi_tf_confirmed  | BOOLEAN          | Higher TF alignment?                 |
| agent_scores        | JSONB            | Per-agent vote scores                |
| fusion_score        | NUMERIC(5,4)     | Weighted aggregate score             |
| risk_approved       | BOOLEAN          | Did risk manager approve?            |
| risk_rejection_reason | VARCHAR(256)   | Why rejected (if applicable)         |
| position_size_units | NUMERIC(20,8)   | Approved position quantity           |
| position_size_usd   | NUMERIC(20,4)   | Position value in USD                |
| execution_mode      | execution_mode   | paper/live                           |

---

### trades

| Column              | Type             | Description                          |
|---------------------|------------------|--------------------------------------|
| id                  | UUID             | Primary key                          |
| signal_id           | UUID             | FK → signals.id                      |
| market/symbol       | varchar          | Market context                       |
| exchange_order_id   | VARCHAR(128)     | Exchange-assigned order ID           |
| direction           | signal_direction | LONG/SHORT                           |
| status              | trade_status     | PENDING/OPEN/.../CLOSED              |
| entry_price/time    | numeric/ts       | Fill details                         |
| exit_price/time     | numeric/ts       | Exit details (null if still open)    |
| exit_reason         | VARCHAR(128)     | "take_profit_1", "stop_loss", etc.   |
| stop_loss/take_profit_1/2/3 | numeric | Risk levels                          |
| realized_pnl        | NUMERIC(20,8)    | Realised P&L in base currency        |
| net_pnl_usd         | NUMERIC(20,4)    | Net P&L after fees                   |
| fees_paid           | NUMERIC(20,8)    | Total fees                           |
| mae/mfe             | NUMERIC(20,8)    | Max adverse/favorable excursion      |
| duration_seconds    | INTEGER          | Trade duration                       |

---

### agent_decisions

| Column        | Type           | Description                    |
|---------------|----------------|--------------------------------|
| id            | UUID           | Primary key                    |
| signal_id     | UUID           | FK → signals.id                |
| agent         | agent_name     | One of 7 agents                |
| vote          | signal_direction | LONG/SHORT/NEUTRAL            |
| confidence    | NUMERIC(5,4)   | 0.0–1.0                        |
| reasoning     | TEXT           | LLM-generated explanation      |
| key_factors   | JSONB          | Key indicator values           |
| model_used    | VARCHAR(64)    | Which LLM was used             |
| tokens_used   | INTEGER        | LLM token count                |
| latency_ms    | INTEGER        | Response latency               |

---

### portfolio_snapshots

Hourly snapshots triggered by trade inserts/updates.

| Column              | Type           | Description               |
|---------------------|----------------|---------------------------|
| snapshot_at         | TIMESTAMPTZ    | Hour boundary timestamp   |
| execution_mode      | execution_mode | paper/live                |
| total_equity_usd    | NUMERIC(20,4)  | Total portfolio value      |
| daily_pnl_usd/pct   | numeric        | Day's P&L                 |
| current_drawdown_pct| NUMERIC(8,6)   | Current drawdown           |
| win_rate            | NUMERIC(5,4)   | Cumulative win rate        |
| positions_json      | JSONB          | Open position details      |

---

### pattern_vectors

Used for cosine similarity search on historical patterns.

| Column          | Type          | Description                    |
|-----------------|---------------|--------------------------------|
| embedding       | vector(25)    | 25-dim feature vector          |
| pattern_name    | VARCHAR(128)  | Pattern identifier             |
| signal_direction| enum          | LONG/SHORT                     |
| avg_pnl_pct     | NUMERIC(10,6) | Average outcome                |
| win_rate        | NUMERIC(5,4)  | Win rate for this pattern      |

**Similarity search:**
```sql
SELECT pattern_name, signal_direction, win_rate,
       1 - (embedding <=> '[0.1, 0.2, ...]'::vector) AS similarity
FROM pattern_vectors
WHERE market = 'crypto'
ORDER BY embedding <=> '[0.1, 0.2, ...]'::vector
LIMIT 5;
```

---

## Realtime Subscriptions

### Publication: `nexus_realtime`

Tables included: `signals`, `trades`, `risk_events`, `portfolio_snapshots`, `system_config`

### JavaScript (Next.js dashboard)

```javascript
import { createClient } from '@supabase/supabase-js'

const supabase = createClient(
  process.env.NEXT_PUBLIC_SUPABASE_URL,
  process.env.NEXT_PUBLIC_SUPABASE_ANON_KEY
)

// Subscribe to new signals
const signalChannel = supabase
  .channel('nexus-signals')
  .on('postgres_changes', {
    event: 'INSERT',
    schema: 'public',
    table: 'signals',
    filter: 'edge_detected=eq.true'
  }, (payload) => {
    console.log('New edge signal:', payload.new)
    updateSignalList(payload.new)
  })
  .subscribe()

// Subscribe to trade updates
const tradeChannel = supabase
  .channel('nexus-trades')
  .on('postgres_changes', {
    event: '*',   // INSERT + UPDATE
    schema: 'public',
    table: 'trades'
  }, (payload) => {
    updateTradeCard(payload.new)
  })
  .subscribe()

// Subscribe to risk events
const riskChannel = supabase
  .channel('nexus-risk')
  .on('postgres_changes', {
    event: 'INSERT',
    schema: 'public',
    table: 'risk_events',
    filter: 'severity=gte.4'
  }, (payload) => {
    showRiskAlert(payload.new)
  })
  .subscribe()

// Cleanup
function cleanup() {
  supabase.removeChannel(signalChannel)
  supabase.removeChannel(tradeChannel)
  supabase.removeChannel(riskChannel)
}
```

### Python (pg_notify)

```python
import asyncpg

async def listen_for_signals(db_url: str):
    conn = await asyncpg.connect(db_url)
    
    async def on_new_signal(conn, pid, channel, payload):
        import json
        signal = json.loads(payload)
        print(f"New signal: {signal['symbol']} {signal['direction']}")
    
    await conn.add_listener('nexus_new_signal', on_new_signal)
    
    try:
        await asyncio.Future()  # Block forever
    finally:
        await conn.close()
```

---

## Function Signatures (Key Interfaces)

### SupabaseClient (`src/db/supabase_client.py`)

```python
class SupabaseClient:
    async def connect(self) -> None
    async def close(self) -> None
    async def health_check(self) -> bool

    async def upsert_candle(
        self, market: str, symbol: str, timeframe: str, candle: Dict
    ) -> None

    async def fetch_candles(
        self, market: str, symbol: str, timeframe: str,
        limit: int = 500, since: datetime = None
    ) -> List[Dict]

    async def store_signal(
        self, signal: FusedSignal, edge: EdgeResult, risk: RiskResult
    ) -> str  # Returns signal_id (UUID)

    async def store_trade(
        self, signal_id: str, trade: TradeResult
    ) -> str  # Returns trade_id (UUID)

    async def store_agent_decisions(
        self, signal_id: str, decisions: List[AgentDecision]
    ) -> None

    async def get_open_positions(
        self, execution_mode: str = "paper"
    ) -> List[Dict]

    async def get_portfolio_state(
        self, execution_mode: str = "paper"
    ) -> Dict

    async def get_config(self, key: str) -> Optional[str]
    async def set_config(self, key: str, value: str) -> None
```

### RiskManager (`src/risk/manager.py`)

```python
class RiskManager:
    async def init(self) -> None
    
    async def evaluate(
        self, signal: FusedSignal, edge: EdgeResult
    ) -> RiskResult
    # Runs all 5 layers; returns first rejection or full approval
    
    async def get_circuit_breaker_status(
        self
    ) -> Dict[str, CircuitBreakerState]
    
    async def reset_circuit_breaker(self, name: str) -> None
    async def get_portfolio_state(self) -> PortfolioState
```

### ExecutionEngine (`src/execution/engine.py`)

```python
class ExecutionEngine:
    async def execute(
        self, signal: FusedSignal, risk_result: RiskResult
    ) -> TradeResult

    async def close_position(
        self, trade_id: str, reason: str = "manual"
    ) -> TradeResult

    async def health_check(self) -> HealthResult
    async def shutdown(self) -> None
```

### AgentCoordinator (`src/agents/coordinator.py`)

```python
class AgentCoordinator:
    async def init(self) -> None

    async def debate(
        self,
        candidate: CandidateSignal,
        indicators: Dict[str, Any],
        regime: RegimeResult,
        candle: Dict[str, Any],
    ) -> DebateResult
    # Returns all 7 agent votes
```

### AlertManager (`src/alerts/manager.py`)

```python
class AlertManager:
    async def init(self) -> None
    async def run(self) -> None  # Background message queue processor

    async def notify_trade(
        self, trade: TradeResult, signal: FusedSignal
    ) -> None

    async def notify_warning(self, message: str) -> None
    async def notify_critical(self, message: str) -> None
    async def send_daily_summary(self) -> None
```

---

## Configuration Keys (`system_config` table)

| Key                         | Type   | Default  |
|-----------------------------|--------|----------|
| `paper_mode`                | bool   | true     |
| `max_open_positions`        | int    | 5        |
| `max_position_size_pct`     | float  | 10.0     |
| `daily_loss_limit_pct`      | float  | 3.0      |
| `weekly_loss_limit_pct`     | float  | 8.0      |
| `drawdown_pause_pct`        | float  | 15.0     |
| `drawdown_stop_pct`         | float  | 25.0     |
| `dream_mode_enabled`        | bool   | true     |
| `dream_mode_auto_evolve`    | bool   | false    |
| `llm_cost_guard_daily_usd`  | float  | 10.0     |
| `alert_telegram_enabled`    | bool   | true     |
| `crypto_enabled`            | bool   | true     |
| `forex_enabled`             | bool   | false    |
| `indian_stocks_enabled`     | bool   | false    |
| `us_stocks_enabled`         | bool   | false    |

**Read at runtime:**
```python
paper_mode = await db.get_config("paper_mode") == "true"
```

**Override via SQL (takes effect on next config reload):**
```sql
UPDATE system_config SET value = 'false', updated_at = NOW()
WHERE key = 'dream_mode_auto_evolve';
```
