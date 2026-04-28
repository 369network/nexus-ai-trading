#!/usr/bin/env python3
"""
NEXUS ALPHA - Health Check Script
Run as a cron job or monitoring agent. Checks all system components.

Exit codes:
  0 = healthy
  1 = warning (degraded but operational)
  2 = critical (action required)

Usage:
    python scripts/health_check.py
    python scripts/health_check.py --quiet    # Only output on failure
    python scripts/health_check.py --json     # JSON output for monitoring tools
"""

from __future__ import annotations

import argparse
import asyncio
import json
import logging
import os
import sys
import time
from dataclasses import asdict, dataclass, field
from datetime import datetime, timezone
from typing import Any, Dict, List, Optional, Tuple

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

# Severity levels
OK       = 0
WARNING  = 1
CRITICAL = 2


@dataclass
class CheckResult:
    name: str
    status: int          # OK / WARNING / CRITICAL
    message: str
    detail: Optional[str] = None
    latency_ms: Optional[float] = None


@dataclass
class HealthReport:
    timestamp: str
    overall_status: int
    checks: List[CheckResult] = field(default_factory=list)

    @property
    def status_name(self) -> str:
        return {0: "HEALTHY", 1: "WARNING", 2: "CRITICAL"}[self.overall_status]


# ---------------------------------------------------------------------------
# Individual check functions
# ---------------------------------------------------------------------------

async def check_supabase(url: str, key: str) -> CheckResult:
    t0 = time.monotonic()
    try:
        from supabase import create_client  # type: ignore
        sb = create_client(url, key)
        result = sb.table("system_config").select("key").limit(1).execute()
        latency = (time.monotonic() - t0) * 1000
        if result.data is not None:
            return CheckResult("Supabase", OK, "Connected", f"{len(result.data)} rows", latency)
        return CheckResult("Supabase", WARNING, "Empty response", None, latency)
    except Exception as exc:
        return CheckResult("Supabase", CRITICAL, f"Connection failed: {exc}", None)


async def check_binance_rest() -> CheckResult:
    t0 = time.monotonic()
    try:
        import aiohttp
        async with aiohttp.ClientSession() as s:
            async with s.get(
                "https://api.binance.com/api/v3/ping",
                timeout=aiohttp.ClientTimeout(total=10)
            ) as r:
                latency = (time.monotonic() - t0) * 1000
                if r.status == 200:
                    return CheckResult("Binance REST", OK, "Reachable", f"{latency:.0f}ms", latency)
                return CheckResult("Binance REST", WARNING, f"HTTP {r.status}", None, latency)
    except Exception as exc:
        return CheckResult("Binance REST", CRITICAL, f"Unreachable: {exc}", None)


async def check_binance_ws() -> CheckResult:
    """Check WebSocket endpoint reachability."""
    import ssl
    t0 = time.monotonic()
    try:
        import aiohttp
        async with aiohttp.ClientSession() as s:
            async with s.ws_connect(
                "wss://stream.binance.com:9443/ws/btcusdt@ping",
                timeout=aiohttp.ClientTimeout(total=15),
                ssl=ssl.create_default_context(),
            ) as ws:
                latency = (time.monotonic() - t0) * 1000
                await ws.close()
                return CheckResult("Binance WS", OK, "WebSocket reachable", f"{latency:.0f}ms", latency)
    except Exception as exc:
        return CheckResult("Binance WS", WARNING, f"WebSocket check failed: {exc}", None)


async def check_candle_freshness(sb_url: str, sb_key: str) -> CheckResult:
    """Check that crypto candles were received within the last 5 minutes."""
    try:
        from supabase import create_client  # type: ignore
        sb = create_client(sb_url, sb_key)

        result = (
            sb.table("market_data")
            .select("symbol,timeframe,timestamp")
            .eq("market", "crypto")
            .eq("timeframe", "1h")
            .order("timestamp", desc=True)
            .limit(5)
            .execute()
        )

        if not result.data:
            return CheckResult("Candle Freshness", WARNING, "No crypto candles in DB", None)

        now = datetime.now(timezone.utc)
        stale = []
        for row in result.data:
            ts = datetime.fromisoformat(row["timestamp"].replace("Z", "+00:00"))
            age_min = (now - ts).total_seconds() / 60
            if age_min > 5:
                stale.append(f"{row['symbol']}:{age_min:.0f}m old")

        if stale:
            return CheckResult(
                "Candle Freshness", WARNING,
                f"Stale candles: {', '.join(stale[:3])}", None,
            )
        return CheckResult("Candle Freshness", OK, "All candles fresh (<5min)")

    except Exception as exc:
        return CheckResult("Candle Freshness", CRITICAL, f"Check failed: {exc}", None)


async def check_bot_process() -> CheckResult:
    """Check if the nexus bot process is running."""
    try:
        import subprocess
        result = subprocess.run(
            ["systemctl", "is-active", "nexus-bot.service"],
            capture_output=True, text=True, timeout=10,
        )
        if result.returncode == 0 and "active" in result.stdout:
            return CheckResult("Bot Process", OK, "nexus-bot.service active")

        # Fallback: check for python process
        result2 = subprocess.run(
            ["pgrep", "-f", "nexus_alpha"],
            capture_output=True, text=True, timeout=5,
        )
        if result2.returncode == 0:
            pid = result2.stdout.strip().split()[0]
            return CheckResult("Bot Process", WARNING, f"Running as PID {pid} (not via systemd)")

        return CheckResult("Bot Process", CRITICAL, "nexus-bot.service NOT active")

    except FileNotFoundError:
        # systemctl not available (dev environment)
        return CheckResult("Bot Process", OK, "systemctl not available (dev environment)")
    except Exception as exc:
        return CheckResult("Bot Process", WARNING, f"Process check failed: {exc}")


async def check_memory_usage() -> CheckResult:
    """Check system memory usage."""
    try:
        import psutil  # type: ignore
        mem = psutil.virtual_memory()
        pct = mem.percent
        used_gb = mem.used / 1024 ** 3
        total_gb = mem.total / 1024 ** 3

        if pct >= 90:
            return CheckResult(
                "Memory", CRITICAL,
                f"Memory {pct:.0f}% used ({used_gb:.1f}/{total_gb:.1f}GB)"
            )
        if pct >= 80:
            return CheckResult(
                "Memory", WARNING,
                f"Memory {pct:.0f}% used ({used_gb:.1f}/{total_gb:.1f}GB)"
            )
        return CheckResult(
            "Memory", OK,
            f"Memory {pct:.0f}% used ({used_gb:.1f}/{total_gb:.1f}GB)"
        )
    except ImportError:
        # Read /proc/meminfo directly
        try:
            with open("/proc/meminfo") as f:
                lines = f.readlines()
            info = {}
            for line in lines:
                parts = line.split()
                if len(parts) >= 2:
                    info[parts[0].rstrip(":")] = int(parts[1])
            total = info.get("MemTotal", 0)
            avail = info.get("MemAvailable", 0)
            used_pct = (1 - avail / total) * 100 if total > 0 else 0
            if used_pct >= 90:
                return CheckResult("Memory", CRITICAL, f"Memory {used_pct:.0f}% used")
            if used_pct >= 80:
                return CheckResult("Memory", WARNING, f"Memory {used_pct:.0f}% used")
            return CheckResult("Memory", OK, f"Memory {used_pct:.0f}% used")
        except Exception:
            return CheckResult("Memory", OK, "Memory check not available")
    except Exception as exc:
        return CheckResult("Memory", WARNING, f"Memory check failed: {exc}")


async def check_disk_usage() -> CheckResult:
    """Check disk usage on the bot's working directory."""
    try:
        import shutil
        total, used, free = shutil.disk_usage("/")
        pct = used / total * 100
        free_gb = free / 1024 ** 3

        if pct >= 90:
            return CheckResult("Disk", CRITICAL, f"Disk {pct:.0f}% full ({free_gb:.1f}GB free)")
        if pct >= 75:
            return CheckResult("Disk", WARNING, f"Disk {pct:.0f}% full ({free_gb:.1f}GB free)")
        return CheckResult("Disk", OK, f"Disk {pct:.0f}% used ({free_gb:.1f}GB free)")
    except Exception as exc:
        return CheckResult("Disk", WARNING, f"Disk check failed: {exc}")


async def check_oanda() -> CheckResult:
    token = os.getenv("OANDA_ACCESS_TOKEN", "")
    if not token:
        return CheckResult("OANDA", OK, "Not configured (skipped)")
    try:
        import aiohttp
        practice = os.getenv("OANDA_PRACTICE", "true").lower() == "true"
        base = "https://api-fxpractice.oanda.com" if practice else "https://api-fxtrade.oanda.com"
        headers = {"Authorization": f"Bearer {token}"}
        async with aiohttp.ClientSession(headers=headers) as s:
            async with s.get(f"{base}/v3/accounts", timeout=aiohttp.ClientTimeout(total=10)) as r:
                if r.status == 200:
                    mode = "practice" if practice else "live"
                    return CheckResult("OANDA", OK, f"Connected ({mode})")
                return CheckResult("OANDA", WARNING, f"HTTP {r.status}")
    except Exception as exc:
        return CheckResult("OANDA", WARNING, f"OANDA check failed: {exc}")


async def check_risk_events(sb_url: str, sb_key: str) -> CheckResult:
    """Check for recent unresolved critical risk events."""
    try:
        from supabase import create_client  # type: ignore
        from datetime import timedelta
        sb = create_client(sb_url, sb_key)

        cutoff = (datetime.now(timezone.utc) - timedelta(hours=1)).isoformat()
        result = (
            sb.table("risk_events")
            .select("event_type,severity,created_at")
            .gte("created_at", cutoff)
            .is_("resolved_at", "null")
            .gte("severity", "4")
            .execute()
        )

        if result.data:
            types = [r["event_type"] for r in result.data]
            return CheckResult(
                "Risk Events", CRITICAL,
                f"{len(result.data)} unresolved critical risk events: {', '.join(set(types)[:3])}"
            )
        return CheckResult("Risk Events", OK, "No unresolved critical risk events")

    except Exception as exc:
        return CheckResult("Risk Events", WARNING, f"Risk check failed: {exc}")


async def check_open_positions(sb_url: str, sb_key: str) -> CheckResult:
    """Verify open positions count is within limits."""
    try:
        from supabase import create_client  # type: ignore
        sb = create_client(sb_url, sb_key)
        result = (
            sb.table("trades")
            .select("id", count="exact")
            .eq("status", "OPEN")
            .execute()
        )
        count = result.count or 0
        max_pos = int(os.getenv("MAX_OPEN_POSITIONS", "5"))

        if count > max_pos:
            return CheckResult(
                "Open Positions", CRITICAL,
                f"{count} open positions (max: {max_pos})"
            )
        return CheckResult("Open Positions", OK, f"{count}/{max_pos} positions open")

    except Exception as exc:
        return CheckResult("Open Positions", WARNING, f"Position check failed: {exc}")


# ---------------------------------------------------------------------------
# Telegram notification
# ---------------------------------------------------------------------------

async def send_telegram_alert(message: str) -> None:
    token   = os.getenv("TELEGRAM_BOT_TOKEN", "")
    chat_id = os.getenv("TELEGRAM_CHAT_ID", "")
    if not token or not chat_id:
        return
    try:
        import aiohttp
        url = f"https://api.telegram.org/bot{token}/sendMessage"
        payload = {"chat_id": chat_id, "text": message, "parse_mode": "HTML"}
        async with aiohttp.ClientSession() as s:
            async with s.post(url, json=payload, timeout=aiohttp.ClientTimeout(total=10)) as r:
                if r.status != 200:
                    logging.warning("Telegram alert failed: HTTP %d", r.status)
    except Exception as exc:
        logging.warning("Telegram alert error: %s", exc)


# ---------------------------------------------------------------------------
# Output formatters
# ---------------------------------------------------------------------------

ICONS = {OK: "OK", WARNING: "WARN", CRITICAL: "CRIT"}
ICONS_COLOR = {
    OK:       "\033[92m[OK]  \033[0m",
    WARNING:  "\033[93m[WARN]\033[0m",
    CRITICAL: "\033[91m[CRIT]\033[0m",
}


def print_report(report: HealthReport, quiet: bool) -> None:
    if quiet and report.overall_status == OK:
        return

    status_color = {0: "\033[92m", 1: "\033[93m", 2: "\033[91m"}
    reset = "\033[0m"
    bold  = "\033[1m"

    sc = status_color[report.overall_status]
    print(f"\n{bold}NEXUS ALPHA Health Check — {report.timestamp}{reset}")
    print(f"Overall Status: {sc}{bold}{report.status_name}{reset}\n")

    for c in report.checks:
        icon = ICONS_COLOR[c.status]
        lat  = f" ({c.latency_ms:.0f}ms)" if c.latency_ms else ""
        print(f"  {icon} {c.name:<25} {c.message}{lat}")
        if c.detail:
            print(f"            {c.detail}")

    print()


def json_report(report: HealthReport) -> str:
    return json.dumps({
        "timestamp":      report.timestamp,
        "overall_status": report.overall_status,
        "status_name":    report.status_name,
        "checks": [
            {
                "name":       c.name,
                "status":     c.status,
                "status_name": ICONS[c.status],
                "message":    c.message,
                "detail":     c.detail,
                "latency_ms": c.latency_ms,
            }
            for c in report.checks
        ],
    }, indent=2)


# ---------------------------------------------------------------------------
# Main
# ---------------------------------------------------------------------------

async def main(quiet: bool, output_json: bool) -> int:
    from dotenv import load_dotenv
    load_dotenv()

    logging.basicConfig(
        level=logging.WARNING,
        format="%(asctime)s [%(levelname)s] %(message)s",
    )

    sb_url = os.getenv("SUPABASE_URL", "")
    sb_key = os.getenv("SUPABASE_SERVICE_KEY", "")

    check_fns = [
        check_supabase(sb_url, sb_key)     if sb_url else None,
        check_binance_rest(),
        check_binance_ws(),
        check_candle_freshness(sb_url, sb_key) if sb_url else None,
        check_bot_process(),
        check_memory_usage(),
        check_disk_usage(),
        check_oanda(),
        check_risk_events(sb_url, sb_key)  if sb_url else None,
        check_open_positions(sb_url, sb_key) if sb_url else None,
    ]
    check_fns = [c for c in check_fns if c is not None]

    results = await asyncio.gather(*check_fns, return_exceptions=True)

    checks: List[CheckResult] = []
    for r in results:
        if isinstance(r, CheckResult):
            checks.append(r)
        elif isinstance(r, Exception):
            checks.append(CheckResult("Unknown", CRITICAL, str(r)))

    overall = max((c.status for c in checks), default=OK)

    report = HealthReport(
        timestamp=datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ"),
        overall_status=overall,
        checks=checks,
    )

    if output_json:
        print(json_report(report))
    else:
        print_report(report, quiet)

    # Send Telegram alert for critical/warning
    if overall >= WARNING:
        failed = [c for c in checks if c.status >= WARNING]
        msg_lines = [f"<b>NEXUS ALPHA Health Alert — {report.status_name}</b>"]
        for c in failed:
            icon = "WARNING" if c.status == WARNING else "CRITICAL"
            msg_lines.append(f"{icon}: {c.name} — {c.message}")
        await send_telegram_alert("\n".join(msg_lines))

    return overall


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description="NEXUS ALPHA Health Check")
    parser.add_argument("--quiet", action="store_true",
                        help="Only output if there is a failure")
    parser.add_argument("--json",  action="store_true",
                        help="Output as JSON (for monitoring integrations)")
    args = parser.parse_args()

    exit_code = asyncio.run(main(args.quiet, args.json))
    sys.exit(exit_code)
