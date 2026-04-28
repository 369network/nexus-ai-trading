# NEXUS ALPHA — Operations Runbook

**VPS:** 187.77.140.75 | **App dir:** `/opt/nexus-alpha` | **Container:** `nexus-paper-bot`

---

## 1. Deployment

### Quick Deploy (recommended)

```bash
# From your local machine
ssh root@187.77.140.75
cd /opt/nexus-alpha
bash deploy/vps_deploy.sh
```

### Manual Steps

```bash
# 1. SSH into VPS
ssh root@187.77.140.75

# 2. Pull latest code
cd /opt/nexus-alpha && git pull origin main

# 3. Rebuild images
docker compose -f docker-compose.paper.yml build --no-cache nexus-bot

# 4. Apply any new .env changes
nano /opt/nexus-alpha/.env

# 5. Restart the stack
docker compose -f docker-compose.paper.yml up -d --remove-orphans

# 6. Verify all containers are healthy
docker compose -f docker-compose.paper.yml ps
```

### First-Time Setup

```bash
# Run security hardening (once)
sudo bash /opt/nexus-alpha/deploy/security_hardening.sh

# Install and enable the systemd service
cp /opt/nexus-alpha/deploy/systemd/nexus-alpha.service /etc/systemd/system/
systemctl daemon-reload
systemctl enable --now nexus-alpha.service
systemctl status nexus-alpha.service
```

---

## 2. Monitoring

| Interface   | URL                                  | Credentials         |
|-------------|--------------------------------------|---------------------|
| Grafana     | http://187.77.140.75:3001            | admin / see .env    |
| Prometheus  | http://187.77.140.75:9090            | No auth (internal)  |
| Health API  | http://187.77.140.75:8080/health     | Public              |

### Health Check

```bash
curl -s http://187.77.140.75:8080/health | python3 -m json.tool
```

A healthy response looks like:
```json
{
  "status": "ok",
  "mode": "paper",
  "uptime_seconds": 3600,
  "open_positions": 2,
  "daily_pnl_pct": 0.42
}
```

### Grafana Dashboards

- **Trading Overview** — PnL, win rate, open positions
- **System** — CPU, memory, Docker container metrics
- **LLM** — API call latency, token usage, error rate

---

## 3. Common Operations

### View Logs

```bash
# Follow live bot logs
docker logs -f nexus-paper-bot

# Last 500 lines
docker logs --tail 500 nexus-paper-bot

# Via systemd (includes restarts)
journalctl -u nexus-alpha.service -f

# Filter for errors only
docker logs nexus-paper-bot 2>&1 | grep -i error
```

### Check Health

```bash
curl http://187.77.140.75:8080/health
```

### Restart Bot

```bash
# Restart only the bot container (keeps Grafana/Prometheus running)
docker compose -f /opt/nexus-alpha/docker-compose.paper.yml restart nexus-bot

# Full stack restart
systemctl restart nexus-alpha.service
```

### Update Bot

```bash
cd /opt/nexus-alpha
git pull origin main
docker compose -f docker-compose.paper.yml build nexus-bot
docker compose -f docker-compose.paper.yml up -d nexus-bot
docker logs -f nexus-paper-bot   # confirm clean startup
```

### Stop / Start Stack

```bash
# Stop everything
systemctl stop nexus-alpha.service

# Start everything
systemctl start nexus-alpha.service

# Check status
systemctl status nexus-alpha.service
```

---

## 4. Incident Response

### Bot Container Is Down

```bash
# 1. Check container status
docker compose -f /opt/nexus-alpha/docker-compose.paper.yml ps

# 2. Inspect last 200 lines of logs for crash reason
docker logs --tail 200 nexus-paper-bot

# 3. Check Supabase connectivity
curl -s "$(grep SUPABASE_URL /opt/nexus-alpha/.env | cut -d= -f2)/health"

# 4. Check disk space (full disk = silent crash)
df -h /opt/nexus-alpha

# 5. Restart
docker compose -f /opt/nexus-alpha/docker-compose.paper.yml up -d nexus-bot
```

### High Drawdown Alert

If daily drawdown exceeds the configured threshold (default 3%):

```bash
# 1. Trigger emergency stop via health endpoint
curl -X POST http://187.77.140.75:8080/control/emergency-stop \
  -H "X-Admin-Key: $(grep ADMIN_API_KEY /opt/nexus-alpha/.env | cut -d= -f2)"

# 2. Verify all positions are closed (paper mode — no real money at risk)
curl http://187.77.140.75:8080/positions

# 3. Review recent trades in Grafana → Trading Overview → Recent Trades

# 4. Adjust MAX_DAILY_DRAWDOWN_PCT in .env and restart
nano /opt/nexus-alpha/.env
systemctl restart nexus-alpha.service
```

### LLM API Errors

```bash
# 1. Check error frequency
docker logs nexus-paper-bot 2>&1 | grep -i "llm\|openai\|anthropic\|rate.limit" | tail -50

# 2. Verify API key is valid (test directly)
curl https://api.anthropic.com/v1/messages \
  -H "x-api-key: $(grep ANTHROPIC_API_KEY /opt/nexus-alpha/.env | cut -d= -f2)" \
  -H "anthropic-version: 2023-06-01" \
  -H "content-type: application/json" \
  -d '{"model":"claude-haiku-20240307","max_tokens":10,"messages":[{"role":"user","content":"ping"}]}'

# 3. If rate-limited: increase LLM_COOLDOWN_SECONDS in .env (default 2)
# 4. If key invalid: update ANTHROPIC_API_KEY / OPENAI_API_KEY in .env, restart
```

### Database / Supabase Connectivity Lost

```bash
# 1. Test connection
psql "$(grep DATABASE_URL /opt/nexus-alpha/.env | cut -d= -f2)" -c "SELECT 1"

# 2. Check if Supabase project is paused (free tier pauses after 1 week idle)
#    → Log in to supabase.com → Resume project

# 3. Rotate DB credentials if suspected compromise
#    → supabase.com → Settings → Database → Reset password
#    → Update DATABASE_URL in .env → systemctl restart nexus-alpha.service
```

---

## 5. Paper → Live Transition Checklist

Complete every item before switching `TRADING_MODE=live`.

- [ ] **30+ days of paper trading data** with consistent positive expectancy (Sharpe > 1.0)
- [ ] **Max drawdown observed** in paper mode is below your real-money tolerance
- [ ] **All strategy parameters** reviewed and stress-tested against historical data
- [ ] **Risk limits set**: `MAX_POSITION_SIZE_USD`, `MAX_DAILY_DRAWDOWN_PCT`, `MAX_OPEN_POSITIONS`
- [ ] **Exchange API keys created** with trade permissions only (no withdrawal permissions)
- [ ] **API keys stored** in `.env` (chmod 600) — never committed to git
- [ ] **Emergency stop tested**: confirm `/control/emergency-stop` endpoint closes all positions
- [ ] **Alerting verified**: Grafana alerts fire correctly to your notification channel
- [ ] **Start small**: set `MAX_POSITION_SIZE_USD` to 1–5% of intended capital for the first week live
- [ ] **Security hardening script run** and UFW firewall confirmed active (`ufw status`)

---

## 6. Backup & Recovery

### Backup .env

```bash
# On VPS: copy .env to secure local storage
scp root@187.77.140.75:/opt/nexus-alpha/.env ~/nexus-alpha-env.bak

# Or encrypt and store in a password manager
gpg --symmetric /opt/nexus-alpha/.env
```

**Never commit `.env` to git. It contains API keys and database credentials.**

### Backup State Volumes

```bash
# List volumes
docker volume ls | grep nexus

# Export a volume to a tar archive
docker run --rm \
  -v nexus-alpha_bot-state:/data \
  -v /opt/backups:/backup \
  alpine tar czf /backup/nexus-state-$(date +%Y%m%d).tar.gz -C /data .

# Restore a volume
docker run --rm \
  -v nexus-alpha_bot-state:/data \
  -v /opt/backups:/backup \
  alpine tar xzf /backup/nexus-state-20240428.tar.gz -C /data
```

### Restore From Scratch

```bash
ssh root@187.77.140.75
git clone https://github.com/your-org/nexus-alpha /opt/nexus-alpha
cp ~/nexus-alpha-env.bak /opt/nexus-alpha/.env
chmod 600 /opt/nexus-alpha/.env
bash /opt/nexus-alpha/deploy/security_hardening.sh
cp /opt/nexus-alpha/deploy/systemd/nexus-alpha.service /etc/systemd/system/
systemctl daemon-reload
systemctl enable --now nexus-alpha.service
```

---

## 7. Performance Tuning

Key environment variables in `/opt/nexus-alpha/.env`:

| Variable | Default | Effect |
|---|---|---|
| `LLM_COOLDOWN_SECONDS` | `2` | Delay between LLM calls. Increase if rate-limited. |
| `SIGNAL_INTERVAL_SECONDS` | `60` | How often the bot evaluates new signals. Lower = more CPU. |
| `MAX_OPEN_POSITIONS` | `5` | Cap on concurrent positions. Lower reduces capital at risk. |
| `MAX_POSITION_SIZE_USD` | `100` | Per-trade size in paper mode. |
| `MAX_DAILY_DRAWDOWN_PCT` | `3.0` | Auto-stop threshold as % of starting capital. |
| `LLM_PROVIDER` | `anthropic` | Switch to `openai` if Anthropic rate limits are hit. |
| `LLM_MODEL` | `claude-haiku-20240307` | Use Haiku for speed/cost; Sonnet for signal quality. |
| `LOG_LEVEL` | `INFO` | Set to `DEBUG` temporarily for troubleshooting (verbose). |
| `DB_POOL_SIZE` | `5` | Supabase connection pool. Increase if seeing connection errors under load. |

After editing `.env`:
```bash
systemctl restart nexus-alpha.service
docker logs -f nexus-paper-bot   # confirm startup with new settings
```
