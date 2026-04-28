/**
 * GET /api/bot/metrics
 *
 * Server-side proxy to the VPS Prometheus metrics endpoint.
 * Returns parsed key metrics as JSON for the dashboard.
 *
 * Metric name mapping (Prometheus → JSON field):
 *   nexus_portfolio_value_usd       → portfolio_equity
 *   nexus_current_drawdown_pct      → portfolio_drawdown
 *   nexus_open_positions            → open_positions
 *   nexus_signals_generated_total   → signals_today   (summed across labels)
 *   nexus_trades_total              → trades_today    (summed across labels)
 *   nexus_win_rate                  → win_rate
 *   nexus_daily_pnl_usd             → daily_pnl
 *   nexus_llm_requests_total        → llm_calls_today (summed across labels)
 *   nexus_active_strategies         → active_strategies
 */

import { NextResponse } from 'next/server';

const BOT_METRICS_URL =
  process.env.BOT_METRICS_URL ?? 'http://187.77.140.75:8080/metrics';

export const dynamic = 'force-dynamic';
export const revalidate = 0;

/**
 * Parse a single-value (unlabelled) Prometheus gauge/counter.
 * Returns the numeric value or null when the metric is absent.
 */
function parseScalar(text: string, metricName: string): number | null {
  const lines = text.split('\n');
  for (const line of lines) {
    if (line.startsWith('#')) continue;
    if (line.startsWith(metricName + ' ') || line.startsWith(metricName + '\t')) {
      const val = parseFloat(line.trim().split(/\s+/).pop()!);
      return isNaN(val) ? null : val;
    }
  }
  return null;
}

/**
 * Sum all time-series for a labelled counter/gauge across all label combinations.
 * Useful for counters like nexus_trades_total{market="crypto", side="long", strategy="..."}.
 */
function parseSum(text: string, metricName: string): number | null {
  const lines = text.split('\n');
  let total: number | null = null;
  for (const line of lines) {
    if (line.startsWith('#')) continue;
    if (line.startsWith(metricName + '{') || line.startsWith(metricName + ' ')) {
      const val = parseFloat(line.trim().split(/\s+/).pop()!);
      if (!isNaN(val)) {
        total = (total ?? 0) + val;
      }
    }
  }
  return total;
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

    const metrics = {
      reachable: true,
      // Gauges (single value, no labels)
      portfolio_equity:    parseScalar(text, 'nexus_portfolio_value_usd'),
      portfolio_drawdown:  parseScalar(text, 'nexus_current_drawdown_pct'),
      open_positions:      parseScalar(text, 'nexus_open_positions'),
      win_rate:            parseScalar(text, 'nexus_win_rate'),
      daily_pnl:           parseScalar(text, 'nexus_daily_pnl_usd'),
      active_strategies:   parseScalar(text, 'nexus_active_strategies'),
      // Labelled counters — sum across all label combinations
      signals_today:       parseSum(text, 'nexus_signals_generated_total'),
      trades_today:        parseSum(text, 'nexus_trades_total'),
      llm_calls_today:     parseSum(text, 'nexus_llm_requests_total'),
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
