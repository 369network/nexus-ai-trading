# NEXUS ALPHA — Risk Framework

## Philosophy

Risk management is the primary defence against catastrophic loss. Every trade is evaluated through 5 independent layers before execution. The risk manager has the authority to reject any signal, regardless of agent consensus.

**Core principle:** Protect capital first; profit second.

---

## Risk Layer 1: Position Size Validation

Ensures no single position exceeds the maximum allowed exposure.

### Limits by Market Class

| Market        | Max Position Size | Max Positions | Max Correlated |
|---------------|-------------------|---------------|----------------|
| Crypto        | 10% of equity     | 3             | 2              |
| Forex         | 5% of equity      | 5             | 3              |
| Indian Stocks | 8% of equity      | 4             | 2              |
| US Stocks     | 8% of equity      | 4             | 2              |

### Position Sizing Methodology

**Quarter Kelly Criterion:**
```
Kelly fraction = (win_rate × avg_win - (1 - win_rate) × avg_loss) / avg_win
Quarter Kelly  = Kelly / 4   ← Never use full Kelly in live trading
Position size  = equity × quarter_kelly
```

**ATR-based sizing (primary method):**
```
risk_amount    = equity × risk_pct_per_trade (default: 1.0%)
stop_distance  = entry_price - stop_loss   (in price units)
quantity       = risk_amount / stop_distance
```

**Drawdown scaling:**
- At 5% drawdown: position sizes × 0.80
- At 10% drawdown: position sizes × 0.60
- At 15% drawdown: position sizes × 0.40 (approaching PAUSE threshold)

### Example

```
Equity: $100,000
Risk per trade: 1.0%
Risk amount: $1,000
ATR: 2,500 (BTC at $50,000, ATR = 2.5%)
Stop distance: 1.5 × ATR = $3,750
Quantity: $1,000 / $3,750 = 0.2667 BTC
Position value: 0.2667 × $50,000 = $13,333 (13.3% of equity — REJECTED if >10%)
```

Adjustment: reduce risk_pct to 0.75%, giving:
```
Risk amount: $750
Quantity: $750 / $3,750 = 0.200 BTC
Position value: 0.200 × $50,000 = $10,000 (10.0% — borderline, approved)
```

---

## Risk Layer 2: Correlation Check

Prevents over-exposure to correlated positions.

### Correlation Matrix (built at runtime)

The risk manager maintains a running correlation matrix of all open positions using the last 20 daily returns.

**Thresholds:**
- Correlation > 0.80 with any open position → REJECT new position
- Sum of correlations > 2.0 (portfolio highly correlated) → REJECT

**Exception:** Explicitly hedged positions (opposite direction) are allowed even with high correlation.

### Correlation Examples

| Pair          | Typical Correlation | Notes                            |
|---------------|---------------------|----------------------------------|
| BTC / ETH     | 0.85–0.95           | Very high — treat as correlated  |
| BTC / BNB     | 0.80–0.90           | High                             |
| EUR/USD / GBP/USD | 0.70–0.85       | Often correlated                 |
| AAPL / MSFT   | 0.60–0.80           | Moderate — monitor               |
| Gold / USD    | -0.30 to -0.50      | Typically negative correlation   |

---

## Risk Layer 3: Daily Loss Limit

Halts all new trading if the daily loss exceeds the threshold.

### Thresholds

| Threshold | Action                                | Reset              |
|-----------|---------------------------------------|--------------------|
| 3% daily  | HALT — no new positions for 24 hours  | Auto at midnight   |
| 5% daily  | HALT + Telegram alert                 | Manual reset req.  |

### Calculation

```
daily_loss_pct = (peak_equity_today - current_equity) / peak_equity_today × 100
```

Uses the intraday peak, not the start-of-day equity, to prevent gaming via early wins.

**Example:**
- Start of day equity: $100,000
- Midday peak: $101,500
- Current equity: $98,400
- Daily loss: ($101,500 - $98,400) / $101,500 = 3.05% → HALT triggered

---

## Risk Layer 4: Drawdown-Based Position Reduction

Dynamically reduces trading activity as drawdown increases.

### Drawdown States

| Drawdown    | State  | Action                                                        |
|-------------|--------|---------------------------------------------------------------|
| 0–5%        | NORMAL | Full position sizing                                          |
| 5–10%       | CAUTION| Position sizes × 0.80; alert if >7%                         |
| 10–15%      | WARNING| Position sizes × 0.60; telegram warning; review strategies   |
| 15–20%      | PAUSE  | No new positions; manage existing exits only                  |
| >25%        | STOP   | Close all positions; full system halt; alert                 |

### Drawdown Calculation

```
peak_equity = max(equity_curve)
current_drawdown_pct = (peak_equity - current_equity) / peak_equity × 100
```

Uses the all-time peak equity (since bot start or last reset), not rolling peak.

---

## Risk Layer 5: Tail Risk / Systemic Stop

Final safety net for catastrophic scenarios.

### Conditions

| Trigger                          | Action                                      |
|----------------------------------|---------------------------------------------|
| Drawdown ≥ 25%                  | Close ALL positions, halt system, alert     |
| 3+ circuit breakers in 1 hour   | System halt; require manual restart         |
| LLM API completely unavailable  | Fall back to rule-only mode (no agent debate)|
| Supabase unavailable > 5 min    | Halt new trades; continue managing exits    |

---

## Circuit Breakers (6 Total)

### CB-1: Flash Crash Detector

**Trigger:** Single candle drops > 5% (crypto) or > 3% (stocks/forex)

**Action:** Close any SHORT-side positions at risk; halt new longs for 15 minutes; alert

```
# Example: BTC drops from $50,000 to $47,000 in one 1h candle (-6%)
→ Circuit breaker 1 tripped
→ Any leveraged LONG positions: tighten stop to breakeven
→ No new LONG entries for 15 minutes
→ Telegram: "Flash crash detected: BTC -6.0% in 1h"
```

### CB-2: Liquidity Breaker

**Trigger:** Bid-ask spread > 3× the rolling 1-hour average spread

**Action:** No new market orders; limit orders only; alert

### CB-3: Volume Anomaly

**Trigger:** Volume > 10× the 20-period average on a single candle

**Action:** Wait 2 candles before new entries; alert; re-evaluate after normalisation

### CB-4: API Error Rate

**Trigger:** > 5 exchange API errors in 60 seconds

**Action:** Switch to data-only mode (no execution); re-check every 60s; alert if >10 min

### CB-5: P&L Spike

**Trigger:** Portfolio loses > 2% in any 5-minute window

**Action:** Pause new entries for 10 minutes; review all stops; alert

### CB-6: Correlation Cascade

**Trigger:** 3 or more open positions all moving against the bot simultaneously

**Action:** Reduce position sizes by 50%; evaluate closing weakest position; alert

---

## Stop Loss Methodology

All positions use ATR-based stops:

```
LONG entry:  stop_loss = entry_price - (ATR × stop_multiplier)
SHORT entry: stop_loss = entry_price + (ATR × stop_multiplier)

Default stop_multiplier by strategy:
  TrendMomentum:   1.5× ATR
  MeanReversionBB: 1.0× ATR
  BreakoutVolume:  2.0× ATR (wider to avoid noise)
```

### Trailing Stops

After position moves 2× ATR in profit direction, trailing stop activates:
```
LONG: trailing stop = current_high - (ATR × 1.5)
SHORT: trailing stop = current_low + (ATR × 1.5)
```

---

## Take Profit Levels

Three-tier take profit for position scaling:

| Level | Target             | Action                     |
|-------|--------------------|----------------------------|
| TP1   | 1.5× stop distance | Close 33% of position      |
| TP2   | 3.0× stop distance | Close 33% of position      |
| TP3   | 5.0× stop distance | Close remaining 34%; trail |

**Example (LONG, entry $50,000, stop $48,500, stop distance $1,500):**
- TP1: $52,250 → close 33%
- TP2: $54,500 → close 33%
- TP3: $57,500 → trail remaining

---

## Risk Events Logging

All risk decisions are stored in `risk_events` table with:
- Event type and severity (1–5)
- Trigger value and threshold
- Action taken
- Equity at time of event

This creates an audit trail for post-event review and backtesting parameter tuning.

---

## Configuration Reference

All thresholds are configurable via `system_config` table:

| Key                         | Default  | Description                          |
|-----------------------------|----------|--------------------------------------|
| `max_position_size_pct`     | 10.0     | Max single position (% equity)       |
| `daily_loss_limit_pct`      | 3.0      | Daily loss halt threshold            |
| `weekly_loss_limit_pct`     | 8.0      | Weekly loss limit                    |
| `drawdown_pause_pct`        | 15.0     | Drawdown → PAUSE mode                |
| `drawdown_stop_pct`         | 25.0     | Drawdown → FULL STOP                 |
| `max_open_positions`        | 5        | Maximum concurrent open positions    |
| `correlation_threshold`     | 0.80     | Max correlation with existing pos.   |
| `flash_crash_pct`           | 5.0      | Single-candle drop for CB-1          |
| `volume_anomaly_multiplier` | 10.0     | Volume × avg for CB-3                |
| `api_error_rate_limit`      | 5        | Errors/min for CB-4                  |
