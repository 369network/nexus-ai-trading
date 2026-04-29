/**
 * GET /api/stocks/sectors
 * -----------------------
 * Real US sector performance from Yahoo Finance.
 * Fetches the 11 SPDR sector ETFs (XLK, XLF, XLV, etc.) for live % changes.
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

interface YahooQuote {
  symbol: string;
  regularMarketPrice: number;
  regularMarketChangePercent: number;
  regularMarketChange: number;
  regularMarketPreviousClose: number;
}

interface YahooQuoteResponse {
  quoteResponse?: {
    result: YahooQuote[];
    error: null | string;
  };
}

async function fetchYahooQuotes(symbols: string[]): Promise<YahooQuote[]> {
  const joined = symbols.join(',');
  const url =
    `https://query1.finance.yahoo.com/v7/finance/quote` +
    `?symbols=${encodeURIComponent(joined)}` +
    `&fields=regularMarketPrice,regularMarketChangePercent,regularMarketChange,regularMarketPreviousClose`;

  const res = await fetch(url, {
    headers: {
      'User-Agent': 'Mozilla/5.0 (compatible; NEXUS-ALPHA/1.0)',
      Accept: 'application/json',
    },
    next: { revalidate: 60 },
  });

  if (!res.ok) throw new Error(`Yahoo Finance error ${res.status}`);
  const json: YahooQuoteResponse = await res.json();
  return json.quoteResponse?.result ?? [];
}

export async function GET() {
  try {
    const symbols = SECTOR_ETFS.map((s) => s.symbol);
    const quotes = await fetchYahooQuotes(symbols);

    const quoteMap = new Map(quotes.map((q) => [q.symbol, q]));

    const sectors = SECTOR_ETFS.map((etf) => {
      const q = quoteMap.get(etf.symbol);
      return {
        symbol: etf.symbol,
        name: etf.name,
        weight: etf.weight,
        price: q?.regularMarketPrice ?? null,
        change: q?.regularMarketChange ?? null,
        change_pct: q?.regularMarketChangePercent ?? null,
        prev_close: q?.regularMarketPreviousClose ?? null,
      };
    });

    return NextResponse.json({ sectors, timestamp: new Date().toISOString() });
  } catch (err) {
    return NextResponse.json(
      { error: err instanceof Error ? err.message : String(err), sectors: [] },
      { status: 500 },
    );
  }
}
