/**
 * GET /api/bot/metrics
 *
 * Server-side proxy to the VPS Prometheus metrics endpoint.
 * Returns parsed key metrics as JSON for the dashboard.
 */

import { NextResponse } from 'next/server';

const BOT_METRICS_URL =
  process.env.BOT_METRICS_URL ?? 'http://187.77.140.75:8080/metrics';

export const dynamic = 'force-dynamic';
export const revalidate = 0;

// Minimal Prometheus text-format parser for known metrics
function parsePrometheusLine(text: string, metricName: string): number | null {
  const lines = text.split('\n');
  for (const line of lines) {
    if (line.startsWith(metricName + ' ') || line.startsWith(metricName + '{')) {
      const parts = line.trim().split(/\s+/);
      const val = parseFloat(parts[parts.length - 1]);
      return isNaN(val) ? null : val;
    }
  }
  return null;
}

export async function GET() {
  try {
    const res = await fetch(BOT_METRICS_URL, {
      signal: AbortSignal.timeout(6000),
      cache: 'no-store',
      headers: { Accept: 'text/plain' },
    });

    if (!res.ok) {
      return NextResponse.json(
        { reachable: false, error: `Metrics endpoint returned ${res.status}` },
        { status: 502 },
      );
    }

    const text = await res.text();

    // Extract key metrics for the dashboard
    const metrics = {
      reachable: true,
      portfolio_equity: parsePrometheusLine(text, 'nexus_portfolio_equity'),
      portfolio_drawdown: parsePrometheusLine(text, 'nexus_portfolio_drawdown_pct'),
      open_positions: parsePrometheusLine(text, 'nexus_open_positions'),
      signals_today: parsePrometheusLine(text, 'nexus_signals_generated_total'),
      trades_today: parsePrometheusLine(text, 'nexus_trades_executed_total'),
      win_rate: parsePrometheusLine(text, 'nexus_win_rate'),
      daily_pnl: parsePrometheusLine(text, 'nexus_daily_pnl'),
      llm_calls_today: parsePrometheusLine(text, 'nexus_llm_calls_total'),
      active_strategies: parsePrometheusLine(text, 'nexus_active_strategies'),
    };

    return NextResponse.json(metrics);
  } catch (err: unknown) {
    const msg = err instanceof Error ? err.message : String(err);
    return NextResponse.json(
      { reachable: false, error: msg },
      { status: 503 },
    );
  }
}
