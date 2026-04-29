/**
 * GET /api/market/vix
 * -------------------
 * Real VIX (CBOE Volatility Index) from Yahoo Finance.
 * Also returns other key volatility/market metrics.
 */

import { NextResponse } from 'next/server';

export const dynamic = 'force-dynamic';

interface YahooQuote {
  symbol: string;
  regularMarketPrice: number;
  regularMarketChangePercent: number;
  regularMarketChange: number;
  regularMarketDayHigh: number;
  regularMarketDayLow: number;
  fiftyTwoWeekHigh: number;
  fiftyTwoWeekLow: number;
  shortName: string;
}

interface YahooResponse {
  quoteResponse?: { result: YahooQuote[]; error: null | string };
}

const SYMBOLS = [
  '^VIX',   // CBOE Volatility Index
  '^VXN',   // NASDAQ Volatility
  '^VVIX',  // VIX of VIX
  'GVZ',    // Gold Volatility
  'OVX',    // Oil Volatility
];

export async function GET() {
  try {
    const res = await fetch(
      `https://query1.finance.yahoo.com/v7/finance/quote?symbols=${encodeURIComponent(SYMBOLS.join(','))}&fields=regularMarketPrice,regularMarketChangePercent,regularMarketChange,regularMarketDayHigh,regularMarketDayLow,fiftyTwoWeekHigh,fiftyTwoWeekLow,shortName`,
      {
        headers: {
          'User-Agent': 'Mozilla/5.0 (compatible; NEXUS-ALPHA/1.0)',
          Accept: 'application/json',
        },
        next: { revalidate: 60 },
      },
    );

    if (!res.ok) {
      return NextResponse.json({ error: `Yahoo Finance error ${res.status}`, vix: null }, { status: res.status });
    }

    const json: YahooResponse = await res.json();
    const quotes = json.quoteResponse?.result ?? [];

    const quoteMap = new Map(quotes.map((q) => [q.symbol, q]));
    const vix = quoteMap.get('^VIX');

    const result = {
      vix: vix
        ? {
            value: vix.regularMarketPrice,
            change: vix.regularMarketChange,
            change_pct: vix.regularMarketChangePercent,
            high: vix.regularMarketDayHigh,
            low: vix.regularMarketDayLow,
            week52_high: vix.fiftyTwoWeekHigh,
            week52_low: vix.fiftyTwoWeekLow,
            // Regime: <15=Low, 15-20=Normal, 20-30=Elevated, >30=Extreme
            regime:
              vix.regularMarketPrice < 15
                ? 'Low'
                : vix.regularMarketPrice < 20
                ? 'Normal'
                : vix.regularMarketPrice < 30
                ? 'Elevated'
                : 'Extreme',
          }
        : null,
      vxn: quoteMap.get('^VXN')
        ? {
            value: quoteMap.get('^VXN')!.regularMarketPrice,
            change_pct: quoteMap.get('^VXN')!.regularMarketChangePercent,
          }
        : null,
      vvix: quoteMap.get('^VVIX')
        ? {
            value: quoteMap.get('^VVIX')!.regularMarketPrice,
            change_pct: quoteMap.get('^VVIX')!.regularMarketChangePercent,
          }
        : null,
      gvz: quoteMap.get('GVZ')
        ? {
            value: quoteMap.get('GVZ')!.regularMarketPrice,
            change_pct: quoteMap.get('GVZ')!.regularMarketChangePercent,
          }
        : null,
      ovx: quoteMap.get('OVX')
        ? {
            value: quoteMap.get('OVX')!.regularMarketPrice,
            change_pct: quoteMap.get('OVX')!.regularMarketChangePercent,
          }
        : null,
      timestamp: new Date().toISOString(),
    };

    return NextResponse.json(result);
  } catch (err) {
    return NextResponse.json(
      { error: err instanceof Error ? err.message : String(err), vix: null },
      { status: 500 },
    );
  }
}
