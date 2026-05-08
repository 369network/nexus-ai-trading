/**
 * POST /api/bot/fix-portfolio
 *
 * Full paper-trading reset in Supabase:
 *  1. Cancels ALL old OPEN paper trades (stale entries that poison portfolio restore)
 *  2. Inserts a fresh portfolio_snapshot with $10,000 initial capital
 *
 * After calling this, restart the VPS bot — it will start with $10,000 and no
 * stale positions. Safe to call multiple times.
 */

import { NextResponse } from 'next/server';
import { createClient } from '@supabase/supabase-js';

export const dynamic = 'force-dynamic';

const INITIAL_CAPITAL = 10_000; // USD

export async function POST() {
  const supabaseUrl  = process.env.NEXT_PUBLIC_SUPABASE_URL;
  const supabaseKey  = process.env.SUPABASE_SERVICE_ROLE_KEY
                    ?? process.env.NEXT_PUBLIC_SUPABASE_ANON_KEY;

  if (!supabaseUrl || !supabaseKey) {
    return NextResponse.json(
      { ok: false, error: 'Supabase env vars not set' },
      { status: 503 },
    );
  }

  const supabase = createClient(supabaseUrl, supabaseKey);

  try {
    // ── Step 1: Cancel all stale OPEN paper trades ─────────────────────────
    // These are what caused the bot to restore 250+ old positions and show -99% drawdown.
    const { data: openTrades, error: fetchErr } = await supabase
      .from('trades')
      .select('id, symbol, entry_price, quantity')
      .eq('status', 'OPEN')
      .eq('execution_mode', 'paper');

    if (fetchErr) {
      return NextResponse.json(
        { ok: false, error: `Failed to fetch open trades: ${fetchErr.message}` },
        { status: 500 },
      );
    }

    const openCount = openTrades?.length ?? 0;

    if (openCount > 0) {
      const { error: cancelErr } = await supabase
        .from('trades')
        .update({ status: 'CANCELLED', closed_at: new Date().toISOString() })
        .eq('status', 'OPEN')
        .eq('execution_mode', 'paper');

      if (cancelErr) {
        return NextResponse.json(
          { ok: false, error: `Failed to cancel open trades: ${cancelErr.message}` },
          { status: 500 },
        );
      }
    }

    // ── Step 2: Insert a clean portfolio snapshot ──────────────────────────
    // Bot's _restore_state_from_db does: ORDER BY created_at DESC LIMIT 1
    // So inserting now means the bot picks this up on next restart.
    const { error: insertErr } = await supabase
      .from('portfolio_snapshots')
      .insert({
        equity:          INITIAL_CAPITAL,
        cash:            INITIAL_CAPITAL,
        positions_value: 0,
        daily_pnl:       0,
        daily_pnl_pct:   0,
        total_pnl:       0,
        drawdown_pct:    0,
        open_positions:  0,
        win_rate:        0,
        portfolio_heat:  0,
      });

    if (insertErr) {
      return NextResponse.json(
        { ok: false, error: `Snapshot insert failed: ${insertErr.message}` },
        { status: 500 },
      );
    }

    return NextResponse.json({
      ok:              true,
      trades_cancelled: openCount,
      initial_capital:  INITIAL_CAPITAL,
      message: `Reset complete: ${openCount} stale OPEN trades cancelled, fresh $${INITIAL_CAPITAL.toLocaleString()} snapshot inserted.`,
      next_step: 'Restart the VPS bot — it will now start with $10,000 and 0 positions.',
      timestamp: new Date().toISOString(),
    });
  } catch (err: unknown) {
    const msg = err instanceof Error ? err.message : String(err);
    return NextResponse.json({ ok: false, error: msg }, { status: 500 });
  }
}
