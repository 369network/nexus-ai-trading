/**
 * POST /api/bot/paper-reset
 *
 * Resets the VPS paper trading portfolio back to initial capital.
 * Clears all open positions, trade history, and P&L counters.
 * Proxies to the VPS bot's /control/paper-reset endpoint.
 */

import { NextResponse } from 'next/server';

const BOT_API_URL =
  process.env.BOT_API_URL ?? 'http://187.77.140.75:8080';

const BOT_ADMIN_KEY = process.env.BOT_ADMIN_KEY ?? process.env.BOT_API_KEY ?? '';

export const dynamic = 'force-dynamic';

export async function POST() {
  try {
    const res = await fetch(`${BOT_API_URL}/control/paper-reset`, {
      method: 'POST',
      signal: AbortSignal.timeout(10_000),
      headers: {
        'Content-Type': 'application/json',
        ...(BOT_ADMIN_KEY ? { 'X-Admin-Key': BOT_ADMIN_KEY } : {}),
      },
    });

    if (!res.ok) {
      const text = await res.text().catch(() => '');
      return NextResponse.json(
        { ok: false, error: `Bot returned ${res.status}: ${text}` },
        { status: 502 },
      );
    }

    const data = await res.json().catch(() => ({ reset: true }));
    return NextResponse.json({ ok: true, ...data });
  } catch (err: unknown) {
    const msg = err instanceof Error ? err.message : String(err);
    return NextResponse.json({ ok: false, error: msg }, { status: 503 });
  }
}
