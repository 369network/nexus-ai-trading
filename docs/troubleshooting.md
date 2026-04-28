# NEXUS ALPHA — Troubleshooting Guide

---

## 1. Kite Auth Failure — TOTP Troubleshooting

### Symptom
```
KiteException: Invalid `api_key` or `access_token`
```
or
```
requests.exceptions.HTTPError: 403 Client Error
```

### Diagnosis

Zerodha Kite access tokens expire **every day at midnight IST**. You must re-authenticate daily.

```bash
# Check token age
python -c "
import os; from datetime import datetime
token_ts = os.getenv('KITE_TOKEN_GENERATED_AT', '')
print('Token generated:', token_ts)
"
```

### Solution — TOTP Re-authentication

1. **Ensure pyotp is installed:**
   ```bash
   pip install pyotp
   ```

2. **Generate new access token (run daily, ideally in cron at 06:30 IST):**
   ```python
   # scripts/kite_auth.py
   import pyotp, time
   from kiteconnect import KiteConnect

   api_key    = os.getenv("KITE_API_KEY")
   api_secret = os.getenv("KITE_API_SECRET")
   totp_secret = os.getenv("KITE_TOTP_SECRET")   # From Kite 2FA setup

   kite = KiteConnect(api_key=api_key)
   print("Login URL:", kite.login_url())
   # After manual login, copy the `request_token` from the redirect URL
   # OR automate with Selenium if you have the credentials

   totp = pyotp.TOTP(totp_secret)
   current_otp = totp.now()
   print("Current OTP:", current_otp)
   ```

3. **Update `.env` with new token:**
   ```bash
   KITE_ACCESS_TOKEN=your_new_token_here
   KITE_TOKEN_GENERATED_AT=$(date -u +%Y-%m-%dT%H:%M:%SZ)
   ```

4. **Cron for daily re-auth:**
   ```cron
   0 1 * * 1-5 /home/nexus/nexus-alpha/.venv/bin/python /home/nexus/nexus-alpha/scripts/kite_auth.py >> /var/log/nexus/kite_auth.log 2>&1
   ```

### Common TOTP Issues

| Error                          | Fix                                                    |
|--------------------------------|--------------------------------------------------------|
| OTP always wrong               | System clock drift — run `ntpdate -u pool.ntp.org`     |
| "Too many login attempts"      | Wait 30 min before retrying                            |
| TOTP secret lost               | Re-enroll 2FA in Kite settings                         |

---

## 2. TA-Lib Installation Errors

### Symptom
```
ImportError: libta_lib.so.0: cannot open shared object file
```
or
```
error: command 'gcc' failed with exit code 1
```

### Platform-Specific Solutions

#### Ubuntu/Debian (VPS)
```bash
# Build from source (most reliable)
cd /tmp
wget https://prdownloads.sourceforge.net/ta-lib/ta-lib-0.4.0-src.tar.gz
tar -xzf ta-lib-0.4.0-src.tar.gz
cd ta-lib
./configure --prefix=/usr
make -j$(nproc)
sudo make install
sudo ldconfig

# Then install Python wrapper
pip install TA-Lib
```

#### macOS (Homebrew)
```bash
brew install ta-lib
pip install TA-Lib
```

#### Windows (WSL2 recommended)
```powershell
# Option 1: Pre-built wheel (easiest)
pip install TA-Lib-prebuilt   # unofficial but works

# Option 2: Use conda
conda install -c conda-forge ta-lib

# Option 3: Download pre-built DLL
# Download ta-lib-0.4.0-msvc.zip from SourceForge
# Extract to C:\ta-lib
set TA_LIBRARY_PATH=C:\ta-lib\lib
set TA_INCLUDE_PATH=C:\ta-lib\include
pip install TA-Lib
```

#### "Function not found" after install
```bash
# Verify C library is installed
ldconfig -p | grep ta_lib
# Should show: libta_lib.so.0 => /usr/lib/libta_lib.so.0

# If missing: re-run ldconfig
sudo ldconfig
```

#### Python wrapper version mismatch
```bash
pip install TA-Lib==0.4.28  # Pin to a specific version that matches C lib 0.4.0
```

---

## 3. Supabase Connection Issues

### Symptom
```
APIError: JWTError: invalid token
```
or
```
httpx.ConnectTimeout: Connection to [supabase_url] timed out
```

### RLS Configuration

If you get `Row not found` or empty results when you know data exists:

```sql
-- Check RLS policies
SELECT tablename, policyname, cmd, roles
FROM pg_policies
WHERE tablename = 'market_data';

-- Grant service_role access (run as superuser)
CREATE POLICY "service_role_access" ON market_data
    FOR ALL TO service_role USING (TRUE) WITH CHECK (TRUE);

-- Verify you're using the SERVICE KEY, not the ANON KEY
-- SERVICE KEY: has full bypass of RLS
-- ANON KEY: subject to RLS policies
```

Ensure `.env` has:
```bash
SUPABASE_SERVICE_KEY=eyJhbGc...  # starts with 'eyJ', NOT the anon key
```

### SSL Configuration

If SSL errors occur:
```python
# In supabase_client.py
from supabase import create_client, ClientOptions

options = ClientOptions(
    postgrest_client_timeout=30,
    storage_client_timeout=30,
)
client = create_client(url, key, options)
```

For self-hosted Supabase with custom certs:
```bash
export SSL_CERT_FILE=/path/to/cert.pem
```

### Connection Pool Exhaustion

If you see "too many connections":
```sql
-- Check current connections
SELECT count(*) FROM pg_stat_activity WHERE datname = 'postgres';

-- Kill idle connections
SELECT pg_terminate_backend(pid)
FROM pg_stat_activity
WHERE datname = 'postgres'
  AND state = 'idle'
  AND state_change < NOW() - INTERVAL '5 minutes';
```

In application: ensure you're using connection pooling (Supabase uses PgBouncer automatically on Supabase cloud).

### Realtime Subscription Not Receiving Events

```javascript
// Dashboard: check realtime subscription
const channel = supabase
  .channel('nexus_signals')
  .on('postgres_changes', {
    event: 'INSERT',
    schema: 'public',
    table: 'signals'
  }, (payload) => console.log(payload))
  .subscribe((status) => {
    console.log('Realtime status:', status)  // Should be 'SUBSCRIBED'
  })
```

If status is `CHANNEL_ERROR`:
1. Check that the table is in the realtime publication: `SELECT * FROM pg_publication_tables WHERE pubname = 'nexus_realtime';`
2. Check that Realtime is enabled in Supabase dashboard → Database → Replication

---

## 4. High LLM Costs

### Diagnosis

```bash
# Check today's LLM spend (in Supabase)
python -c "
import os
from supabase import create_client
from datetime import date

sb = create_client(os.getenv('SUPABASE_URL'), os.getenv('SUPABASE_SERVICE_KEY'))
result = sb.table('model_performance').select('model_name,tokens_used').gte('recorded_at', str(date.today())).execute()
total = sum(r.get('tokens_used', 0) for r in result.data)
print(f'Total tokens today: {total:,}')
# Estimate cost: GPT-4o ~\$5/1M tokens output
print(f'Estimated cost (GPT-4o): \${total * 5 / 1_000_000:.2f}')
"
```

### Cost Guards

1. **Daily spend limit** (in `system_config`):
   ```sql
   UPDATE system_config SET value = '5.0'
   WHERE key = 'llm_cost_guard_daily_usd';
   ```

2. **Model selection** — route cheaper models to lower-stakes decisions:
   ```yaml
   # config/llm.yaml
   model_routing:
     high_confidence_signal: gpt-4o          # Full analysis
     low_confidence_signal: gpt-3.5-turbo    # Quick reject
     agent_debate: claude-3-haiku            # Cheaper per agent
     dream_mode: gemini-pro                  # Overnight optimisation
   ```

3. **Reduce agent calls** — only debate high-confidence candidates:
   ```yaml
   min_confidence_for_debate: 0.60   # Only debate signals above this threshold
   ```

4. **Cache common analyses** — identical indicator snapshots can reuse previous agent responses:
   ```python
   # In agent_coordinator.py
   cache_key = hash_indicator_snapshot(indicators)
   if cache_key in self._response_cache:
       return self._response_cache[cache_key]
   ```

5. **Switch to local models** during high-volume periods:
   ```bash
   # Install Ollama
   curl -fsSL https://ollama.com/install.sh | sh
   ollama pull llama3
   
   # In .env
   LLM_FALLBACK_LOCAL=true
   OLLAMA_BASE_URL=http://localhost:11434
   ```

---

## 5. Circuit Breaker False Positives

### Symptom

Circuit breakers triggering too frequently in normal market conditions, causing missed trades.

### Diagnosis

```python
# Check recent circuit breaker events
from supabase import create_client
sb = create_client(...)
events = sb.table('risk_events').select('*').eq('event_type', 'CIRCUIT_BREAKER_TRIPPED').order('created_at', desc=True).limit(20).execute()
for e in events.data:
    print(e['circuit_breaker'], e['trigger_value'], e['threshold_value'], e['created_at'])
```

### Tuning Guide

#### CB-1 Flash Crash (too sensitive in volatile crypto)

```sql
-- Increase threshold from 5% to 7% for crypto
UPDATE system_config SET value = '7.0'
WHERE key = 'flash_crash_pct';
```

Or make it timeframe-aware:
```yaml
circuit_breakers:
  flash_crash:
    crypto_1h: 5.0    # percent
    crypto_4h: 8.0
    forex_1h: 2.0
    stocks_1h: 3.0
```

#### CB-3 Volume Anomaly (false positives on earnings/news)

```sql
-- Increase multiplier from 10x to 15x for stocks
UPDATE system_config SET value = '15.0'
WHERE key = 'volume_anomaly_multiplier';
```

Add a whitelist for expected high-volume events:
```python
# In circuit_breaker.py
EXPECTED_HIGH_VOLUME_EVENTS = [
    {"symbol": "AAPL", "date": "2025-01-31", "reason": "Earnings"},
]
```

#### CB-5 P&L Spike (too sensitive in 1-minute timeframe)

The 5-minute P&L spike check calculates against open position mark-to-market, which is noisy on short timeframes.

Solution: only enable for timeframes ≥ 5m:
```python
if self.settings.primary_timeframe in ("1m", "3m"):
    cb5_enabled = False
```

#### Auto-cool-off period

Prevent re-trigger for 30 minutes after a circuit breaker fires:
```python
if cb.last_triggered and (datetime.now() - cb.last_triggered).seconds < 1800:
    return False  # Still in cool-off period
```

---

## 6. WebSocket Disconnections

### Symptom

```
ConnectionResetError: [Errno 104] Connection reset by peer
websockets.exceptions.ConnectionClosedError: received 1011 (unexpected error)
```

### Solution

The WebSocket connector implements automatic reconnection with exponential backoff. If disconnections are frequent:

1. **Check network stability:**
   ```bash
   ping api.binance.com -c 20
   # Packet loss > 5% indicates network issues
   ```

2. **Increase ping interval:**
   ```yaml
   websocket:
     ping_interval: 20   # seconds (default)
     ping_timeout: 10
     reconnect_delay: 5
     max_reconnects: 10
   ```

3. **Check for rate limits:**
   ```
   Binance: 1200 requests/min, 10 WebSocket connections per IP
   OANDA: 20 requests/second
   ```

4. **Use backup REST polling** during WS outages:
   ```python
   # In MarketOrchestrator
   if ws_consecutive_failures > 3:
       # Switch to REST polling every 10 seconds
       await self._start_rest_polling_mode()
   ```

---

## 7. Bot Not Generating Signals

### Diagnosis Checklist

```bash
# 1. Check bot is running
systemctl status nexus-bot.service

# 2. Check candles are being received (last 10 minutes)
python scripts/health_check.py

# 3. Check market hours
python -c "
from datetime import datetime, timezone
now = datetime.now(timezone.utc)
print('UTC:', now.strftime('%H:%M %Z'), 'Weekday:', now.weekday())
# 0=Mon, 4=Fri, 5=Sat, 6=Sun
"

# 4. Check if circuit breakers are tripped
python -c "
from supabase import create_client; import os
sb = create_client(os.getenv('SUPABASE_URL'), os.getenv('SUPABASE_SERVICE_KEY'))
events = sb.table('risk_events').select('event_type,resolved_at').is_('resolved_at', 'null').execute()
print('Unresolved risk events:', len(events.data))
for e in events.data:
    print(' -', e['event_type'])
"

# 5. Check strategy is producing candidates (debug log)
LOG_LEVEL=DEBUG python scripts/run_paper.py 2>&1 | head -100
```

### Common Causes

| Cause                        | Fix                                          |
|------------------------------|----------------------------------------------|
| Market closed (weekend/holiday) | Normal — crypto is 24/7; stocks/forex have hours |
| ADX < 25 (no trend)         | Adjust `min_adx` or wait for clearer market   |
| Daily loss limit hit         | Check `risk_events` table; reset via dashboard|
| All agents voting NEUTRAL    | Check LLM API keys; check indicator data      |
| Edge filter rejecting        | Lower `min_expected_value` threshold in config|

---

## 8. Memory / OOM Kills

### Symptom

Bot process killed with no error message; `systemctl status nexus-bot` shows "code=killed".

### Check

```bash
# Check for OOM kill
dmesg | grep -i "out of memory" | tail -20
journalctl -u nexus-bot -b -1 | grep -i "kill\|oom\|memory"
```

### Fix

1. **Reduce candle cache size:**
   ```python
   # In market_orchestrator.py
   CANDLE_CACHE_SIZE = 200   # Reduce from 500
   ```

2. **Limit symbols:**
   ```yaml
   # config/markets.yaml
   crypto:
     symbols: ["BTCUSDT", "ETHUSDT"]   # 2 instead of 5
   ```

3. **Increase VPS memory:**
   - Nexus Alpha requires minimum 4GB RAM for crypto only
   - 8GB recommended for multi-market

4. **Add swap:**
   ```bash
   fallocate -l 4G /swapfile
   chmod 600 /swapfile
   mkswap /swapfile
   swapon /swapfile
   echo '/swapfile none swap sw 0 0' >> /etc/fstab
   ```
