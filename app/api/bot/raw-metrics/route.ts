/**
 * GET /api/bot/raw-metrics
 * ------------------------
 * Returns the raw Prometheus text from the VPS bot metrics endpoint.
 * Used for debugging — shows exactly which metrics the VPS bot exports
 * and their current values.
 */

import { NextResponse } from 'next/server';

const BOT_METRICS_URL =
  process.env.BOT_METRICS_URL ?? 'http://187.77.140.75:8080/metrics';

export const dynamic = 'force-dynamic';
export const revalidate = 0;

export async function GET() {
  try {
    const res = await fetch(BOT_METRICS_URL, {
      signal: AbortSignal.timeout(8000),
      cache: 'no-store',
      headers: { Accept: 'text/plain' },
    });

    if (!res.ok) {
      return NextResponse.json(
        { reachable: false, error: `VPS returned ${res.status}` },
        { status: 502 },
      );
    }

    const raw = await res.text();

    // Extract only nexus_* lines for clarity (plus any portfolio/equity related lines)
    const lines = raw.split('\n');
    const nexusLines = lines.filter(
      (l) =>
        l.startsWith('nexus_') ||
        l.startsWith('# HELP nexus_') ||
        l.startsWith('# TYPE nexus_') ||
        l.toLowerCase().includes('portfolio') ||
        l.toLowerCase().includes('equity') ||
        l.toLowerCase().includes('drawdown') ||
        l.toLowerCase().includes('pnl') ||
        l.toLowerCase().includes('position') ||
        l.toLowerCase().includes('win_rate') ||
        l.toLowerCase().includes('trade') ||
        l.toLowerCase().includes('signal'),
    );

    return NextResponse.json({
      reachable: true,
      total_lines: lines.length,
      nexus_lines: nexusLines.length,
      nexus_metrics: nexusLines.join('\n'),
      full_raw: raw,
      timestamp: new Date().toISOString(),
    });
  } catch (err: unknown) {
    const msg = err instanceof Error ? err.message : String(err);
    return NextResponse.json(
      { reachable: false, error: msg },
      { status: 503 },
    );
  }
}
