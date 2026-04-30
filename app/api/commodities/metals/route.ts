/**
 * GET /api/commodities/metals
 * ---------------------------
 * Real precious metals prices with 24h change.
 * Source: Yahoo Finance v8/chart per-symbol (same pattern as /api/prices/indices)
 * Symbols: GC=F (Gold), SI=F (Silver), PL=F (Platinum), PA=F (Palladium)
 */

import { NextResponse } from 'next/server';

export const dynamic = 'force-dynamic';

const YAHOO_BASE = 'https://query1.finance.yahoo.com/v8/finance/chart';

interface MetalData {
  price: number | null;
  change: number | null;
  change_pct: number | null;
  high: number | null;
  low: number | null;
}

async function fetchYahooMetal(symbol: string): Promise<MetalData> {
  const empty: MetalData = { price: null, change: null, change_pct: null, high: null, low: null };
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
    if (!res.ok) return empty;

    const data = await res.json();
    const result = data.chart?.result?.[0];
    if (!result) return empty;

    const meta = result.meta;
    const price: number = meta.regularMarketPrice;
    const prevClose: number = meta.chartPreviousClose ?? meta.previousClose ?? price;
    const change = price - prevClose;
    const change_pct = prevClose !== 0 ? (change / prevClose) * 100 : 0;
    const high: number | null = meta.regularMarketDayHigh ?? null;
    const low: number | null = meta.regularMarketDayLow ?? null;

    return {
      price:      typeof price === 'number' ? price : null,
      change:     typeof change === 'number' ? change : null,
      change_pct: typeof change_pct === 'number' ? change_pct : null,
      high,
      low,
    };
  } catch {
    return empty;
  }
}

export async function GET() {
  const [gold, silver, platinum, palladium] = await Promise.all([
    fetchYahooMetal('GC=F'),
    fetchYahooMetal('SI=F'),
    fetchYahooMetal('PL=F'),
    fetchYahooMetal('PA=F'),
  ]);

  const metals = [
    { name: 'Gold',      symbol: 'XAU', ...gold,      unit: 'oz' },
    { name: 'Silver',    symbol: 'XAG', ...silver,    unit: 'oz' },
    { name: 'Platinum',  symbol: 'XPT', ...platinum,  unit: 'oz' },
    { name: 'Palladium', symbol: 'XPD', ...palladium, unit: 'oz' },
  ];

  // Gold/Silver ratio
  const gsRatio =
    gold.price && silver.price
      ? Math.round((gold.price / silver.price) * 100) / 100
      : null;

  return NextResponse.json({
    metals,
    gold_silver_ratio: gsRatio,
    timestamp: new Date().toISOString(),
  });
}
