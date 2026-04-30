import { NextResponse } from 'next/server';
import { createClient } from '@/lib/supabase';
import type { PolymarketStats } from '@/lib/types';

export const dynamic = 'force-dynamic';

const ZERO_STATS: PolymarketStats = {
  total_pnl:                0,
  win_rate:                 0,
  open_positions:           0,
  best_edge:                0,
  total_deployed:           0,
  opportunities_found_today: 0,
};

export async function GET() {
  try {
    const supabase = createClient();

    // Run queries in parallel; handle missing tables gracefully
    const [posRes, sigRes] = await Promise.all([
      supabase
        .from('polymarket_positions')
        .select('unrealized_pnl, realized_pnl, avg_price, size, status')
        .limit(500),
      supabase
        .from('polymarket_signals')
        .select('edge, executed, created_at')
        .order('created_at', { ascending: false })
        .limit(200),
    ]);

    // If either table is missing, return zeros
    const posTableMissing =
      posRes.error &&
      (posRes.error.code === 'PGRST116' ||
        posRes.error.message?.toLowerCase().includes('does not exist'));

    const sigTableMissing =
      sigRes.error &&
      (sigRes.error.code === 'PGRST116' ||
        sigRes.error.message?.toLowerCase().includes('does not exist'));

    if (posTableMissing && sigTableMissing) {
      return NextResponse.json({
        ...ZERO_STATS,
        timestamp: new Date().toISOString(),
        note: 'Polymarket tables not yet created',
      });
    }

    const positions = posRes.data ?? [];
    const signals   = sigRes.data ?? [];

    // --- Compute stats from positions ---
    const openPositions = positions.filter((p) => p.status === 'OPEN');

    const unrealizedPnl = openPositions.reduce(
      (sum, p) => sum + parseFloat(p.unrealized_pnl ?? 0),
      0
    );

    const realizedPnl = positions
      .filter((p) => p.status === 'CLOSED' || p.status === 'RESOLVED')
      .reduce((sum, p) => sum + parseFloat(p.realized_pnl ?? 0), 0);

    const totalPnl = unrealizedPnl + realizedPnl;

    // Capital deployed = sum of (avg_price × size) for open positions
    const totalDeployed = openPositions.reduce(
      (sum, p) => sum + parseFloat(p.avg_price ?? 0) * parseFloat(p.size ?? 0),
      0
    );

    // Win rate over closed/resolved positions
    const closedPositions = positions.filter(
      (p) => (p.status === 'CLOSED' || p.status === 'RESOLVED') && p.realized_pnl != null
    );
    const winners  = closedPositions.filter((p) => parseFloat(p.realized_pnl ?? 0) > 0);
    const winRate  = closedPositions.length > 0 ? winners.length / closedPositions.length : 0;

    // Best edge from recent unexecuted signals
    const bestEdge = signals.reduce((max, s) => {
      const e = parseFloat(s.edge ?? 0);
      return e > max ? e : max;
    }, 0);

    // Opportunities found today (signals created today)
    const todayStart = new Date();
    todayStart.setHours(0, 0, 0, 0);
    const oppsToday = signals.filter(
      (s) => s.created_at && new Date(s.created_at) >= todayStart
    ).length;

    const stats: PolymarketStats = {
      total_pnl:                totalPnl,
      win_rate:                 winRate,
      open_positions:           openPositions.length,
      best_edge:                bestEdge,
      total_deployed:           totalDeployed,
      opportunities_found_today: oppsToday,
    };

    return NextResponse.json({
      ...stats,
      timestamp: new Date().toISOString(),
    });
  } catch (err) {
    console.error('[/api/polymarket/stats]', err);
    return NextResponse.json(
      { ...ZERO_STATS, timestamp: new Date().toISOString(), error: 'Internal error' },
      { status: 500 }
    );
  }
}
