/**
 * GET /api/commodities/metals
 * ---------------------------
 * Real precious metals prices WITH 24h % change.
 * Primary: metals.live API for spot prices
 * Change %: Yahoo Finance GC=F / SI=F / PL=F / PA=F futures for daily change
 */

import { NextResponse } from 'next/server';

export const dynamic = 'force-dynamic';

interface MetalSpot {
  gold: number;
  silver: number;
  platinum: number;
  palladium: number;
}

interface YahooQuote {
  symbol: string;
  regularMarketPrice: number;
  regularMarketChangePercent: number;
  regularMarketChange: number;
  regularMarketDayHigh: number;
  regularMarketDayLow: number;
  regularMarketVolume: number;
}

async function fetchMetalsLive(): Promise<MetalSpot | null> {
  try {
    const res = await fetch('https://api.metals.live/v1/spot/gold,silver,platinum,palladium', {
      next: { revalidate: 60 },
    });
    if (!res.ok) return null;
    const data: Array<Record<string, number>> = await res.json();
    const merged: Record<string, number> = Object.assign({}, ...data);
    return {
      gold: merged.gold ?? 0,
      silver: merged.silver ?? 0,
      platinum: merged.platinum ?? 0,
      palladium: merged.palladium ?? 0,
    };
  } catch {
    return null;
  }
}

async function fetchYahooMetals(): Promise<YahooQuote[]> {
  // GC=F Gold Futures, SI=F Silver Futures, PL=F Platinum, PA=F Palladium
  const symbols = ['GC=F', 'SI=F', 'PL=F', 'PA=F'];
  try {
    const res = await fetch(
      `https://query1.finance.yahoo.com/v7/finance/quote?symbols=${encodeURIComponent(symbols.join(','))}&fields=regularMarketPrice,regularMarketChangePercent,regularMarketChange,regularMarketDayHigh,regularMarketDayLow,regularMarketVolume`,
      {
        headers: {
          'User-Agent': 'Mozilla/5.0 (compatible; NEXUS-ALPHA/1.0)',
          Accept: 'application/json',
        },
        next: { revalidate: 60 },
      },
    );
    if (!res.ok) return [];
    const json = await res.json();
    return json.quoteResponse?.result ?? [];
  } catch {
    return [];
  }
}

export async function GET() {
  const [spotData, yahooQuotes] = await Promise.all([
    fetchMetalsLive(),
    fetchYahooMetals(),
  ]);

  const yahooMap = new Map(yahooQuotes.map((q) => [q.symbol, q]));

  // Merge: use metals.live for spot price (more accurate), Yahoo for % change
  const gc = yahooMap.get('GC=F');
  const si = yahooMap.get('SI=F');
  const pl = yahooMap.get('PL=F');
  const pa = yahooMap.get('PA=F');

  const metals = [
    {
      name: 'Gold',
      symbol: 'XAU',
      price: spotData?.gold ?? gc?.regularMarketPrice ?? null,
      change_pct: gc?.regularMarketChangePercent ?? null,
      change: gc?.regularMarketChange ?? null,
      high: gc?.regularMarketDayHigh ?? null,
      low: gc?.regularMarketDayLow ?? null,
      unit: 'oz',
    },
    {
      name: 'Silver',
      symbol: 'XAG',
      price: spotData?.silver ?? si?.regularMarketPrice ?? null,
      change_pct: si?.regularMarketChangePercent ?? null,
      change: si?.regularMarketChange ?? null,
      high: si?.regularMarketDayHigh ?? null,
      low: si?.regularMarketDayLow ?? null,
      unit: 'oz',
    },
    {
      name: 'Platinum',
      symbol: 'XPT',
      price: spotData?.platinum ?? pl?.regularMarketPrice ?? null,
      change_pct: pl?.regularMarketChangePercent ?? null,
      change: pl?.regularMarketChange ?? null,
      high: pl?.regularMarketDayHigh ?? null,
      low: pl?.regularMarketDayLow ?? null,
      unit: 'oz',
    },
    {
      name: 'Palladium',
      symbol: 'XPD',
      price: spotData?.palladium ?? pa?.regularMarketPrice ?? null,
      change_pct: pa?.regularMarketChangePercent ?? null,
      change: pa?.regularMarketChange ?? null,
      high: pa?.regularMarketDayHigh ?? null,
      low: pa?.regularMarketDayLow ?? null,
      unit: 'oz',
    },
  ];

  // Gold/Silver ratio
  const gsRatio =
    metals[0].price && metals[1].price
      ? Math.round((metals[0].price / metals[1].price) * 100) / 100
      : null;

  return NextResponse.json({
    metals,
    gold_silver_ratio: gsRatio,
    timestamp: new Date().toISOString(),
  });
}
