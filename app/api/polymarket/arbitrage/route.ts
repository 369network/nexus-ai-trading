import { NextResponse } from 'next/server';
import type { ArbOpportunity } from '@/lib/types';

export const dynamic = 'force-dynamic';

const GAMMA_BASE = 'https://gamma-api.polymarket.com';

// Platform fee assumption (0.02 = 2%)
const PLATFORM_FEE = 0.02;
// Minimum profit after fees to qualify as an opportunity
const MIN_PROFIT_THRESHOLD = 0;

// eslint-disable-next-line @typescript-eslint/no-explicit-any
function parseMarketOpportunity(raw: any): ArbOpportunity | null {
  try {
    const outcomes: string[] = raw.outcomes ?? [];
    const prices:   string[] = raw.outcomePrices ?? [];

    if (outcomes.length < 2 || prices.length < 2) return null;

    const yesIdx = outcomes.findIndex((o: string) => o.toLowerCase() === 'yes');
    const noIdx  = outcomes.findIndex((o: string) => o.toLowerCase() === 'no');
    const yi = yesIdx >= 0 ? yesIdx : 0;
    const ni = noIdx  >= 0 ? noIdx  : 1;

    const yesPrice = parseFloat(prices[yi] ?? '0');
    const noPrice  = parseFloat(prices[ni] ?? '0');

    if (isNaN(yesPrice) || isNaN(noPrice)) return null;
    if (yesPrice <= 0 || noPrice <= 0)     return null;
    // Prices should be in [0,1] for binary markets
    if (yesPrice > 1 || noPrice > 1)       return null;

    const combined = yesPrice + noPrice;

    // Arbitrage exists when combined < 1 - fee buffer
    const profitPerDollar = 1.0 - combined - PLATFORM_FEE;

    if (profitPerDollar <= MIN_PROFIT_THRESHOLD) return null;

    // Estimate max tradeable size from 24h volume
    const vol24 = parseFloat(raw.volume24hr ?? raw.volume24h ?? '0');
    // Conservative: use 5% of 24h volume as max arb size, capped at $50k
    const maxSizeUsdc = Math.min(vol24 * 0.05, 50_000);

    const question = raw.question ?? '';
    const marketId = raw.conditionId ?? raw.id ?? '';

    return {
      market_id:        marketId,
      question,
      type:             'single_condition',
      yes_price:        yesPrice,
      no_price:         noPrice,
      combined_price:   combined,
      profit_per_dollar: profitPerDollar,
      estimated_profit: profitPerDollar * maxSizeUsdc,
      max_size_usdc:    maxSizeUsdc,
      detected_at:      new Date().toISOString(),
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
      // Arbitrage data is time-sensitive — short cache
      next: { revalidate: 30 },
    });

    if (!res.ok) {
      return NextResponse.json(
        {
          opportunities: [],
          count: 0,
          scanned: 0,
          timestamp: new Date().toISOString(),
          error: `Gamma API ${res.status}`,
        },
        { status: 502 }
      );
    }

    // eslint-disable-next-line @typescript-eslint/no-explicit-any
    const raw: any[] = await res.json();
    const markets = Array.isArray(raw) ? raw : [];

    const opportunities = markets
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
      {
        opportunities: [],
        count: 0,
        scanned: 0,
        timestamp: new Date().toISOString(),
        error: 'Internal error',
      },
      { status: 500 }
    );
  }
}
