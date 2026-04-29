import { NextResponse } from 'next/server';

export const dynamic = 'force-dynamic';

// Indian market indices via Yahoo Finance (server-side, avoids CORS)
const SYMBOLS: Record<string, string> = {
  '^NSEI':    'NIFTY50',
  '^NSEBANK': 'BANKNIFTY',
  '^NSMIDCP': 'MIDCAP',
  '^CNXIT':   'IT',
};

interface YFQuote {
  symbol: string;
  regularMarketPrice?: number;
  regularMarketChange?: number;
  regularMarketChangePercent?: number;
  regularMarketDayHigh?: number;
  regularMarketDayLow?: number;
  regularMarketPreviousClose?: number;
  regularMarketVolume?: number;
}

export async function GET() {
  try {
    const symbolList = Object.keys(SYMBOLS).join(',');
    const url = `https://query1.finance.yahoo.com/v7/finance/quote?symbols=${encodeURIComponent(symbolList)}&fields=regularMarketPrice,regularMarketChange,regularMarketChangePercent,regularMarketDayHigh,regularMarketDayLow,regularMarketPreviousClose,regularMarketVolume`;

    const res = await fetch(url, {
      headers: {
        'User-Agent': 'Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36',
        'Accept': 'application/json',
      },
      next: { revalidate: 60 }, // Cache for 60s in Next.js data cache
    });

    if (!res.ok) {
      throw new Error(`Yahoo Finance returned ${res.status}`);
    }

    const data = await res.json();
    const quotes: YFQuote[] = data?.quoteResponse?.result ?? [];

    const indices: Record<string, {
      symbol: string;
      label: string;
      price: number;
      change: number;
      changePct: number;
      high: number;
      low: number;
      prevClose: number;
      volume: number;
    }> = {};

    for (const q of quotes) {
      const key = SYMBOLS[q.symbol];
      if (!key) continue;
      indices[key] = {
        symbol:    q.symbol,
        label:     getLabel(key),
        price:     q.regularMarketPrice ?? 0,
        change:    q.regularMarketChange ?? 0,
        changePct: q.regularMarketChangePercent ?? 0,
        high:      q.regularMarketDayHigh ?? 0,
        low:       q.regularMarketDayLow ?? 0,
        prevClose: q.regularMarketPreviousClose ?? 0,
        volume:    q.regularMarketVolume ?? 0,
      };
    }

    return NextResponse.json({
      indices,
      timestamp: new Date().toISOString(),
      source: 'Yahoo Finance',
    });
  } catch (err) {
    console.error('[indian-prices] fetch error:', err);
    return NextResponse.json({ error: 'Failed to fetch Indian market data', indices: {} }, { status: 502 });
  }
}

function getLabel(key: string): string {
  switch (key) {
    case 'NIFTY50':   return 'NIFTY 50';
    case 'BANKNIFTY': return 'BANK NIFTY';
    case 'MIDCAP':    return 'MIDCAP 50';
    case 'IT':        return 'NIFTY IT';
    default:          return key;
  }
}
