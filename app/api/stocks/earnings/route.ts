/**
 * GET /api/stocks/earnings
 * ------------------------
 * Real upcoming earnings from Yahoo Finance.
 * Fetches the earnings calendar for the next 7 days.
 */

import { NextResponse } from 'next/server';

export const dynamic = 'force-dynamic';

interface YahooEarning {
  ticker: string;
  companyshortname: string;
  startdatetime: string;
  epsestimate: number | null;
  epsactual: number | null;
  epssurprisepct: number | null;
}

interface YahooEarningsResponse {
  finance?: {
    result?: Array<{
      earnings?: YahooEarning[];
    }>;
  };
}

async function fetchYahooEarnings(): Promise<YahooEarning[]> {
  const today = new Date();
  const end = new Date(today.getTime() + 7 * 86_400_000);
  const fmt = (d: Date) => d.toISOString().split('T')[0];

  const url =
    `https://query1.finance.yahoo.com/v1/finance/earning_calendar` +
    `?startDate=${fmt(today)}&endDate=${fmt(end)}&size=20`;

  const res = await fetch(url, {
    headers: {
      'User-Agent': 'Mozilla/5.0 (compatible; NEXUS-ALPHA/1.0)',
      Accept: 'application/json',
    },
    next: { revalidate: 3600 }, // 1h cache
  });

  if (!res.ok) throw new Error(`Yahoo Finance earnings error ${res.status}`);
  const json: YahooEarningsResponse = await res.json();
  return json.finance?.result?.[0]?.earnings ?? [];
}

// Fetch market caps for top companies via Yahoo quote endpoint
async function fetchMarketCaps(symbols: string[]): Promise<Record<string, number | null>> {
  if (symbols.length === 0) return {};
  try {
    const joined = symbols.join(',');
    const res = await fetch(
      `https://query1.finance.yahoo.com/v7/finance/quote?symbols=${encodeURIComponent(joined)}&fields=marketCap`,
      {
        headers: { 'User-Agent': 'Mozilla/5.0', Accept: 'application/json' },
        next: { revalidate: 3600 },
      },
    );
    if (!res.ok) return {};
    const json = await res.json();
    const result: YahooQuote[] = json.quoteResponse?.result ?? [];
    return Object.fromEntries(result.map((q: YahooQuote) => [q.symbol, q.marketCap ?? null]));
  } catch {
    return {};
  }
}

interface YahooQuote {
  symbol: string;
  marketCap?: number;
}

function formatMarketCap(mc: number | null): string {
  if (!mc) return 'N/A';
  if (mc >= 1e12) return `${(mc / 1e12).toFixed(1)}T`;
  if (mc >= 1e9) return `${(mc / 1e9).toFixed(0)}B`;
  return `${(mc / 1e6).toFixed(0)}M`;
}

export async function GET() {
  try {
    const earnings = await fetchYahooEarnings();

    // Take top 10 by most recognisable (Yahoo returns them sorted by market cap already)
    const top = earnings.slice(0, 10);
    const symbols = top.map((e) => e.ticker);
    const caps = await fetchMarketCaps(symbols);

    const events = top.map((e) => ({
      symbol: e.ticker,
      company: e.companyshortname,
      datetime: e.startdatetime,
      eps_estimate: e.epsestimate,
      eps_actual: e.epsactual,
      eps_surprise_pct: e.epssurprisepct,
      is_released: e.epsactual !== null,
      market_cap: formatMarketCap(caps[e.ticker] ?? null),
    }));

    return NextResponse.json({ events, timestamp: new Date().toISOString() });
  } catch (err) {
    return NextResponse.json(
      { error: err instanceof Error ? err.message : String(err), events: [] },
      { status: 500 },
    );
  }
}
