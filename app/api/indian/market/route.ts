/**
 * GET /api/indian/market
 * ----------------------
 * Real NSE India market data:
 *  - Market breadth (advances, declines, unchanged) — via NSE allIndices (no cookie)
 *  - NIFTY 50 option chain (PCR, Max Pain, top strikes, ATM IV) — via NSE option-chain API
 *
 * NSE India blocks many cloud-provider IPs. To maximize success:
 *  1. allIndices breadth uses a simpler endpoint that doesn't require session cookies.
 *  2. Option chain still tries the cookie-based approach; gracefully returns null on failure.
 *  3. All failures return null — the client shows "Unavailable" instead of an error badge.
 */

import { NextResponse } from 'next/server';

export const dynamic = 'force-dynamic';

const NSE_HEADERS = {
  'User-Agent':
    'Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/124.0.0.0 Safari/537.36',
  Accept: 'application/json, text/plain, */*',
  'Accept-Language': 'en-US,en;q=0.9',
  Referer: 'https://www.nseindia.com/',
};

// ---------------------------------------------------------------------------
// Breadth — NSE /api/allIndices (no session cookie required)
// Uses NIFTY 500 row which gives market-wide advance/decline/unchanged
// ---------------------------------------------------------------------------

async function fetchNseBreadth() {
  try {
    const res = await fetch('https://www.nseindia.com/api/allIndices', {
      headers: NSE_HEADERS,
      next: { revalidate: 300 },
    });
    if (!res.ok) return null;

    const json = await res.json();
    const rows: Array<Record<string, string | number>> = json.data ?? [];

    // NIFTY 500 is the broadest index — best proxy for overall market breadth
    const nifty500 = rows.find((r) => r.index === 'NIFTY 500');
    if (!nifty500) return null;

    return {
      advances:  parseInt(String(nifty500.advances  ?? '0'), 10),
      declines:  parseInt(String(nifty500.declines   ?? '0'), 10),
      unchanged: parseInt(String(nifty500.unchanged  ?? '0'), 10),
      // 52-week high/low count not available from allIndices — omit
      high52: null as number | null,
      low52:  null as number | null,
    };
  } catch {
    return null;
  }
}

// ---------------------------------------------------------------------------
// Option Chain — requires NSE session cookie; may fail on cloud IPs
// ---------------------------------------------------------------------------

async function getNseCookie(): Promise<string> {
  try {
    const res = await fetch('https://www.nseindia.com/', {
      headers: NSE_HEADERS,
      redirect: 'follow',
      signal: AbortSignal.timeout(5000),
    });
    return res.headers.get('set-cookie') ?? '';
  } catch {
    return '';
  }
}

async function fetchNseOptionChain(symbol = 'NIFTY') {
  let cookie = '';
  try {
    cookie = await getNseCookie();
  } catch {
    return null;
  }

  try {
    const res = await fetch(
      `https://www.nseindia.com/api/option-chain-indices?symbol=${symbol}`,
      {
        headers: { ...NSE_HEADERS, Cookie: cookie },
        next: { revalidate: 300 },
        signal: AbortSignal.timeout(8000),
      },
    );
    if (!res.ok) return null;
    const json = await res.json();
    const records = json.records ?? {};
    const data: Array<{
      strikePrice: number;
      expiryDate: string;
      CE?: { openInterest: number; impliedVolatility: number; lastPrice: number };
      PE?: { openInterest: number; impliedVolatility: number; lastPrice: number };
    }> = records.data ?? [];

    const underlyingValue: number = records.underlyingValue ?? 0;
    const expiries: string[] = records.expiryDates ?? [];
    const nearExpiry = expiries[0] ?? '';
    const nearData = data.filter((d) => d.expiryDate === nearExpiry);

    let totalCallOI = 0;
    let totalPutOI = 0;
    const strikes: Array<{
      strike: number;
      callOI: number;
      putOI: number;
      callIV: number;
      putIV: number;
    }> = [];

    for (const d of nearData) {
      const callOI = d.CE?.openInterest ?? 0;
      const putOI = d.PE?.openInterest ?? 0;
      totalCallOI += callOI;
      totalPutOI += putOI;
      strikes.push({
        strike: d.strikePrice,
        callOI,
        putOI,
        callIV: d.CE?.impliedVolatility ?? 0,
        putIV: d.PE?.impliedVolatility ?? 0,
      });
    }

    if (strikes.length === 0) return null;

    const pcr = totalCallOI > 0 ? totalPutOI / totalCallOI : 0;

    // Max pain: strike where total option pain is minimum
    let maxPainStrike = underlyingValue;
    let minPain = Infinity;
    for (const s of strikes) {
      const callPain = strikes
        .filter((ss) => ss.strike < s.strike)
        .reduce((acc, ss) => acc + ss.callOI * (s.strike - ss.strike), 0);
      const putPain = strikes
        .filter((ss) => ss.strike > s.strike)
        .reduce((acc, ss) => acc + ss.putOI * (ss.strike - s.strike), 0);
      const total = callPain + putPain;
      if (total < minPain) {
        minPain = total;
        maxPainStrike = s.strike;
      }
    }

    // ATM IV: average of call + put IV at strike closest to underlying
    const atmStrike = strikes.reduce((prev, curr) =>
      Math.abs(curr.strike - underlyingValue) < Math.abs(prev.strike - underlyingValue)
        ? curr
        : prev,
    );
    const atmIV = parseFloat(((atmStrike.callIV + atmStrike.putIV) / 2).toFixed(1));

    return {
      underlying: underlyingValue,
      expiry: nearExpiry,
      pcr: Math.round(pcr * 100) / 100,
      max_pain: maxPainStrike,
      atm_iv: atmIV,
      total_call_oi: totalCallOI,
      total_put_oi: totalPutOI,
      call_oi_buildup: [...strikes].sort((a, b) => b.callOI - a.callOI).slice(0, 5).map((s) => s.strike),
      put_oi_buildup:  [...strikes].sort((a, b) => b.putOI  - a.putOI ).slice(0, 5).map((s) => s.strike),
    };
  } catch {
    return null;
  }
}

// ---------------------------------------------------------------------------
// Handler
// ---------------------------------------------------------------------------

export async function GET() {
  const [breadth, optionChain] = await Promise.all([
    fetchNseBreadth(),
    fetchNseOptionChain('NIFTY'),
  ]);

  return NextResponse.json({
    breadth:      breadth      ?? null,
    option_chain: optionChain  ?? null,
    timestamp: new Date().toISOString(),
  });
}
