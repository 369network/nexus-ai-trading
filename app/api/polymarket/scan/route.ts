/**
 * POST /api/polymarket/scan
 * ─────────────────────────
 * On-demand Polymarket scan:
 *  1. Fetch top 100 active markets from Gamma API
 *  2. Detect arbitrage opportunities (YES + NO < 0.98)
 *  3. Upsert discovered markets + arb opportunities to Supabase
 *
 * Returns summary of markets found, arb found, rows written.
 *
 * NOTE: Gamma API returns `outcomePrices` and `clobTokenIds` as
 *       JSON-encoded strings (e.g. '["0.53","0.47"]'), not real arrays.
 *       `parseJsonField()` normalises them before use so we never send
 *       NaN / null into NOT-NULL numeric columns.
 */

import { NextResponse } from 'next/server';
import { createClient } from '@/lib/supabase';

export const dynamic = 'force-dynamic';

const GAMMA = 'https://gamma-api.polymarket.com';

// ── Helpers ───────────────────────────────────────────────────────────────────

/** Gamma API may return array fields as JSON-encoded strings — normalise them. */
function parseJsonField<T>(v: T | string | undefined | null, fallback: T): T {
  if (v === null || v === undefined) return fallback;
  if (Array.isArray(v)) return v as unknown as T;
  if (typeof v === 'string') {
    try { return JSON.parse(v) as T; } catch { return fallback; }
  }
  return v as T;
}

/** Safe parseFloat that falls back to `def` on NaN / null / undefined. */
function safeParse(s: string | undefined | null, def: number): number {
  if (s == null) return def;
  const n = parseFloat(s);
  return isFinite(n) ? n : def;
}

// ── Fetch markets from Gamma ──────────────────────────────────────────────────

interface GammaMarket {
  conditionId: string;
  question: string;
  description?: string;
  category?: string;
  marketSlug?: string;
  /** May arrive as a JSON-encoded string from Gamma API */
  clobTokenIds?: string[] | string;
  /** May arrive as a JSON-encoded string from Gamma API */
  outcomePrices?: string[] | string;
  volume24hr?: number;
  volume?: number;
  openInterest?: number;
  active?: boolean;
  closed?: boolean;
  resolved?: boolean;
  endDate?: string;
}

/** Normalised market — arrays are always real arrays after fetch. */
interface NormalMarket extends Omit<GammaMarket, 'outcomePrices' | 'clobTokenIds'> {
  outcomePrices: string[];
  clobTokenIds: string[];
}

async function fetchGammaMarkets(): Promise<NormalMarket[]> {
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
  const raw: GammaMarket[] = Array.isArray(json) ? json : (json.markets ?? []);

  return raw.map((m) => ({
    ...m,
    // Gamma API may return these as JSON-encoded strings — parse them
    outcomePrices: parseJsonField<string[]>(m.outcomePrices, ['0.5', '0.5']),
    clobTokenIds:  parseJsonField<string[]>(m.clobTokenIds,  []),
  }));
}

// ── Upsert polymarket_markets ─────────────────────────────────────────────────

async function upsertMarkets(supabase: ReturnType<typeof createClient>, markets: NormalMarket[]) {
  const rows = markets.map((m) => ({
    id:            m.conditionId,
    question:      m.question,
    description:   m.description ?? null,
    category:      m.category ?? null,
    market_slug:   m.marketSlug ?? null,
    yes_token_id:  m.clobTokenIds[0] ?? '',
    no_token_id:   m.clobTokenIds[1] ?? '',
    yes_price:     safeParse(m.outcomePrices[0], 0.5),
    no_price:      safeParse(m.outcomePrices[1], 0.5),
    volume_24h:    m.volume24hr ?? 0,
    total_volume:  m.volume ?? 0,
    open_interest: m.openInterest ?? null,
    active:        m.active ?? true,
    closed:        m.closed ?? false,
    resolved:      m.resolved ?? false,
    end_date:      m.endDate ?? null,
    updated_at:    new Date().toISOString(),
  }));

  if (!rows.length) return 0;

  const { error } = await supabase
    .from('polymarket_markets')
    .upsert(rows, { onConflict: 'id', ignoreDuplicates: false });

  if (error) {
    console.error('[scan] upsertMarkets error:', error.message);
    return 0;
  }
  return rows.length;
}

// ── Detect arb opportunities ──────────────────────────────────────────────────

async function detectAndSaveArb(supabase: ReturnType<typeof createClient>, markets: NormalMarket[]) {
  const arbs = markets
    .map((m) => {
      const yes = safeParse(m.outcomePrices[0], 0.5);
      const no  = safeParse(m.outcomePrices[1], 0.5);
      const combined = yes + no;
      const profit = 1 - combined - 0.02; // minus fees
      return { m, yes, no, combined, profit };
    })
    .filter((x) => x.profit > 0 && x.combined < 0.99)
    .sort((a, b) => b.profit - a.profit)
    .slice(0, 20);

  if (!arbs.length) return 0;

  const rows = arbs.map(({ m, yes, no, combined, profit }) => ({
    market_id:         m.conditionId,
    question:          m.question,
    type:              'single_condition',
    yes_price:         yes,
    no_price:          no,
    combined_price:    combined,
    profit_per_dollar: profit,
    estimated_profit:  profit * Math.min(m.volume24hr ?? 1000, 5000),
    max_size_usdc:     Math.min(m.volume24hr ?? 500, 5000),
    executed:          false,
    detected_at:       new Date().toISOString(),
  }));

  const { error } = await supabase
    .from('polymarket_arb_opportunities')
    .insert(rows);

  if (error) {
    console.error('[scan] detectAndSaveArb error:', error.message);
    return 0;
  }
  return rows.length;
}

// ── Handler ───────────────────────────────────────────────────────────────────

export async function POST() {
  try {
    const supabase = createClient();
    const markets = await fetchGammaMarkets();
    if (!markets.length) {
      return NextResponse.json({ ok: false, error: 'Gamma API returned no markets' }, { status: 502 });
    }

    const [marketsWritten, arbWritten] = await Promise.all([
      upsertMarkets(supabase, markets),
      detectAndSaveArb(supabase, markets),
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
