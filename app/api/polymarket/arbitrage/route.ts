/**
 * GET /api/polymarket/arbitrage
 * ──────────────────────────────
 * Returns real-time arbitrage opportunities by scanning the top 200 Gamma
 * markets live. Opportunities exist when YES + NO combined price < 1 − fee.
 *
 * NOTE: Gamma API returns `outcomePrices`, `outcomes`, and `clobTokenIds`
 *       as JSON-encoded strings — we parse them with parseJsonField().
 */

import { NextResponse } from 'next/server';
import type { ArbOpportunity } from '@/lib/types';

export const dynamic = 'force-dynamic';

const GAMMA_BASE   = 'https://gamma-api.polymarket.com';
const PLATFORM_FEE = 0.02;   // 2% platform fee

/** Gamma API may return array fields as JSON-encoded strings — normalise them. */
// eslint-disable-next-line @typescript-eslint/no-explicit-any
function parseJsonField<T>(v: any, fallback: T): T {
  if (v === null || v === undefined) return fallback;
  if (Array.isArray(v)) return v as unknown as T;
  if (typeof v === 'string') {
    try { return JSON.parse(v) as T; } catch { return fallback; }
  }
  return v as T;
}

/** Safe parseFloat that returns `def` on NaN / null / undefined. */
function safeParse(s: string | undefined | null, def: number): number {
  if (s == null) return def;
  const n = parseFloat(s);
  return isFinite(n) ? n : def;
}

// eslint-disable-next-line @typescript-eslint/no-explicit-any
function parseMarketOpportunity(raw: any): ArbOpportunity | null {
  try {
    const outcomes:  string[] = parseJsonField<string[]>(raw.outcomes,      []);
    const prices:    string[] = parseJsonField<string[]>(raw.outcomePrices, []);

    if (outcomes.length < 2 || prices.length < 2) return null;

    const yesIdx = outcomes.findIndex((o: string) => o.toLowerCase() === 'yes');
    const noIdx  = outcomes.findIndex((o: string) => o.toLowerCase() === 'no');
    const yi = yesIdx >= 0 ? yesIdx : 0;
    const ni = noIdx  >= 0 ? noIdx  : 1;

    const yesPrice = safeParse(prices[yi], -1);
    const noPrice  = safeParse(prices[ni], -1);

    if (yesPrice <= 0 || noPrice <= 0) return null;
    if (yesPrice > 1  || noPrice > 1)  return null;

    const combined        = yesPrice + noPrice;
    const profitPerDollar = 1.0 - combined - PLATFORM_FEE;

    if (profitPerDollar <= 0) return null;

    const vol24       = safeParse(raw.volume24hr ?? raw.volume24h, 0);
    const maxSizeUsdc = Math.min(vol24 * 0.05, 50_000);

    return {
      market_id:         raw.conditionId ?? raw.id ?? '',
      question:          raw.question ?? '',
      type:              'single_condition',
      yes_price:         yesPrice,
      no_price:          noPrice,
      combined_price:    combined,
      profit_per_dollar: profitPerDollar,
      estimated_profit:  profitPerDollar * maxSizeUsdc,
      max_size_usdc:     maxSizeUsdc,
      detected_at:       new Date().toISOString(),
    };
  } catch {
    return null;
  }
}

export async function GET() {
  try {
    const url = new URL(`${GAMMA_BASE}/markets`);
    url.searchParams.set('active',    'true');
    url.searchParams.set('closed',    'false');
    url.searchParams.set('order',     'volume24hr');
    url.searchParams.set('ascending', 'false');
    url.searchParams.set('limit',     '200');

    const res = await fetch(url.toString(), {
      headers: { 'Accept': 'application/json' },
      next: { revalidate: 30 },    // arb is time-sensitive
      signal: AbortSignal.timeout(15000),
    });

    if (!res.ok) {
      return NextResponse.json(
        { opportunities: [], count: 0, scanned: 0, timestamp: new Date().toISOString(), error: `Gamma API ${res.status}` },
        { status: 502 }
      );
    }

    // eslint-disable-next-line @typescript-eslint/no-explicit-any
    const rawBody: any = await res.json();
    const markets: unknown[] = Array.isArray(rawBody) ? rawBody : (rawBody.markets ?? []);

    const opportunities = (markets as Parameters<typeof parseMarketOpportunity>[0][])
      .map(parseMarketOpportunity)
      .filter((o): o is ArbOpportunity => o !== null)
      .sort((a, b) => b.profit_per_dollar - a.profit_per_dollar)
      .slice(0, 10);

    return NextResponse.json({
      opportunities,
      count:     opportunities.length,
      scanned:   markets.length,
      timestamp: new Date().toISOString(),
    });
  } catch (err) {
    console.error('[/api/polymarket/arbitrage]', err);
    return NextResponse.json(
      { opportunities: [], count: 0, scanned: 0, timestamp: new Date().toISOString(), error: 'Internal error' },
      { status: 500 }
    );
  }
}
