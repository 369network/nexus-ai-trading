/**
 * GET /api/market/vix
 * -------------------
 * Real VIX and volatility indices from Yahoo Finance v8/chart per-symbol.
 * Same approach as /api/prices/indices — works from Vercel IPs.
 * Symbols: ^VIX, ^VXN, ^VVIX, GVZ, OVX
 */

import { NextResponse } from 'next/server';

export const dynamic = 'force-dynamic';

const YAHOO_BASE = 'https://query1.finance.yahoo.com/v8/finance/chart';

interface VolData {
  value: number | null;
  change: number | null;
  change_pct: number | null;
  high: number | null;
  low: number | null;
}

async function fetchVol(symbol: string): Promise<VolData> {
  const empty: VolData = { value: null, change: null, change_pct: null, high: null, low: null };
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

    return {
      value:      typeof price === 'number' ? price : null,
      change:     typeof change === 'number' ? change : null,
      change_pct: typeof change_pct === 'number' ? change_pct : null,
      high:       meta.regularMarketDayHigh ?? null,
      low:        meta.regularMarketDayLow ?? null,
    };
  } catch {
    return empty;
  }
}

export async function GET() {
  const [vix, vxn, vvix, gvz, ovx] = await Promise.all([
    fetchVol('^VIX'),
    fetchVol('^VXN'),
    fetchVol('^VVIX'),
    fetchVol('GVZ'),
    fetchVol('OVX'),
  ]);

  // VIX regime classification
  const regime = vix.value == null
    ? null
    : vix.value < 15
    ? 'Low'
    : vix.value < 20
    ? 'Normal'
    : vix.value < 30
    ? 'Elevated'
    : 'Extreme';

  return NextResponse.json({
    vix: vix.value != null
      ? { ...vix, regime }
      : null,
    vxn: vxn.value != null ? { value: vxn.value, change_pct: vxn.change_pct } : null,
    vvix: vvix.value != null ? { value: vvix.value, change_pct: vvix.change_pct } : null,
    gvz: gvz.value != null ? { value: gvz.value, change_pct: gvz.change_pct } : null,
    ovx: ovx.value != null ? { value: ovx.value, change_pct: ovx.change_pct } : null,
    timestamp: new Date().toISOString(),
  });
}
