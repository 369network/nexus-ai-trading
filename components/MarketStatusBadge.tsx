'use client';

/**
 * dashboard/components/MarketStatusBadge.tsx
 * -------------------------------------------
 * Shows whether a market is currently open or closed.
 *
 * Market hours (all times UTC unless noted):
 *
 *  Crypto       — always open (24/7)
 *  Forex        — Sunday 22:00 – Friday 22:00 UTC, broken into sessions:
 *                   Sydney   22:00–07:00  Tokyo  00:00–09:00
 *                   London   08:00–17:00  New York 13:00–22:00
 *  Indian Stocks — 03:45–10:00 UTC  (09:15–15:30 IST) Mon–Fri
 *  US Stocks     — 14:30–21:00 UTC  (09:30–16:00 ET)  Mon–Fri
 *                   Pre-market 09:00–14:30 UTC / After-hours 21:00–01:00 UTC
 *  Commodities   — NYMEX: Mon 23:00 – Fri 22:00 UTC  (near-24h weekdays)
 */

import React, { useEffect, useState } from 'react';
import type { Market, MarketStatusState, MarketStatusInfo } from '../lib/types';

// ---------------------------------------------------------------------------
// Types
// ---------------------------------------------------------------------------

interface MarketStatusBadgeProps {
  /** Which market to display status for */
  market: Market;
  /** When true renders a compact single-line badge (used in Navbar) */
  compact?: boolean;
  /** Refresh interval in ms; defaults to 60 000 (1 min) */
  refreshMs?: number;
}

// ---------------------------------------------------------------------------
// Market status computation
// ---------------------------------------------------------------------------

/**
 * Returns the current UTC hour and minute as a decimal fraction of the day.
 * e.g. 09:30 UTC → 9.5
 */
function utcHour(): number {
  const now = new Date();
  return now.getUTCHours() + now.getUTCMinutes() / 60;
}

/** 0 = Sunday … 6 = Saturday */
function utcDayOfWeek(): number {
  return new Date().getUTCDay();
}

function isBetween(h: number, from: number, to: number): boolean {
  if (from <= to) return h >= from && h < to;
  // Wraps midnight e.g. from=22 to=7 (Sydney session)
  return h >= from || h < to;
}

// --- Per-market logic ---

function getCryptoStatus(): MarketStatusInfo {
  return {
    market: 'crypto',
    status: 'open',
    label: 'Crypto',
    opens_at: null,
    closes_at: null,
    session: '24/7',
  };
}

function getForexStatus(): MarketStatusInfo {
  const dow = utcDayOfWeek();
  const h = utcHour();

  // Forex is closed Saturday all day and Sunday before 22:00 UTC
  const isWeekend =
    dow === 6 || // Saturday
    (dow === 0 && h < 22); // Sunday before market open

  if (isWeekend) {
    // Next open: Sunday 22:00 UTC
    return {
      market: 'forex',
      status: 'closed',
      label: 'Forex',
      opens_at: nextSundayAt(22),
      closes_at: null,
      session: null,
    };
  }

  // Determine active session(s)
  const sessions: string[] = [];
  if (isBetween(h, 22, 7))  sessions.push('Sydney');
  if (isBetween(h, 0, 9))   sessions.push('Tokyo');
  if (isBetween(h, 8, 17))  sessions.push('London');
  if (isBetween(h, 13, 22)) sessions.push('New York');

  return {
    market: 'forex',
    status: 'open',
    label: 'Forex',
    opens_at: null,
    closes_at: null,
    session: sessions.join(' / ') || 'Interbank',
  };
}

function getIndianStocksStatus(): MarketStatusInfo {
  const dow = utcDayOfWeek();
  const h = utcHour();

  // NSE: 09:15–15:30 IST = 03:45–10:00 UTC  (IST = UTC+5:30)
  const OPEN_UTC  = 3 + 45 / 60;   // 03:45 UTC
  const CLOSE_UTC = 10;             // 10:00 UTC

  const isWeekday = dow >= 1 && dow <= 5;
  const isOpen = isWeekday && h >= OPEN_UTC && h < CLOSE_UTC;

  // Pre-market: 09:00–09:15 IST = 03:30–03:45 UTC
  const isPre = isWeekday && h >= (3 + 30 / 60) && h < OPEN_UTC;

  return {
    market: 'indian_stocks',
    status: isOpen ? 'open' : isPre ? 'pre_market' : 'closed',
    label: 'India NSE',
    opens_at: isOpen || isPre ? null : nextWeekdayAt(OPEN_UTC),
    closes_at: isOpen ? todayAt(CLOSE_UTC) : null,
    session: 'NSE / BSE',
  };
}

function getUSStocksStatus(): MarketStatusInfo {
  const dow = utcDayOfWeek();
  const h = utcHour();

  // NYSE/NASDAQ: 09:30–16:00 ET
  // ET = UTC-4 (EDT) or UTC-5 (EST). Using rough EDT:
  const PRE_OPEN_UTC  = 9;          // 09:00 UTC (~05:00 ET) – pre-market starts
  const OPEN_UTC      = 14.5;       // 14:30 UTC (09:30 ET)
  const CLOSE_UTC     = 21;         // 21:00 UTC (16:00 ET)
  const AFTER_CLOSE   = 25;         // after-hours ends 01:00 UTC next day; use 25h to avoid midnight wrap

  const isWeekday = dow >= 1 && dow <= 5;
  const isOpen = isWeekday && h >= OPEN_UTC && h < CLOSE_UTC;
  const isPre = isWeekday && h >= PRE_OPEN_UTC && h < OPEN_UTC;
  const isAfter = isWeekday && h >= CLOSE_UTC && h < 24;

  let status: MarketStatusState;
  if (isOpen)  status = 'open';
  else if (isPre)  status = 'pre_market';
  else if (isAfter) status = 'after_hours';
  else status = 'closed';

  return {
    market: 'us_stocks',
    status,
    label: 'US NYSE/NASDAQ',
    opens_at: isOpen || isPre ? null : nextWeekdayAt(OPEN_UTC),
    closes_at: isOpen ? todayAt(CLOSE_UTC) : null,
    session: isOpen ? 'Regular' : isPre ? 'Pre-Market' : isAfter ? 'After-Hours' : null,
  };
}

function getCommoditiesStatus(): MarketStatusInfo {
  const dow = utcDayOfWeek();
  const h = utcHour();

  // NYMEX / CME: Sunday 23:00 – Friday 22:00 UTC (nearly 24h on weekdays)
  const isOpen =
    (dow === 0 && h >= 23) ||   // Sunday after 23:00
    (dow >= 1 && dow <= 4) ||   // Full Mon–Thu
    (dow === 5 && h < 22);      // Friday before 22:00

  return {
    market: 'commodities',
    status: isOpen ? 'open' : 'closed',
    label: 'NYMEX/CME',
    opens_at: isOpen ? null : nextSundayAt(23),
    closes_at: isOpen ? null : null,
    session: isOpen ? 'NYMEX' : null,
  };
}

// --- UTC time string helpers ---

function todayAt(utcHourFrac: number): string {
  const d = new Date();
  d.setUTCHours(Math.floor(utcHourFrac), Math.round((utcHourFrac % 1) * 60), 0, 0);
  return d.toISOString();
}

function nextWeekdayAt(utcHourFrac: number): string {
  const d = new Date();
  while (d.getUTCDay() === 0 || d.getUTCDay() === 6) {
    d.setUTCDate(d.getUTCDate() + 1);
  }
  d.setUTCHours(Math.floor(utcHourFrac), Math.round((utcHourFrac % 1) * 60), 0, 0);
  return d.toISOString();
}

function nextSundayAt(utcHour: number): string {
  const d = new Date();
  const daysUntilSunday = (7 - d.getUTCDay()) % 7 || 7;
  d.setUTCDate(d.getUTCDate() + daysUntilSunday);
  d.setUTCHours(utcHour, 0, 0, 0);
  return d.toISOString();
}

// --- Dispatcher ---

function getMarketStatus(market: Market): MarketStatusInfo {
  switch (market) {
    case 'crypto':        return getCryptoStatus();
    case 'forex':         return getForexStatus();
    case 'indian_stocks': return getIndianStocksStatus();
    case 'us_stocks':     return getUSStocksStatus();
    case 'commodities':   return getCommoditiesStatus();
  }
}

// ---------------------------------------------------------------------------
// Styling helpers
// ---------------------------------------------------------------------------

const STATUS_DOT: Record<MarketStatusState, string> = {
  open:        'bg-emerald-400',
  closed:      'bg-red-500',
  pre_market:  'bg-yellow-400',
  after_hours: 'bg-orange-400',
};

const STATUS_TEXT: Record<MarketStatusState, string> = {
  open:        'Open',
  closed:      'Closed',
  pre_market:  'Pre',
  after_hours: 'After',
};

const STATUS_COLOR: Record<MarketStatusState, string> = {
  open:        'text-emerald-400',
  closed:      'text-red-400',
  pre_market:  'text-yellow-400',
  after_hours: 'text-orange-400',
};

// ---------------------------------------------------------------------------
// Component
// ---------------------------------------------------------------------------

export default function MarketStatusBadge({
  market,
  compact = false,
  refreshMs = 60_000,
}: MarketStatusBadgeProps) {
  const [info, setInfo] = useState<MarketStatusInfo>(() => getMarketStatus(market));

  useEffect(() => {
    setInfo(getMarketStatus(market));
    const id = setInterval(() => setInfo(getMarketStatus(market)), refreshMs);
    return () => clearInterval(id);
  }, [market, refreshMs]);

  if (compact) {
    // Inline row used in the Navbar sidebar
    return (
      <div className="flex items-center gap-2">
        <span
          className={`h-2 w-2 rounded-full flex-shrink-0 ${STATUS_DOT[info.status]}`}
          aria-hidden="true"
        />
        <span className="text-xs text-gray-400 flex-1 truncate">{info.label}</span>
        <span className={`text-xs font-medium ${STATUS_COLOR[info.status]}`}>
          {STATUS_TEXT[info.status]}
        </span>
      </div>
    );
  }

  // Full-size badge (used on market-specific pages)
  return (
    <div
      className="inline-flex items-center gap-2 rounded-full border border-gray-700 bg-gray-800 px-3 py-1"
      title={info.session ?? undefined}
    >
      <span
        className={`h-2.5 w-2.5 rounded-full animate-pulse flex-shrink-0 ${STATUS_DOT[info.status]}`}
        aria-hidden="true"
      />
      <span className="text-sm font-medium text-gray-200">{info.label}</span>
      <span className={`text-sm font-semibold ${STATUS_COLOR[info.status]}`}>
        {STATUS_TEXT[info.status]}
      </span>
      {info.session && (
        <span className="text-xs text-gray-500">· {info.session}</span>
      )}
    </div>
  );
}
