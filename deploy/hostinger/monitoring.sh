#!/usr/bin/env bash
# =============================================================================
# NEXUS ALPHA - Health Monitoring Script
# Runs every 5 minutes via cron. Restarts failed services and sends Telegram alerts.
#
# Cron entry:
#   */5 * * * * /usr/local/bin/nexus-monitor.sh >> /var/log/nexus/monitor.log 2>&1
# =============================================================================
set -euo pipefail

NEXUS_DIR="${NEXUS_DIR:-/home/nexus/nexus-alpha}"
LOG_DIR="${LOG_DIR:-/var/log/nexus}"
PYTHON="${NEXUS_DIR}/.venv/bin/python"
HEALTH_SCRIPT="${NEXUS_DIR}/scripts/health_check.py"

TIMESTAMP=$(date '+%Y-%m-%dT%H:%M:%S')
echo "[${TIMESTAMP}] Starting health check…"

# ---------------------------------------------------------------------------
# Load environment variables
# ---------------------------------------------------------------------------
if [ -f "${NEXUS_DIR}/.env" ]; then
    # Export only non-comment, non-empty lines
    set -o allexport
    # shellcheck disable=SC1090
    source <(grep -v '^#' "${NEXUS_DIR}/.env" | grep -v '^[[:space:]]*$')
    set +o allexport
fi

TELEGRAM_TOKEN="${TELEGRAM_BOT_TOKEN:-}"
TELEGRAM_CHAT="${TELEGRAM_CHAT_ID:-}"

# ---------------------------------------------------------------------------
# Telegram helper
# ---------------------------------------------------------------------------
send_telegram() {
    local msg="$1"
    if [ -z "${TELEGRAM_TOKEN}" ] || [ -z "${TELEGRAM_CHAT}" ]; then
        echo "[WARN] Telegram not configured — skipping alert."
        return
    fi
    curl -s -X POST \
        "https://api.telegram.org/bot${TELEGRAM_TOKEN}/sendMessage" \
        -H 'Content-Type: application/json' \
        -d "{\"chat_id\":\"${TELEGRAM_CHAT}\",\"text\":\"${msg}\",\"parse_mode\":\"HTML\"}" \
        --max-time 10 > /dev/null || echo "[WARN] Telegram send failed."
}

# ---------------------------------------------------------------------------
# Service check + restart
# ---------------------------------------------------------------------------
check_and_restart_service() {
    local service_name="$1"
    local status

    status=$(systemctl is-active "${service_name}" 2>/dev/null || echo "inactive")

    if [ "${status}" != "active" ]; then
        echo "[CRIT] ${service_name} is ${status} — attempting restart…"

        # Check restart count (prevent restart loops)
        restart_count=$(journalctl -u "${service_name}" --since "1 hour ago" -q \
            | grep -c "Started\|start request" 2>/dev/null || echo "0")

        if [ "${restart_count}" -gt 10 ]; then
            echo "[CRIT] ${service_name} has restarted ${restart_count} times in 1h — not auto-restarting!"
            send_telegram "<b>NEXUS CRITICAL</b>
Service ${service_name} has failed ${restart_count}+ times in 1 hour.
Manual intervention required!
Host: $(hostname)"
            return 1
        fi

        systemctl restart "${service_name}" 2>&1 || true
        sleep 5

        new_status=$(systemctl is-active "${service_name}" 2>/dev/null || echo "inactive")
        if [ "${new_status}" == "active" ]; then
            echo "[OK] ${service_name} restarted successfully."
            send_telegram "<b>NEXUS ALERT</b>
Service ${service_name} was <code>${status}</code> and has been restarted.
Status: ${new_status}
Host: $(hostname)"
        else
            echo "[CRIT] ${service_name} restart FAILED — still ${new_status}."
            send_telegram "<b>NEXUS CRITICAL</b>
Service ${service_name} FAILED TO RESTART.
Was: ${status}
Now: ${new_status}
Host: $(hostname)
Check: journalctl -u ${service_name} -n 50"
        fi
    else
        echo "[OK] ${service_name} is active."
    fi
}

# ---------------------------------------------------------------------------
# Run Python health check script
# ---------------------------------------------------------------------------
HEALTH_EXIT=0
if [ -f "${PYTHON}" ] && [ -f "${HEALTH_SCRIPT}" ]; then
    HEALTH_OUTPUT=$("${PYTHON}" "${HEALTH_SCRIPT}" --quiet --json 2>&1) || HEALTH_EXIT=$?
    echo "${HEALTH_OUTPUT}"

    if [ "${HEALTH_EXIT}" -eq 2 ]; then
        echo "[CRIT] Health check returned CRITICAL."
        # Telegram alert is sent by the health_check.py script itself
    elif [ "${HEALTH_EXIT}" -eq 1 ]; then
        echo "[WARN] Health check returned WARNING."
    fi
else
    echo "[WARN] Health check script not found at ${HEALTH_SCRIPT}"
fi

# ---------------------------------------------------------------------------
# Service status checks
# ---------------------------------------------------------------------------
SERVICES=("nexus-bot.service" "nexus-data.service" "redis.service")

for svc in "${SERVICES[@]}"; do
    check_and_restart_service "${svc}" || true
done

# ---------------------------------------------------------------------------
# Memory check (emergency)
# ---------------------------------------------------------------------------
if command -v free &>/dev/null; then
    MEM_USED_PCT=$(free | awk '/^Mem:/{printf "%.0f", $3/$2 * 100}')
    if [ "${MEM_USED_PCT}" -ge 95 ]; then
        echo "[CRIT] Memory usage critical: ${MEM_USED_PCT}%"
        send_telegram "<b>NEXUS CRITICAL</b>
Memory usage: ${MEM_USED_PCT}%
Host: $(hostname)
Consider reducing open positions or restarting services."
    fi
fi

# ---------------------------------------------------------------------------
# Disk check (emergency)
# ---------------------------------------------------------------------------
DISK_USED_PCT=$(df / | awk 'NR==2{print $5}' | tr -d '%')
if [ "${DISK_USED_PCT}" -ge 90 ]; then
    echo "[CRIT] Disk usage critical: ${DISK_USED_PCT}%"
    send_telegram "<b>NEXUS CRITICAL</b>
Disk usage: ${DISK_USED_PCT}%
Host: $(hostname)
Clean up logs: journalctl --vacuum-size=500M"
fi

TIMESTAMP=$(date '+%Y-%m-%dT%H:%M:%S')
echo "[${TIMESTAMP}] Health check complete. Exit=${HEALTH_EXIT}"
exit "${HEALTH_EXIT}"
