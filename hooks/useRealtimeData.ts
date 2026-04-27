/**
 * dashboard/hooks/useRealtimeData.ts
 * -----------------------------------
 * React hooks that subscribe to Supabase Realtime channels and keep local
 * state in sync.  Each hook returns the latest snapshot of the data it
 * tracks and handles subscription cleanup on unmount automatically.
 */

'use client';

import { useEffect, useRef, useState, useCallback } from 'react';
import type { RealtimeChannel } from '@supabase/supabase-js';
import { supabase } from '../lib/supabase';
import { api } from '../lib/api';
import type {
  Signal,
  Trade,
  RiskMetrics,
  PortfolioSummary,
  Market,
  LoadingState,
} from '../lib/types';

// ---------------------------------------------------------------------------
// Helper: stable channel cleanup
// ---------------------------------------------------------------------------

function useChannelCleanup(channelRef: React.MutableRefObject<RealtimeChannel | null>) {
  useEffect(() => {
    return () => {
      if (channelRef.current) {
        supabase.removeChannel(channelRef.current);
        channelRef.current = null;
      }
    };
  }, [channelRef]);
}

// ---------------------------------------------------------------------------
// useRealtimeSignals
// ---------------------------------------------------------------------------

/**
 * Subscribes to the `signals` table INSERT events via Supabase Realtime.
 * Optionally filters by market.  Returns an array of signals ordered
 * newest-first, capped at `maxItems`.
 */
export function useRealtimeSignals(market?: Market, maxItems = 100): Signal[] {
  const [signals, setSignals] = useState<Signal[]>([]);
  const channelRef = useRef<RealtimeChannel | null>(null);

  useEffect(() => {
    // Seed from REST on mount
    const seed = async () => {
      try {
        const initial = market
          ? await api.getSignals(market, maxItems)
          : await api.getAllSignals(maxItems);
        setSignals(initial);
      } catch {
        // Non-fatal: realtime will populate as events arrive
      }
    };
    seed();

    // Subscribe to new inserts
    const channelName = market ? `signals-live-${market}` : 'signals-live-all';

    const filter = market
      ? { event: 'INSERT' as const, schema: 'public', table: 'signals', filter: `market=eq.${market}` }
      : { event: 'INSERT' as const, schema: 'public', table: 'signals' };

    channelRef.current = supabase
      .channel(channelName)
      .on('postgres_changes', filter, (payload) => {
        const incoming = payload.new as Signal;
        setSignals((prev) => [incoming, ...prev].slice(0, maxItems));
      })
      .subscribe();

    return () => {
      if (channelRef.current) {
        supabase.removeChannel(channelRef.current);
        channelRef.current = null;
      }
    };
  }, [market, maxItems]);

  return signals;
}

// ---------------------------------------------------------------------------
// useRealtimeTrades
// ---------------------------------------------------------------------------

/**
 * Subscribes to `trades` INSERT and UPDATE events.
 * Returns the current list of open trades, updated in real-time.
 */
export function useRealtimeTrades(): Trade[] {
  const [trades, setTrades] = useState<Trade[]>([]);
  const channelRef = useRef<RealtimeChannel | null>(null);

  useEffect(() => {
    // Seed open trades on mount
    const seed = async () => {
      try {
        const openTrades = await api.getOpenTrades();
        setTrades(openTrades);
      } catch {
        // Non-fatal
      }
    };
    seed();

    channelRef.current = supabase
      .channel('trades-live-hook')
      .on(
        'postgres_changes',
        { event: 'INSERT', schema: 'public', table: 'trades' },
        (payload) => {
          const incoming = payload.new as Trade;
          setTrades((prev) => [incoming, ...prev]);
        },
      )
      .on(
        'postgres_changes',
        { event: 'UPDATE', schema: 'public', table: 'trades' },
        (payload) => {
          const updated = payload.new as Trade;
          setTrades((prev) =>
            prev.map((t) => (t.id === updated.id ? updated : t)),
          );
        },
      )
      .on(
        'postgres_changes',
        { event: 'DELETE', schema: 'public', table: 'trades' },
        (payload) => {
          const deleted = payload.old as Pick<Trade, 'id'>;
          setTrades((prev) => prev.filter((t) => t.id !== deleted.id));
        },
      )
      .subscribe();

    return () => {
      if (channelRef.current) {
        supabase.removeChannel(channelRef.current);
        channelRef.current = null;
      }
    };
  }, []);

  return trades;
}

// ---------------------------------------------------------------------------
// useRealtimeRisk
// ---------------------------------------------------------------------------

/**
 * Polls risk metrics from the backend on a configurable interval and also
 * subscribes to the `risk_events` table so the UI updates immediately when
 * a circuit breaker fires.
 *
 * @param pollingIntervalMs  How often to re-fetch full metrics. Default 10 s.
 */
export function useRealtimeRisk(pollingIntervalMs = 10_000): RiskMetrics {
  const defaultMetrics: RiskMetrics = {
    portfolio_heat: 0,
    max_drawdown: 0,
    current_drawdown: 0,
    correlation_warning: false,
    circuit_breakers_active: 0,
    daily_loss_limit_used_pct: 0,
    var_95: null,
    leverage_ratio: 0,
  };

  const [metrics, setMetrics] = useState<RiskMetrics>(defaultMetrics);
  const channelRef = useRef<RealtimeChannel | null>(null);
  const intervalRef = useRef<ReturnType<typeof setInterval> | null>(null);

  const fetchMetrics = useCallback(async () => {
    try {
      const data = await api.getRiskMetrics();
      setMetrics(data);
    } catch {
      // Keep last-known-good value
    }
  }, []);

  useEffect(() => {
    // Initial fetch
    fetchMetrics();

    // Polling
    intervalRef.current = setInterval(fetchMetrics, pollingIntervalMs);

    // Realtime trigger: re-fetch when a risk event fires so the UI reacts
    // immediately rather than waiting for the next poll cycle.
    channelRef.current = supabase
      .channel('risk-hook')
      .on(
        'postgres_changes',
        { event: 'INSERT', schema: 'public', table: 'risk_events' },
        () => {
          fetchMetrics();
        },
      )
      .subscribe();

    return () => {
      if (intervalRef.current) clearInterval(intervalRef.current);
      if (channelRef.current) {
        supabase.removeChannel(channelRef.current);
        channelRef.current = null;
      }
    };
  }, [fetchMetrics, pollingIntervalMs]);

  return metrics;
}

// ---------------------------------------------------------------------------
// usePortfolioSummary
// ---------------------------------------------------------------------------

/**
 * Fetches the portfolio summary on mount and re-fetches whenever a new
 * `portfolio_snapshots` row is inserted (Supabase Realtime trigger).
 *
 * Returns a `LoadingState<PortfolioSummary>` so the UI can show a skeleton
 * during the initial load and an error message on failure.
 */
export function usePortfolioSummary(): LoadingState<PortfolioSummary> {
  const [state, setState] = useState<LoadingState<PortfolioSummary>>({
    data: null,
    loading: true,
    error: null,
  });
  const channelRef = useRef<RealtimeChannel | null>(null);

  const fetchSummary = useCallback(async () => {
    setState((prev) => ({ ...prev, loading: true, error: null }));
    try {
      const data = await api.getPortfolioSummary();
      setState({ data, loading: false, error: null });
    } catch (err) {
      setState((prev) => ({
        ...prev,
        loading: false,
        error: err instanceof Error ? err : new Error(String(err)),
      }));
    }
  }, []);

  useEffect(() => {
    fetchSummary();

    // Re-fetch whenever a new portfolio snapshot arrives
    channelRef.current = supabase
      .channel('portfolio-summary-hook')
      .on(
        'postgres_changes',
        { event: 'INSERT', schema: 'public', table: 'portfolio_snapshots' },
        () => {
          fetchSummary();
        },
      )
      .subscribe();

    return () => {
      if (channelRef.current) {
        supabase.removeChannel(channelRef.current);
        channelRef.current = null;
      }
    };
  }, [fetchSummary]);

  return state;
}

// ---------------------------------------------------------------------------
// useRealtimePrice  (utility: subscribe to a single symbol's latest close)
// ---------------------------------------------------------------------------

/**
 * Subscribes to `market_data` inserts for a single symbol and returns the
 * latest closing price.
 */
export function useRealtimePrice(symbol: string): number | null {
  const [price, setPrice] = useState<number | null>(null);
  const channelRef = useRef<RealtimeChannel | null>(null);

  useEffect(() => {
    if (!symbol) return;

    channelRef.current = supabase
      .channel(`price-${symbol}`)
      .on(
        'postgres_changes',
        {
          event: 'INSERT',
          schema: 'public',
          table: 'market_data',
          filter: `symbol=eq.${symbol}`,
        },
        (payload) => {
          const row = payload.new as { close: number };
          if (typeof row.close === 'number') {
            setPrice(row.close);
          }
        },
      )
      .subscribe();

    return () => {
      if (channelRef.current) {
        supabase.removeChannel(channelRef.current);
        channelRef.current = null;
      }
    };
  }, [symbol]);

  return price;
}
