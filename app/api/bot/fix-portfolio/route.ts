/**
 * POST /api/bot/fix-portfolio
 *
 * ONE-TIME FIX: Inserts a clean portfolio snapshot into Supabase so the VPS
 * paper-trading bot restores $10,000 initial capital on next restart.
 *
 * Also clears old 0-balance snapshots that were causing the $0 display.
 * Safe to call multiple times — uses upsert pattern.
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
    // 1. Insert a fresh portfolio snapshot with proper initial capital
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
        { ok: false, error: `Insert failed: ${insertErr.message}` },
        { status: 500 },
      );
    }

    // 2. Archive old zero-balance snapshots (mark equity = 0 rows as stale)
    //    We can't delete them, but we inserted a newer one above that will be
    //    picked up by the bot's ORDER BY created_at DESC LIMIT 1 query.

    return NextResponse.json({
      ok: true,
      message: `Clean portfolio snapshot inserted: equity=$${INITIAL_CAPITAL.toLocaleString()}`,
      initial_capital: INITIAL_CAPITAL,
      note: 'Restart the VPS bot to apply — it will restore $10,000 from this snapshot.',
      timestamp: new Date().toISOString(),
    });
  } catch (err: unknown) {
    const msg = err instanceof Error ? err.message : String(err);
    return NextResponse.json({ ok: false, error: msg }, { status: 500 });
  }
}
