/**
 * GET /api/stocks/sectors
 * -----------------------
 * Real US sector performance from Yahoo Finance v8/chart per-symbol.
 * Fetches the 11 SPDR sector ETFs (XLK, XLF, XLV, etc.) for live % changes.
 * Uses per-symbol v8/chart approach (same as /api/prices/indices) which works
 * from Vercel IPs — the v7/quote batch endpoint was returning 401.
 */

import { NextResponse } from 'next/server';

export const dynamic = 'force-dynamic';

const SECTOR_ETFS = [
  { symbol: 'XLK',  name: 'Technology',        weight: 31.2 },
  { symbol: 'XLF',  name: 'Financials',         weight: 12.8 },
  { symbol: 'XLV',  name: 'Health Care',        weight: 12.1 },
  { symbol: 'XLY',  name: 'Consumer Discret.',  weight: 10.5 },
  { symbol: 'XLC',  name: 'Comm. Services',     weight: 8.9  },
  { symbol: 'XLI',  name: 'Industrials',        weight: 8.2  },
  { symbol: 'XLP',  name: 'Consumer Staples',   weight: 5.8  },
  { symbol: 'XLE',  name: 'Energy',             weight: 4.1  },
  { symbol: 'XLRE', name: 'Real Estate',        weight: 2.5  },
  { symbol: 'XLB',  name: 'Materials',          weight: 2.4  },
  { symbol: 'XLU',  name: 'Utilities',          weight: 2.4  },
];

const YAHOO_BASE = 'https://query1.finance.yahoo.com/v8/finance/chart';

async function fetchSector(symbol: string) {
  try {
    const res = await fetch(
      `${YAHOO_BASE}/${encodeURIComponent(symbol)}?interval=1d&range=5d`,
      {
        headers: {
          'User-Agent': 'Mozilla/5.0 (compatible; NEXUS-ALPHA/1.0)',
          'Accept': 'application/json',
        },
        next: { revalidate: 60 },
        signal: AbortSignal.timeout(10000),
      }
    );
    if (!res.ok) return null;

    const data = await res.json();
    const result = data.chart?.result?.[0];
    if (!result) return null;

    const meta = result.meta;
    const price: number = meta.regularMarketPrice;
    const prevClose: number = meta.chartPreviousClose ?? meta.previousClose ?? price;
    const change = price - prevClose;
    const change_pct = prevClose !== 0 ? (change / prevClose) * 100 : 0;

    return {
      price:      typeof price === 'number' ? price : null,
      change:     typeof change === 'number' ? change : null,
      change_pct: typeof change_pct === 'number' ? change_pct : null,
      prev_close: typeof prevClose === 'number' ? prevClose : null,
    };
  } catch {
    return null;
  }
}

export async function GET() {
  // Fetch all 11 ETFs in parallel
  const results = await Promise.all(SECTOR_ETFS.map((etf) => fetchSector(etf.symbol)));

  const sectors = SECTOR_ETFS.map((etf, i) => ({
    symbol:     etf.symbol,
    name:       etf.name,
    weight:     etf.weight,
    price:      results[i]?.price      ?? null,
    change:     results[i]?.change     ?? null,
    change_pct: results[i]?.change_pct ?? null,
    prev_close: results[i]?.prev_close ?? null,
  }));

  return NextResponse.json({ sectors, timestamp: new Date().toISOString() });
}
