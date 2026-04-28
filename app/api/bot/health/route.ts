/**
 * GET /api/bot/health
 *
 * Server-side proxy to the VPS bot health endpoint.
 * Avoids mixed-content browser errors (HTTPS page → HTTP backend).
 *
 * Response mirrors the bot's /health JSON payload plus an extra
 * `reachable` boolean so the client can distinguish "bot down" from
 * "proxy error".
 */

import { NextResponse } from 'next/server';

const BOT_HEALTH_URL =
  process.env.BOT_HEALTH_URL ?? 'http://187.77.140.75:8080/health';

export const dynamic = 'force-dynamic';
export const revalidate = 0;

export async function GET() {
  try {
    const res = await fetch(BOT_HEALTH_URL, {
      signal: AbortSignal.timeout(6000),
      cache: 'no-store',
      headers: { Accept: 'application/json' },
    });

    if (!res.ok) {
      return NextResponse.json(
        { reachable: false, error: `Bot returned ${res.status}` },
        { status: 502 },
      );
    }

    const data = await res.json();
    return NextResponse.json({ reachable: true, ...data });
  } catch (err: unknown) {
    const msg = err instanceof Error ? err.message : String(err);
    return NextResponse.json(
      { reachable: false, error: msg },
      { status: 503 },
    );
  }
}
