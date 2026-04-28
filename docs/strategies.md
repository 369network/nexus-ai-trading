# NEXUS ALPHA — Strategy Documentation

## Overview

NEXUS ALPHA implements 5 production strategies across multiple markets. All strategies produce candidate signals that are then debated by the 7-agent ensemble. Strategy parameters are optimised nightly by Dream Mode.

---

## Strategy 1: TrendMomentum

**Category:** Trend Following  
**Markets:** Crypto (primary), US Stocks  
**Timeframes:** 1h, 4h  

### Entry Conditions

**LONG entry (all must be true):**
1. EMA(8) crosses above EMA(21) in the current or previous candle
2. ADX(14) ≥ 25 (trend strength confirmed)
3. RSI(14) > 55 (momentum bias up)
4. Volume ≥ 1.5× 20-period average
5. Price above EMA(50) (major trend filter)
6. Higher timeframe (4h or 1d) not in opposing regime

**SHORT entry (mirror conditions):**
1. EMA(8) crosses below EMA(21)
2. ADX(14) ≥ 25
3. RSI(14) < 45
4. Volume ≥ 1.5× 20-period average
5. Price below EMA(50)

### Exit Conditions

- **Stop loss:** 1.5× ATR(14) from entry
- **TP1:** +1.5× stop distance (close 33%)
- **TP2:** +3.0× stop distance (close 33%)
- **TP3:** Trailing at 1.5× ATR (close final 34%)
- **Timeout:** Close if in position >48h with no TP hit (crypto), >5 days (stocks)

### Default Parameters

```yaml
ema_fast: 8
ema_slow: 21
rsi_period: 14
rsi_threshold: 55
atr_period: 14
atr_stop_multiplier: 1.5
atr_tp_multiplier: 3.0
volume_factor: 1.5
min_adx: 25
```

### Backtested Performance (90 days, BTCUSDT 1h)

| Metric           | Value    |
|------------------|----------|
| Total trades     | 47       |
| Win rate         | 58.0%    |
| Profit factor    | 1.82     |
| Sharpe ratio     | 1.34     |
| Max drawdown     | 8.2%     |
| Avg trade R:R    | 1:2.1    |

### When It Works Best

- Trending markets (ADX > 30)
- Post-consolidation breakouts with volume
- Bitcoin dominance trending

### When It Fails

- Ranging/choppy markets (ADX < 20)
- News-driven whipsaw moves
- Low-liquidity periods (weekends for crypto)

---

## Strategy 2: MeanReversionBB

**Category:** Mean Reversion  
**Markets:** Crypto, Forex  
**Timeframes:** 1h, 4h  

### Entry Conditions

**LONG entry:**
1. Price closes below Bollinger Band lower (period=20, std=2.0)
2. RSI(14) < 30 (oversold)
3. Market regime: RANGING (ADX < 20)
4. Not in downtrend on higher timeframe (4h/1d EMA not steeply negative)
5. Volume within normal range (not >3× avg, which could indicate panic selling)

**SHORT entry:**
1. Price closes above Bollinger Band upper
2. RSI(14) > 70 (overbought)
3. Market regime: RANGING
4. Not in uptrend on higher timeframe

### Exit Conditions

- **Stop loss:** 1.0× ATR(14) beyond the band (wide to avoid noise)
- **Take profit:** Bollinger Band middle (mean)
- **Alternative TP:** When RSI crosses back through 50

### Default Parameters

```yaml
bb_period: 20
bb_std: 2.0
rsi_period: 14
rsi_oversold: 30
rsi_overbought: 70
atr_stop_multiplier: 1.0
max_adx: 20   # Only trade when ranging
```

### Backtested Performance (90 days, ETHUSDT 1h)

| Metric           | Value    |
|------------------|----------|
| Total trades     | 83       |
| Win rate         | 65.0%    |
| Profit factor    | 1.45     |
| Sharpe ratio     | 1.12     |
| Max drawdown     | 6.4%     |
| Avg trade R:R    | 1:1.4    |

### Notes

Higher win rate but lower R:R than TrendMomentum — suited for sideways markets. The Dream Mode regularly optimises BB std (testing 1.5, 2.0, 2.5) and RSI thresholds.

---

## Strategy 3: BreakoutVolume

**Category:** Momentum / Breakout  
**Markets:** Crypto, US Stocks  
**Timeframes:** 1h, 4h, 1d  

### Entry Conditions

**LONG entry:**
1. Price breaks above highest high of last 20 candles (Donchian Channel top)
2. Volume ≥ 2.0× 20-period average (mandatory — no volume = no breakout)
3. ATR buffer: price must be ≥ 0.2% above breakout level (avoid false breaks)
4. Confirmation: close above the level (not just a wick)
5. No major resistance within 2% (volume profile check)

**SHORT entry (mirror):**
1. Price breaks below lowest low of last 20 candles
2. Volume ≥ 2.0× average
3. ATR buffer applied below
4. Confirmation close

### Exit Conditions

- **Stop loss:** 2.0× ATR (wider than other strategies to avoid re-entry whipsaw)
- **TP1:** 1× stop distance (capture quick breakout move)
- **TP2:** Previous major resistance/support level
- **TP3:** Trail at 1.5× ATR

### Default Parameters

```yaml
lookback_bars: 20
volume_multiplier: 2.0
atr_buffer_pct: 0.2
confirmation_bars: 1
atr_stop_multiplier: 2.0
```

### Backtested Performance (90 days, BTCUSDT 4h)

| Metric           | Value    |
|------------------|----------|
| Total trades     | 23       |
| Win rate         | 52.0%    |
| Profit factor    | 2.10     |
| Sharpe ratio     | 1.56     |
| Max drawdown     | 10.5%    |
| Avg trade R:R    | 1:2.8    |

### Notes

Lower win rate but highest profit factor — breakouts when they work are large. Accepts that most setups fail but one winner pays for 2–3 losers.

---

## Strategy 4: ScalpEMA (Forex)

**Category:** Short-term scalping  
**Markets:** Forex (EUR/USD, GBP/USD)  
**Timeframes:** 15m, 1h  
**Session restriction:** London + NY overlap only (13:00–17:00 UTC)

### Entry Conditions

**LONG entry:**
1. EMA(5) > EMA(13) (fast > slow)
2. RSI(7) crosses above 50 (momentum shift)
3. Price within 1 pip of EMA(5) (entry pullback)
4. Session: London/NY overlap active
5. No major economic event within 30 minutes

**SHORT entry (mirror).**

### Exit Conditions

- **Stop loss:** 7 pips (fixed for forex scalping)
- **Take profit:** 10 pips (fixed)
- **Time stop:** Close position if not filled within 2 hours

### Default Parameters

```yaml
ema_fast: 5
ema_slow: 13
rsi_period: 7
session_start_utc: "13:00"
session_end_utc: "17:00"
pip_target: 10
pip_stop: 7
```

### Backtested Performance (90 days, EURUSD 1h)

| Metric           | Value    |
|------------------|----------|
| Total trades     | 156      |
| Win rate         | 61.0%    |
| Profit factor    | 1.38     |
| Sharpe ratio     | 1.05     |
| Max drawdown     | 4.2%     |

---

## Strategy 5: SwingSupRes (Indian Stocks)

**Category:** Support/Resistance Swing Trading  
**Markets:** Indian Stocks (NSE)  
**Timeframes:** 1h, 1d  
**Hours restriction:** NSE session only (09:15–15:30 IST)

### Entry Conditions

**LONG at support:**
1. Price comes within 0.3% of an identified support level
2. Support level has been tested ≥ 2 times previously
3. Bullish candle pattern at support (hammer, engulfing, inside bar breakout)
4. Volume ≥ 1.5× average at the support touch
5. RSI not overbought (< 65)

**SHORT at resistance (mirror conditions).**

### Support/Resistance Identification

```python
# Swing high/low detection (lookback: 50 bars)
For each candle i:
  is_swing_high = high[i] > high[i-n:i] and high[i] > high[i+1:i+n]
  is_swing_low  = low[i]  < low[i-n:i]  and low[i]  < low[i+1:i+n]
  n = 5 (bars on each side)

# Level clustering (within 0.5% = same level)
levels = cluster_nearby_swings(swing_highs, swing_lows, tolerance=0.005)
```

### Exit Conditions

- **Stop loss:** 1.5× ATR below support (or above resistance for shorts)
- **Take profit:** Previous resistance (for longs) or support (for shorts)
- **Time stop:** Close at end of session if in profit < 0.5%

### Default Parameters

```yaml
lookback_bars: 50
touch_tolerance_pct: 0.3
min_touches: 2
volume_confirm: true
atr_stop_multiplier: 1.5
```

### Backtested Performance (90 days, RELIANCE 1d)

| Metric           | Value    |
|------------------|----------|
| Total trades     | 31       |
| Win rate         | 64.0%    |
| Profit factor    | 1.72     |
| Sharpe ratio     | 1.28     |
| Max drawdown     | 5.8%     |

---

## Strategy Selection Logic

At each candle close, strategies are evaluated in priority order:

1. `TrendMomentum` — checked first if ADX > 25
2. `BreakoutVolume` — checked if volume anomaly detected
3. `MeanReversionBB` — checked if ADX < 20 (ranging)
4. `ScalpEMA` — checked only during session hours (forex)
5. `SwingSupRes` — checked if near a known S/R level (Indian stocks)

The first strategy to produce a valid candidate signal wins. If no strategy produces a signal, the candle is skipped (no trade).

---

## Strategy Performance Tracking

All strategy performance is tracked in `model_performance` table. After each trade closes, the system:

1. Records predicted probability (confidence at signal time)
2. Records actual outcome (1=profitable, 0=loss)
3. Calculates Brier score
4. Updates rolling win rate, Sharpe, profit factor

If a strategy's rolling Sharpe falls below 0.5 over 30 days, Dream Mode is triggered automatically.
