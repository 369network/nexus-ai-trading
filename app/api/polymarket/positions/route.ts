import { NextResponse } from 'next/server';
import { createClient } from '@/lib/supabase';
import type { PolymarketPosition } from '@/lib/types';

export const dynamic = 'force-dynamic';

export async function GET() {
  try {
    const supabase = createClient();

    const { data, error } = await supabase
      .from('polymarket_positions')
      .select('*')
      .eq('status', 'OPEN')
      .order('opened_at', { ascending: false });

    if (error) {
      const isTableMissing =
        error.code === 'PGRST116' ||
        error.message?.toLowerCase().includes('does not exist') ||
        error.message?.toLowerCase().includes('relation');

      if (isTableMissing) {
        return NextResponse.json({
          positions: [],
          count:     0,
          timestamp: new Date().toISOString(),
          note:      'polymarket_positions table not yet created',
        });
      }

      console.error('[/api/polymarket/positions] Supabase error:', error);
      return NextResponse.json(
        { positions: [], count: 0, timestamp: new Date().toISOString(), error: error.message },
        { status: 500 }
      );
    }

    const positions: PolymarketPosition[] = (data ?? []).map((row) => ({
      id:              row.id,
      market_id:       row.market_id ?? '',
      question:        row.question ?? '',
      outcome:         row.outcome ?? 'YES',
      token_id:        row.token_id ?? '',
      size:            parseFloat(row.size ?? 0),
      avg_price:       parseFloat(row.avg_price ?? 0),
      current_price:   parseFloat(row.current_price ?? 0),
      unrealized_pnl:  parseFloat(row.unrealized_pnl ?? 0),
      status:          row.status ?? 'OPEN',
      resolution:      row.resolution ?? undefined,
      realized_pnl:    row.realized_pnl != null ? parseFloat(row.realized_pnl) : undefined,
      opened_at:       row.opened_at,
      closed_at:       row.closed_at ?? undefined,
    }));

    return NextResponse.json({
      positions,
      count:     positions.length,
      timestamp: new Date().toISOString(),
    });
  } catch (err) {
    console.error('[/api/polymarket/positions]', err);
    return NextResponse.json(
      { positions: [], count: 0, timestamp: new Date().toISOString(), error: 'Internal error' },
      { status: 500 }
    );
  }
}
