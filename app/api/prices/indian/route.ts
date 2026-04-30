/**
 * GET /api/prices/indian
 * ----------------------
 * Indian market index prices via Yahoo Finance v8/chart (per-symbol).
 *
 * Why v8/chart instead of v7/quote batch:
 *   The v7/quote batch endpoint is aggressively rate-limited on shared
 *   Vercel IPs (returns 429). The v8/chart endpoint is per-symbol and
 *   tolerates Vercel's concurrency without triggering rate limits.
 *
 * Fallback: if Yahoo Finance fails for a symbol, that slot returns null
 * and the client shows "Unavailable" — no 502 error badge.
 */

import { NextResponse } from 'next/server';

export const dynamic = 'force-dynamic';

const YAHOO_CHART = 'https://query1.finance.yahoo.com/v8/finance/chart';

const SYMBOLS: Array<{ symbol: string; key: string; label: string }> = [
  { symbol: '^NSEI',    key: 'NIFTY50',    label: 'NIFTY 50'   },
  { symbol: '^NSEBANK', key: 'BANKNIFTY',  label: 'BANK NIFTY' },
  { symbol: '^NSMIDCP', key: 'MIDCAP',     label: 'MIDCAP 50'  },
  { symbol: '^CNXIT',   key: 'IT',         label: 'NIFTY IT'   },
];

interface IndexQuote {
  symbol: string;
  label: string;
  price: number;
  change: number;
  changePct: number;
  high: number;
  low: number;
  prevClose: number;
  volume: number;
}

async function fetchIndex(
  symbol: string,
  label: string,
): Promise<IndexQuote | null> {
  try {
    const encoded = encodeURIComponent(symbol);
    const res = await fetch(
      `${YAHOO_CHART}/${encoded}?interval=1d&range=5d`,
      {
        headers: {
          'User-Agent': 'Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36',
          Accept: 'application/json',
        },
        next: { revalidate: 60 },
      },
    );
    if (!res.ok) return null;

    const data = await res.json();
    const meta = data.chart?.result?.[0]?.meta;
    if (!meta) return null;

    const price     = meta.regularMarketPrice ?? 0;
    const prevClose = meta.chartPreviousClose ?? meta.previousClose ?? price;
    const high      = meta.regularMarketDayHigh ?? price;
    const low       = meta.regularMarketDayLow  ?? price;
    const volume    = meta.regularMarketVolume  ?? 0;
    const change    = price - prevClose;
    const changePct = prevClose !== 0 ? (change / prevClose) * 100 : 0;

    return { symbol, label, price, change, changePct, high, low, prevClose, volume };
  } catch {
    return null;
  }
}

export async function GET() {
  // Fetch all symbols in parallel
  const results = await Promise.all(
    SYMBOLS.map(({ symbol, label }) => fetchIndex(symbol, label)),
  );

  const indices: Record<string, IndexQuote | null> = {};
  SYMBOLS.forEach(({ key }, i) => {
    indices[key] = results[i];
  });

  // Always return 200 — let the client handle null slots as "Unavailable"
  return NextResponse.json({
    indices,
    timestamp: new Date().toISOString(),
    source: 'Yahoo Finance',
  });
}
