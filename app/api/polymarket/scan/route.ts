/**
 * POST /api/polymarket/scan
 * ─────────────────────────
 * On-demand Polymarket scan:
 *  1. Fetch top 100 active markets from Gamma API
 *  2. Detect arbitrage opportunities (YES + NO < 0.98)
 *  3. Upsert discovered markets + arb opportunities to Supabase
 *
 * Returns summary of markets found, arb found, rows written.
 */

import { NextResponse } from 'next/server';

export const dynamic = 'force-dynamic';

const GAMMA = 'https://gamma-api.polymarket.com';
const SUPABASE_URL = (process.env.SUPABASE_URL ?? process.env.NEXT_PUBLIC_SUPABASE_URL)!;
const SERVICE_KEY  = (process.env.SUPABASE_SERVICE_KEY ?? process.env.NEXT_PUBLIC_SUPABASE_ANON_KEY)!;

const SB_HEADERS = {
  'Content-Type': 'application/json',
  apikey: SERVICE_KEY,
  Authorization: `Bearer ${SERVICE_KEY}`,
  Prefer: 'resolution=merge-duplicates,return=minimal',
};

// ── Fetch markets from Gamma ──────────────────────────────────────────────────

interface GammaMarket {
  conditionId: string;
  question: string;
  description?: string;
  category?: string;
  marketSlug?: string;
  clobTokenIds?: string[];
  outcomePrices?: string[];
  volume24hr?: number;
  volume?: number;
  openInterest?: number;
  active?: boolean;
  closed?: boolean;
  resolved?: boolean;
  endDate?: string;
}

async function fetchGammaMarkets(): Promise<GammaMarket[]> {
  const params = new URLSearchParams({
    active: 'true',
    closed: 'false',
    order: 'volume24hr',
    ascending: 'false',
    limit: '100',
  });
  const res = await fetch(`${GAMMA}/markets?${params}`, {
    signal: AbortSignal.timeout(15000),
  });
  if (!res.ok) return [];
  const json = await res.json();
  return Array.isArray(json) ? json : (json.markets ?? []);
}

// ── Upsert polymarket_markets ─────────────────────────────────────────────────

async function upsertMarkets(markets: GammaMarket[]) {
  const rows = markets.map((m) => {
    const prices: string[] = m.outcomePrices ?? ['0.5', '0.5'];
    return {
      id:           m.conditionId,
      question:     m.question,
      description:  m.description ?? null,
      category:     m.category ?? null,
      market_slug:  m.marketSlug ?? null,
      yes_token_id: m.clobTokenIds?.[0] ?? '',
      no_token_id:  m.clobTokenIds?.[1] ?? '',
      yes_price:    parseFloat(prices[0] ?? '0.5'),
      no_price:     parseFloat(prices[1] ?? '0.5'),
      volume_24h:   m.volume24hr ?? 0,
      total_volume: m.volume ?? 0,
      open_interest: m.openInterest ?? null,
      active:       m.active ?? true,
      closed:       m.closed ?? false,
      resolved:     m.resolved ?? false,
      end_date:     m.endDate ?? null,
      updated_at:   new Date().toISOString(),
    };
  });

  if (!rows.length) return 0;
  const res = await fetch(`${SUPABASE_URL}/rest/v1/polymarket_markets`, {
    method: 'POST',
    headers: SB_HEADERS,
    body: JSON.stringify(rows),
    signal: AbortSignal.timeout(10000),
  });
  return res.ok ? rows.length : 0;
}

// ── Detect arb opportunities ──────────────────────────────────────────────────

async function detectAndSaveArb(markets: GammaMarket[]) {
  const arbs = markets
    .map((m) => {
      const prices = m.outcomePrices ?? ['0.5', '0.5'];
      const yes = parseFloat(prices[0] ?? '0.5');
      const no  = parseFloat(prices[1] ?? '0.5');
      const combined = yes + no;
      const profit = 1 - combined - 0.02; // minus fees
      return { m, yes, no, combined, profit };
    })
    .filter((x) => x.profit > 0 && x.combined < 0.99)
    .sort((a, b) => b.profit - a.profit)
    .slice(0, 20);

  if (!arbs.length) return 0;

  const rows = arbs.map(({ m, yes, no, combined, profit }) => ({
    market_id:          m.conditionId,
    question:           m.question,
    type:               'single_condition',
    yes_price:          yes,
    no_price:           no,
    combined_price:     combined,
    profit_per_dollar:  profit,
    estimated_profit:   profit * Math.min(m.volume24hr ?? 1000, 5000),
    max_size_usdc:      Math.min(m.volume24hr ?? 500, 5000),
    executed:           false,
    detected_at:        new Date().toISOString(),
  }));

  const res = await fetch(`${SUPABASE_URL}/rest/v1/polymarket_arb_opportunities`, {
    method: 'POST',
    headers: { ...SB_HEADERS, Prefer: 'return=minimal' },
    body: JSON.stringify(rows),
    signal: AbortSignal.timeout(10000),
  });
  return res.ok ? rows.length : 0;
}

// ── Handler ───────────────────────────────────────────────────────────────────

export async function POST() {
  try {
    const markets = await fetchGammaMarkets();
    if (!markets.length) {
      return NextResponse.json({ ok: false, error: 'Gamma API returned no markets' }, { status: 502 });
    }

    const [marketsWritten, arbWritten] = await Promise.all([
      upsertMarkets(markets),
      detectAndSaveArb(markets),
    ]);

    return NextResponse.json({
      ok: true,
      markets_fetched: markets.length,
      markets_written: marketsWritten,
      arb_written:     arbWritten,
      scanned_at:      new Date().toISOString(),
    });
  } catch (err) {
    return NextResponse.json({ ok: false, error: String(err) }, { status: 500 });
  }
}
