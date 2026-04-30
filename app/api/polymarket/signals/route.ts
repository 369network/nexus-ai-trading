import { NextResponse } from 'next/server';
import { createClient } from '@/lib/supabase';
import type { PolymarketSignal } from '@/lib/types';

export const dynamic = 'force-dynamic';

export async function GET() {
  try {
    const supabase = createClient();

    const { data, error } = await supabase
      .from('polymarket_signals')
      .select('*')
      .order('created_at', { ascending: false })
      .limit(20);

    // If the table doesn't exist yet (PGRST116 = relation not found)
    // or any schema error, return an honest empty state.
    if (error) {
      const isTableMissing =
        error.code === 'PGRST116' ||
        error.message?.toLowerCase().includes('does not exist') ||
        error.message?.toLowerCase().includes('relation');

      if (isTableMissing) {
        return NextResponse.json({
          signals:   [],
          count:     0,
          timestamp: new Date().toISOString(),
          note:      'polymarket_signals table not yet created',
        });
      }

      console.error('[/api/polymarket/signals] Supabase error:', error);
      return NextResponse.json(
        { signals: [], count: 0, timestamp: new Date().toISOString(), error: error.message },
        { status: 500 }
      );
    }

    const signals: PolymarketSignal[] = (data ?? []).map((row) => ({
      id:              row.id,
      market_id:       row.market_id ?? '',
      question:        row.question ?? '',
      market_price:    parseFloat(row.market_price ?? 0),
      agent_estimate:  parseFloat(row.agent_estimate ?? 0),
      edge:            parseFloat(row.edge ?? 0),
      swarm_std_dev:   parseFloat(row.swarm_std_dev ?? 0),
      direction:       row.direction ?? 'YES',
      kelly_fraction:  parseFloat(row.kelly_fraction ?? 0),
      position_usdc:   parseFloat(row.position_usdc ?? 0),
      strategy:        row.strategy ?? 'llm_ensemble',
      agent_breakdown: row.agent_breakdown ?? {},
      reasoning:       row.reasoning ?? '',
      executed:        row.executed ?? false,
      created_at:      row.created_at,
    }));

    return NextResponse.json({
      signals,
      count:     signals.length,
      timestamp: new Date().toISOString(),
    });
  } catch (err) {
    console.error('[/api/polymarket/signals]', err);
    return NextResponse.json(
      { signals: [], count: 0, timestamp: new Date().toISOString(), error: 'Internal error' },
      { status: 500 }
    );
  }
}
