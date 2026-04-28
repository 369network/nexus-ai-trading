/**
 * useBotMetrics
 * -------------
 * Polls /api/bot/metrics every 30 seconds and returns parsed
 * key metrics from the VPS Prometheus endpoint.
 *
 * Returns null values when the bot is unreachable so the UI
 * can show a clear "offline" state instead of stale numbers.
 */

'use client';

import { useEffect, useState, useCallback } from 'react';

export interface BotMetrics {
  reachable: boolean;
  portfolio_equity: number | null;
  portfolio_drawdown: number | null;
  open_positions: number | null;
  signals_today: number | null;
  trades_today: number | null;
  win_rate: number | null;
  daily_pnl: number | null;
  llm_calls_today: number | null;
  active_strategies: number | null;
  fetchedAt: string;
}

const DEFAULT_METRICS: BotMetrics = {
  reachable: false,
  portfolio_equity: null,
  portfolio_drawdown: null,
  open_positions: null,
  signals_today: null,
  trades_today: null,
  win_rate: null,
  daily_pnl: null,
  llm_calls_today: null,
  active_strategies: null,
  fetchedAt: new Date().toISOString(),
};

const POLL_INTERVAL_MS = 30_000;

export function useBotMetrics(): BotMetrics {
  const [metrics, setMetrics] = useState<BotMetrics>(DEFAULT_METRICS);

  const fetch_metrics = useCallback(async () => {
    try {
      const res = await fetch('/api/bot/metrics', {
        cache: 'no-store',
        signal: AbortSignal.timeout(8000),
      });

      if (!res.ok) {
        setMetrics((prev) => ({ ...prev, reachable: false, fetchedAt: new Date().toISOString() }));
        return;
      }

      const data = await res.json();
      setMetrics({
        reachable: data.reachable ?? true,
        portfolio_equity: data.portfolio_equity ?? null,
        portfolio_drawdown: data.portfolio_drawdown ?? null,
        open_positions: data.open_positions ?? null,
        signals_today: data.signals_today ?? null,
        trades_today: data.trades_today ?? null,
        win_rate: data.win_rate ?? null,
        daily_pnl: data.daily_pnl ?? null,
        llm_calls_today: data.llm_calls_today ?? null,
        active_strategies: data.active_strategies ?? null,
        fetchedAt: new Date().toISOString(),
      });
    } catch {
      // Bot unreachable — keep last known values but mark as offline
      setMetrics((prev) => ({ ...prev, reachable: false, fetchedAt: new Date().toISOString() }));
    }
  }, []);

  useEffect(() => {
    fetch_metrics();
    const timer = setInterval(fetch_metrics, POLL_INTERVAL_MS);
    return () => clearInterval(timer);
  }, [fetch_metrics]);

  return metrics;
}
