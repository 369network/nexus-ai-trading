#!/usr/bin/env python3
"""
NEXUS ALPHA - Paper Trading Launcher
Forces PAPER_MODE=true and starts the bot with a pre-flight checklist.

Usage:
    python scripts/run_paper.py
    python scripts/run_paper.py --log-level DEBUG
"""

from __future__ import annotations

import asyncio
import logging
import os
import sys
import time
from typing import Dict, List, Tuple

# Force paper mode before any other imports
os.environ["PAPER_MODE"] = "true"

# Ensure project root is on path
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))


# ANSI colour codes for checklist output
GREEN  = "\033[92m"
RED    = "\033[91m"
YELLOW = "\033[93m"
RESET  = "\033[0m"
BOLD   = "\033[1m"


def ok(msg: str) -> str:
    return f"{GREEN}[OK]{RESET}  {msg}"


def fail(msg: str) -> str:
    return f"{RED}[FAIL]{RESET} {msg}"


def warn(msg: str) -> str:
    return f"{YELLOW}[WARN]{RESET} {msg}"


# ---------------------------------------------------------------------------
# Pre-flight checks
# ---------------------------------------------------------------------------

async def check_supabase() -> Tuple[bool, str]:
    try:
        from supabase import create_client  # type: ignore
        url = os.getenv("SUPABASE_URL", "")
        key = os.getenv("SUPABASE_SERVICE_KEY") or os.getenv("SUPABASE_ANON_KEY", "")
        if not url or not key:
            return False, "SUPABASE_URL or SUPABASE_ANON_KEY not set"
        sb = create_client(url, key)
        result = sb.table("feature_flags").select("flag_name").limit(1).execute()
        return True, f"Connected — {len(result.data)} feature flags"
    except Exception as exc:
        return False, str(exc)


async def check_binance() -> Tuple[bool, str]:
    try:
        import aiohttp
        async with aiohttp.ClientSession() as session:
            async with session.get("https://api.binance.com/api/v3/ping", timeout=aiohttp.ClientTimeout(total=10)) as r:
                if r.status == 200:
                    return True, "Binance REST reachable"
                return False, f"HTTP {r.status}"
    except Exception as exc:
        return False, str(exc)


async def check_oanda() -> Tuple[bool, str]:
    token = os.getenv("OANDA_ACCESS_TOKEN", "")
    if not token:
        return False, "OANDA_ACCESS_TOKEN not configured (skipped)"
    try:
        import aiohttp
        headers = {"Authorization": f"Bearer {token}"}
        url = "https://api-fxpractice.oanda.com/v3/accounts"
        async with aiohttp.ClientSession(headers=headers) as session:
            async with session.get(url, timeout=aiohttp.ClientTimeout(total=10)) as r:
                if r.status == 200:
                    return True, "OANDA practice connected"
                return False, f"HTTP {r.status}"
    except Exception as exc:
        return False, str(exc)


async def check_llm() -> Tuple[bool, str]:
    key = os.getenv("OPENAI_API_KEY", "") or os.getenv("ANTHROPIC_API_KEY", "")
    if not key:
        return False, "No LLM API key configured (OPENAI_API_KEY or ANTHROPIC_API_KEY)"
    return True, "LLM API key present"


async def check_env_file() -> Tuple[bool, str]:
    env_path = os.path.join(
        os.path.dirname(os.path.dirname(os.path.abspath(__file__))), ".env"
    )
    if os.path.exists(env_path):
        return True, f".env file found at {env_path}"
    return False, f".env file NOT found at {env_path}"


async def check_paper_mode() -> Tuple[bool, str]:
    mode = os.getenv("PAPER_MODE", "false").lower()
    if mode == "true":
        return True, "PAPER_MODE=true (forced by run_paper.py)"
    return False, f"PAPER_MODE={mode!r} — unexpected"


def print_checklist(results: List[Tuple[str, bool, str]]) -> bool:
    print()
    print(f"{BOLD}{'=' * 60}{RESET}")
    print(f"{BOLD}  NEXUS ALPHA — Paper Trading Pre-flight Checklist{RESET}")
    print(f"{BOLD}{'=' * 60}{RESET}")

    all_ok = True
    for name, status, detail in results:
        if status:
            line = ok(f"{name:<28} {detail}")
        else:
            line = fail(f"{name:<28} {detail}")
            all_ok = False
        print(f"  {line}")

    print(f"{BOLD}{'=' * 60}{RESET}")
    if all_ok:
        print(f"  {GREEN}{BOLD}All checks passed. Starting paper trading bot…{RESET}")
    else:
        print(f"  {RED}{BOLD}Some checks FAILED. Review above before proceeding.{RESET}")
    print(f"{BOLD}{'=' * 60}{RESET}")
    print()
    return all_ok


async def run_preflight() -> bool:
    from dotenv import load_dotenv
    load_dotenv()

    checks = [
        ("Paper mode",        check_paper_mode),
        ("Environment file",  check_env_file),
        ("Supabase",          check_supabase),
        ("Binance REST",      check_binance),
        ("OANDA practice",    check_oanda),
        ("LLM API key",       check_llm),
    ]

    results = []
    for name, fn in checks:
        try:
            status, detail = await fn()
        except Exception as exc:
            status, detail = False, str(exc)
        results.append((name, status, detail))

    return print_checklist(results)


# ---------------------------------------------------------------------------
# Main
# ---------------------------------------------------------------------------

async def main(log_level: str) -> None:
    logging.basicConfig(
        level=getattr(logging, log_level.upper(), logging.INFO),
        format="%(asctime)s [%(levelname)s] %(name)s - %(message)s",
        datefmt="%Y-%m-%dT%H:%M:%S",
    )

    all_ok = await run_preflight()

    if not all_ok:
        print(f"{YELLOW}Proceeding despite failures (paper mode is safe).{RESET}\n")

    # Import and start the bot
    from src.main import NexusAlpha
    bot = NexusAlpha()
    await bot.start()


if __name__ == "__main__":
    import argparse
    parser = argparse.ArgumentParser(description="Start NEXUS ALPHA in paper trading mode")
    parser.add_argument("--log-level", default="INFO",
                        choices=["DEBUG", "INFO", "WARNING", "ERROR"])
    args = parser.parse_args()

    try:
        asyncio.run(main(args.log_level))
    except KeyboardInterrupt:
        print("\nBot stopped by user.")
        sys.exit(0)
