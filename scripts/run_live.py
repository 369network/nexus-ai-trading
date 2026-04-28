#!/usr/bin/env python3
"""
NEXUS ALPHA - Live Trading Launcher
Verifies configuration, shows risk summary, and requires explicit confirmation.

Usage:
    python scripts/run_live.py
"""

from __future__ import annotations

import asyncio
import logging
import os
import sys
import time
from typing import Dict, List, Tuple

# Ensure project root is on path
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

# ANSI codes
GREEN  = "\033[92m"
RED    = "\033[91m"
YELLOW = "\033[93m"
CYAN   = "\033[96m"
RESET  = "\033[0m"
BOLD   = "\033[1m"

CONFIRM_PHRASE = "CONFIRM LIVE TRADING"


# ---------------------------------------------------------------------------
# Pre-flight checks — more stringent than paper mode
# ---------------------------------------------------------------------------

def _load_env() -> None:
    from dotenv import load_dotenv
    load_dotenv()


async def check_paper_mode_is_false() -> Tuple[bool, str]:
    mode = os.getenv("PAPER_MODE", "true").lower()
    if mode == "false":
        return True, "PAPER_MODE=false confirmed"
    return False, f"PAPER_MODE={mode!r} — must be 'false' for live trading. Refusing to start."


async def check_supabase_live() -> Tuple[bool, str]:
    try:
        from supabase import create_client  # type: ignore
        url = os.getenv("SUPABASE_URL", "")
        key = os.getenv("SUPABASE_SERVICE_KEY", "")
        if not url or not key:
            return False, "SUPABASE_URL or SUPABASE_SERVICE_KEY not set"
        sb = create_client(url, key)
        result = sb.table("system_config").select("key,value").eq("key", "paper_mode").execute()
        if result.data and result.data[0]["value"].lower() == "true":
            return False, "system_config.paper_mode=true — update to 'false' via dashboard"
        return True, "Supabase connected, paper_mode config is false"
    except Exception as exc:
        return False, str(exc)


async def check_binance_live() -> Tuple[bool, str]:
    api_key = os.getenv("BINANCE_API_KEY", "")
    secret = os.getenv("BINANCE_SECRET", "")
    if not api_key or not secret:
        return False, "BINANCE_API_KEY or BINANCE_SECRET not set"
    try:
        import aiohttp, hashlib, hmac, time as t

        ts = int(t.time() * 1000)
        query = f"timestamp={ts}"
        sig = hmac.new(secret.encode(), query.encode(), hashlib.sha256).hexdigest()
        url = f"https://api.binance.com/api/v3/account?{query}&signature={sig}"
        headers = {"X-MBX-APIKEY": api_key}

        async with aiohttp.ClientSession() as session:
            async with session.get(url, headers=headers, timeout=aiohttp.ClientTimeout(total=15)) as r:
                if r.status == 200:
                    data = await r.json()
                    can_trade = data.get("canTrade", False)
                    balance = next(
                        (float(b["free"]) for b in data.get("balances", []) if b["asset"] == "USDT"), 0
                    )
                    if not can_trade:
                        return False, "Binance account: canTrade=False — check API permissions"
                    return True, f"Binance LIVE connected, USDT balance: {balance:,.2f}"
                body = await r.text()
                return False, f"Binance API error {r.status}: {body[:100]}"
    except Exception as exc:
        return False, str(exc)


async def check_not_testnet() -> Tuple[bool, str]:
    binance_url = os.getenv("BINANCE_BASE_URL", "https://api.binance.com")
    oanda_practice = os.getenv("OANDA_PRACTICE", "true").lower()
    alpaca_paper = os.getenv("ALPACA_PAPER", "true").lower()

    issues = []
    if "testnet" in binance_url.lower():
        issues.append(f"BINANCE_BASE_URL points to testnet ({binance_url})")
    if oanda_practice == "true":
        issues.append("OANDA_PRACTICE=true (practice mode — update if using Forex)")
    if alpaca_paper == "true":
        issues.append("ALPACA_PAPER=true (paper mode — update if using US Stocks)")

    if issues:
        return False, "; ".join(issues)
    return True, "No testnet/practice endpoints detected"


async def check_risk_settings() -> Tuple[bool, str]:
    daily_loss = float(os.getenv("DAILY_LOSS_LIMIT_PCT", "3.0"))
    max_pos = float(os.getenv("MAX_POSITION_SIZE_PCT", "10.0"))
    drawdown_stop = float(os.getenv("DRAWDOWN_STOP_PCT", "25.0"))

    issues = []
    if daily_loss > 5.0:
        issues.append(f"DAILY_LOSS_LIMIT_PCT={daily_loss}% is high (>5%)")
    if max_pos > 20.0:
        issues.append(f"MAX_POSITION_SIZE_PCT={max_pos}% is high (>20%)")
    if drawdown_stop > 40.0:
        issues.append(f"DRAWDOWN_STOP_PCT={drawdown_stop}% is very high (>40%)")

    if issues:
        return False, "; ".join(issues)

    return True, (
        f"daily_loss={daily_loss}%, max_pos={max_pos}%, "
        f"dd_stop={drawdown_stop}%"
    )


async def check_telegram_alerts() -> Tuple[bool, str]:
    token = os.getenv("TELEGRAM_BOT_TOKEN", "")
    chat_id = os.getenv("TELEGRAM_CHAT_ID", "")
    if not token or not chat_id:
        return False, "TELEGRAM_BOT_TOKEN or TELEGRAM_CHAT_ID not set — alerts disabled"
    try:
        import aiohttp
        url = f"https://api.telegram.org/bot{token}/getMe"
        async with aiohttp.ClientSession() as session:
            async with session.get(url, timeout=aiohttp.ClientTimeout(total=10)) as r:
                if r.status == 200:
                    data = await r.json()
                    return True, f"Telegram bot: @{data['result']['username']}"
                return False, f"Telegram API error {r.status}"
    except Exception as exc:
        return False, str(exc)


def display_preflight(results: List[Tuple[str, bool, str]]) -> bool:
    print()
    print(f"{BOLD}{'=' * 65}{RESET}")
    print(f"{RED}{BOLD}  NEXUS ALPHA — LIVE TRADING PRE-FLIGHT CHECK{RESET}")
    print(f"{BOLD}{'=' * 65}{RESET}")

    all_critical_ok = True
    for name, status, detail in results:
        icon = f"{GREEN}[OK]  {RESET}" if status else f"{RED}[FAIL]{RESET}"
        print(f"  {icon} {name:<32} {detail}")
        if not status:
            all_critical_ok = False

    print(f"{BOLD}{'=' * 65}{RESET}")
    return all_critical_ok


def display_risk_summary() -> None:
    daily_loss  = os.getenv("DAILY_LOSS_LIMIT_PCT", "3.0")
    weekly_loss = os.getenv("WEEKLY_LOSS_LIMIT_PCT", "8.0")
    dd_pause    = os.getenv("DRAWDOWN_PAUSE_PCT", "15.0")
    dd_stop     = os.getenv("DRAWDOWN_STOP_PCT", "25.0")
    max_pos     = os.getenv("MAX_POSITION_SIZE_PCT", "10.0")
    max_open    = os.getenv("MAX_OPEN_POSITIONS", "5")

    print(f"\n{CYAN}{BOLD}  ACTIVE RISK SETTINGS:{RESET}")
    print(f"  {'Daily loss limit:':<32} {RED}{daily_loss}%{RESET}")
    print(f"  {'Weekly loss limit:':<32} {RED}{weekly_loss}%{RESET}")
    print(f"  {'Drawdown PAUSE threshold:':<32} {YELLOW}{dd_pause}%{RESET}")
    print(f"  {'Drawdown STOP threshold:':<32} {RED}{dd_stop}%{RESET}")
    print(f"  {'Max position size:':<32} {YELLOW}{max_pos}%{RESET}")
    print(f"  {'Max open positions:':<32} {YELLOW}{max_open}{RESET}")
    print()


def require_confirmation() -> bool:
    """Prompt user to type the confirmation phrase. Returns True if confirmed."""
    print(f"{RED}{BOLD}  WARNING: You are about to start LIVE trading with REAL MONEY.{RESET}")
    print(f"{RED}{BOLD}  Losses can exceed your initial capital in volatile markets.{RESET}")
    print()
    print(f"  To proceed, type exactly: {BOLD}{CONFIRM_PHRASE}{RESET}")
    print()

    try:
        response = input("  > ").strip()
    except (EOFError, KeyboardInterrupt):
        return False

    if response == CONFIRM_PHRASE:
        print(f"\n  {GREEN}{BOLD}Confirmed. Starting live trading bot…{RESET}\n")
        return True

    print(f"\n  {RED}Incorrect phrase '{response}'. Aborting.{RESET}\n")
    return False


# ---------------------------------------------------------------------------
# Main
# ---------------------------------------------------------------------------

async def main() -> None:
    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s [%(levelname)s] %(name)s - %(message)s",
        datefmt="%Y-%m-%dT%H:%M:%S",
    )

    _load_env()

    checks = [
        ("PAPER_MODE=false",         check_paper_mode_is_false),
        ("Supabase (live config)",   check_supabase_live),
        ("Binance LIVE API",         check_binance_live),
        ("No testnet endpoints",     check_not_testnet),
        ("Risk settings",            check_risk_settings),
        ("Telegram alerts",          check_telegram_alerts),
    ]

    results = []
    for name, fn in checks:
        try:
            status, detail = await fn()
        except Exception as exc:
            status, detail = False, str(exc)
        results.append((name, status, detail))

    all_ok = display_preflight(results)

    if not all_ok:
        print(f"\n{RED}{BOLD}Pre-flight FAILED. Fix the issues above before going live.{RESET}\n")
        sys.exit(1)

    display_risk_summary()

    if not require_confirmation():
        print("Aborted.")
        sys.exit(0)

    from src.main import NexusAlpha
    bot = NexusAlpha()
    await bot.start()


if __name__ == "__main__":
    try:
        asyncio.run(main())
    except KeyboardInterrupt:
        print("\nBot stopped by user.")
        sys.exit(0)
