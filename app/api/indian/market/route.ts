/**
 * GET /api/indian/market
 * ----------------------
 * Real NSE India market data:
 *  - Market breadth (advances, declines, unchanged)
 *  - NIFTY 50 option chain (PCR, Max Pain, top strikes, ATM IV)
 *
 * Uses NSE India public API with proper headers to avoid bot detection.
 * Falls back to graceful empty state on any failure.
 */

import { NextResponse } from 'next/server';

export const dynamic = 'force-dynamic';

const NSE_HEADERS = {
  'User-Agent':
    'Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36',
  Accept: 'application/json, text/plain, */*',
  'Accept-Language': 'en-US,en;q=0.9',
  'Accept-Encoding': 'gzip, deflate, br',
  Referer: 'https://www.nseindia.com/',
  Connection: 'keep-alive',
};

// Establish a session cookie first (NSE requires this)
async function getNseCookie(): Promise<string> {
  try {
    const res = await fetch('https://www.nseindia.com/', {
      headers: NSE_HEADERS,
      redirect: 'follow',
    });
    const cookie = res.headers.get('set-cookie') ?? '';
    // Extract nsit and nseappid cookies
    const parts = cookie.split(',').join(';');
    return parts;
  } catch {
    return '';
  }
}

async function fetchNseBreadth(cookie: string) {
  try {
    const res = await fetch(
      'https://www.nseindia.com/api/equity-stockIndices?index=BROAD%20MARKET%20INDICES',
      {
        headers: { ...NSE_HEADERS, Cookie: cookie },
        next: { revalidate: 300 },
      },
    );
    if (!res.ok) return null;
    const json = await res.json();

    // NSE breadth data is in advance/decline section
    const advance = json.advance ?? {};
    return {
      advances: parseInt(advance.advances ?? '0', 10),
      declines: parseInt(advance.declines ?? '0', 10),
      unchanged: parseInt(advance.unchanged ?? '0', 10),
      high52: parseInt(advance.high52 ?? '0', 10),
      low52: parseInt(advance.low52 ?? '0', 10),
    };
  } catch {
    return null;
  }
}

async function fetchNseOptionChain(cookie: string, symbol = 'NIFTY') {
  try {
    const res = await fetch(
      `https://www.nseindia.com/api/option-chain-indices?symbol=${symbol}`,
      {
        headers: { ...NSE_HEADERS, Cookie: cookie },
        next: { revalidate: 300 },
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

    // Find nearest expiry
    const expiries: string[] = records.expiryDates ?? [];
    const nearExpiry = expiries[0] ?? '';

    const nearData = data.filter((d) => d.expiryDate === nearExpiry);

    // Compute PCR (Put-Call Ratio by OI)
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

    // ATM IV (closest strike to underlying)
    const atmStrike = strikes.reduce((prev, curr) =>
      Math.abs(curr.strike - underlyingValue) < Math.abs(prev.strike - underlyingValue)
        ? curr
        : prev,
    );
    const atmIV = ((atmStrike.callIV + atmStrike.putIV) / 2).toFixed(1);

    // Top 5 call & put strikes by OI
    const topCallStrikes = [...strikes]
      .sort((a, b) => b.callOI - a.callOI)
      .slice(0, 5)
      .map((s) => s.strike);
    const topPutStrikes = [...strikes]
      .sort((a, b) => b.putOI - a.putOI)
      .slice(0, 5)
      .map((s) => s.strike);

    return {
      underlying: underlyingValue,
      expiry: nearExpiry,
      pcr: Math.round(pcr * 100) / 100,
      max_pain: maxPainStrike,
      atm_iv: parseFloat(atmIV),
      total_call_oi: totalCallOI,
      total_put_oi: totalPutOI,
      call_oi_buildup: topCallStrikes,
      put_oi_buildup: topPutStrikes,
    };
  } catch {
    return null;
  }
}

export async function GET() {
  const cookie = await getNseCookie();

  const [breadth, optionChain] = await Promise.all([
    fetchNseBreadth(cookie),
    fetchNseOptionChain(cookie, 'NIFTY'),
  ]);

  return NextResponse.json({
    breadth: breadth ?? null,
    option_chain: optionChain ?? null,
    timestamp: new Date().toISOString(),
  });
}
