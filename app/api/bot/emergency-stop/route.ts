/**
 * POST /api/bot/emergency-stop
 *
 * Server-side proxy to the VPS bot emergency-stop endpoint.
 * Avoids CORS and mixed-content issues when called from the browser.
 * Forwards to the Python bot's /api/v1/emergency-stop endpoint.
 */

import { NextResponse } from 'next/server';

const BOT_API_URL =
  process.env.BOT_API_URL ?? 'http://187.77.140.75:8080';

const BOT_API_KEY = process.env.BOT_API_KEY ?? '';

export const dynamic = 'force-dynamic';

export async function POST() {
  try {
    const res = await fetch(`${BOT_API_URL}/api/v1/emergency-stop`, {
      method: 'POST',
      signal: AbortSignal.timeout(10_000),
      headers: {
        'Content-Type': 'application/json',
        ...(BOT_API_KEY ? { Authorization: `Bearer ${BOT_API_KEY}` } : {}),
      },
      body: JSON.stringify({ reason: 'Dashboard emergency stop' }),
    });

    if (!res.ok) {
      const text = await res.text().catch(() => '');
      return NextResponse.json(
        { ok: false, error: `Bot returned ${res.status}: ${text}` },
        { status: 502 },
      );
    }

    return NextResponse.json({ ok: true });
  } catch (err: unknown) {
    const msg = err instanceof Error ? err.message : String(err);
    return NextResponse.json({ ok: false, error: msg }, { status: 503 });
  }
}
