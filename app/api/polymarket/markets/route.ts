/**
 * GET /api/polymarket/markets
 * ───────────────────────────
 * Returns Polymarket binary markets from the polymarket_markets Supabase table.
 * Data is populated by:
 *  • POST /api/polymarket/scan  (on-demand dashboard scan)
 *  • VPS coordinator            (every 5 minutes via GammaClient)
 *
 * Query params:
 *  limit      — max rows to return (default 50, max 200)
 *  category   — filter by category string (case-insensitive)
 *  min_volume — minimum 24h volume in USDC (default 0)
 */

import { NextRequest, NextResponse } from 'next/server';
import { createClient } from '@/lib/supabase';
import type { PolymarketMarket } from '@/lib/types';

export const dynamic = 'force-dynamic';

export async function GET(req: NextRequest) {
  const { searchParams } = new URL(req.url);
  const limit    = Math.min(parseInt(searchParams.get('limit') ?? '50', 10), 200);
  const category = (searchParams.get('category') ?? 'all').toLowerCase();
  const minVol   = parseFloat(searchParams.get('min_volume') ?? '0');

  try {
    const supabase = createClient();

    let query = supabase
      .from('polymarket_markets')
      .select(
        'id, question, description, category, market_slug, yes_token_id, no_token_id, ' +
        'yes_price, no_price, volume_24h, total_volume, active, closed, resolved, ' +
        'resolution, end_date, updated_at'
      )
      .eq('active', true)
      .eq('closed', false)
      .eq('resolved', false)
      .neq('id', 'test-001')           // exclude seed / test rows
      .order('volume_24h', { ascending: false })
      .limit(limit);

    if (minVol > 0) {
      query = query.gte('volume_24h', minVol);
    }
    if (category && category !== 'all') {
      query = query.ilike('category', category);
    }

    const { data, error } = await query;

    if (error) {
      console.error('[/api/polymarket/markets] Supabase error:', error.message);
      return NextResponse.json(
        { markets: [], count: 0, timestamp: new Date().toISOString(), error: error.message },
        { status: 500 }
      );
    }

    // eslint-disable-next-line @typescript-eslint/no-explicit-any
    const markets: PolymarketMarket[] = ((data ?? []) as any[]).map((row) => ({
      id:           row.id,
      question:     row.question,
      description:  row.description ?? '',
      category:     row.category ?? '',
      market_slug:  row.market_slug ?? '',
      yes_token_id: row.yes_token_id,
      no_token_id:  row.no_token_id,
      yes_price:    Number(row.yes_price),
      no_price:     Number(row.no_price),
      volume_24h:   Number(row.volume_24h),
      total_volume: Number(row.total_volume),
      active:       row.active,
      closed:       row.closed,
      resolved:     row.resolved,
      resolution:   row.resolution ?? undefined,
      end_date:     row.end_date ?? undefined,
    }));

    return NextResponse.json({
      markets,
      count:     markets.length,
      timestamp: new Date().toISOString(),
    });
  } catch (err) {
    console.error('[/api/polymarket/markets]', err);
    return NextResponse.json(
      { markets: [], count: 0, timestamp: new Date().toISOString(), error: 'Internal error' },
      { status: 500 }
    );
  }
}
