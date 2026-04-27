/**
 * dashboard/lib/api.ts
 * --------------------
 * Typed API client for the NEXUS ALPHA dashboard.
 *
 * Data is sourced two ways depending on the call:
 *   - Supabase directly (read-heavy, realtime-friendly queries)
 *   - Python backend REST API (mutations, risk commands, feature flag updates)
 *
 * All functions are async, fully typed with no `any`, and throw a descriptive
 * ApiError on failure so callers can show meaningful messages.
 */

import { supabase } from './supabase';
import type {
  Trade,
  Signal,
  AgentDebate,
  PortfolioSummary,
  RiskMetrics,
  CircuitBreakerStatus,
  PerformancePoint,
  FeatureFlags,
  Market,
} from './types';

// ---------------------------------------------------------------------------
// Configuration
// ---------------------------------------------------------------------------

const BACKEND_URL =
  process.env.NEXT_PUBLIC_BACKEND_URL ?? 'http://localhost:8080';

// ---------------------------------------------------------------------------
// Error type
// ---------------------------------------------------------------------------

export class ApiError extends Error {
  constructor(
    message: string,
    public readonly status: number,
    public readonly detail?: string,
  ) {
    super(message);
    this.name = 'ApiError';
  }
}

// ---------------------------------------------------------------------------
// Internal fetch helper (Python backend)
// ---------------------------------------------------------------------------

async function backendFetch<T>(
  path: string,
  options: RequestInit = {},
): Promise<T> {
  const url = `${BACKEND_URL}${path}`;
  const res = await fetch(url, {
    headers: {
      'Content-Type': 'application/json',
      ...options.headers,
    },
    ...options,
  });

  if (!res.ok) {
    const text = await res.text().catch(() => '');
    throw new ApiError(
      `Backend request failed: ${res.statusText}`,
      res.status,
      text,
    );
  }

  return res.json() as Promise<T>;
}

// ---------------------------------------------------------------------------
// Internal Supabase helper — unwrap or throw
// ---------------------------------------------------------------------------

function unwrap<T>(
  data: T | null,
  error: { message: string } | null,
  context: string,
): T {
  if (error) {
    throw new ApiError(`Supabase error in ${context}: ${error.message}`, 500, error.message);
  }
  if (data === null) {
    throw new ApiError(`No data returned from ${context}`, 404);
  }
  return data;
}

// ---------------------------------------------------------------------------
// Public API surface
// ---------------------------------------------------------------------------

export const api = {
  // ---- Portfolio --------------------------------------------------------

  async getPortfolioSummary(): Promise<PortfolioSummary> {
    // Aggregate from portfolio_snapshots + trades in the backend for accuracy
    return backendFetch<PortfolioSummary>('/api/portfolio/summary');
  },

  // ---- Trades -----------------------------------------------------------

  async getOpenTrades(): Promise<Trade[]> {
    const { data, error } = await supabase
      .from('trades')
      .select('*')
      .eq('status', 'OPEN')
      .order('entry_time', { ascending: false });

    return unwrap(data, error, 'getOpenTrades');
  },

  async getClosedTrades(limit = 100): Promise<Trade[]> {
    const { data, error } = await supabase
      .from('trades')
      .select('*')
      .eq('status', 'CLOSED')
      .order('exit_time', { ascending: false })
      .limit(limit);

    return unwrap(data, error, 'getClosedTrades');
  },

  // ---- Signals ----------------------------------------------------------

  async getSignals(market: Market, limit = 50): Promise<Signal[]> {
    const { data, error } = await supabase
      .from('signals')
      .select('*')
      .eq('market', market)
      .order('created_at', { ascending: false })
      .limit(limit);

    return unwrap(data, error, 'getSignals');
  },

  async getAllSignals(limit = 100): Promise<Signal[]> {
    const { data, error } = await supabase
      .from('signals')
      .select('*')
      .order('created_at', { ascending: false })
      .limit(limit);

    return unwrap(data, error, 'getAllSignals');
  },

  // ---- Agent Debates ----------------------------------------------------

  async getAgentDebates(limit = 20): Promise<AgentDebate[]> {
    // Agent debates are composed server-side from agent_decisions grouped by
    // symbol+timestamp; the backend exposes a convenience endpoint.
    return backendFetch<AgentDebate[]>(`/api/agents/debates?limit=${limit}`);
  },

  // ---- Risk -------------------------------------------------------------

  async getRiskMetrics(): Promise<RiskMetrics> {
    return backendFetch<RiskMetrics>('/api/risk/metrics');
  },

  async getCircuitBreakerStatus(): Promise<CircuitBreakerStatus[]> {
    return backendFetch<CircuitBreakerStatus[]>('/api/risk/circuit-breakers');
  },

  // ---- Performance History ----------------------------------------------

  async getPerformanceHistory(days = 90): Promise<PerformancePoint[]> {
    const since = new Date(Date.now() - days * 86_400_000).toISOString().split('T')[0];

    const { data, error } = await supabase
      .from('performance_history')
      .select('date, cumulative_pnl, daily_pnl, drawdown, win_rate_rolling')
      .gte('date', since)
      .order('date', { ascending: true });

    return unwrap(data, error, 'getPerformanceHistory') as PerformancePoint[];
  },

  // ---- Emergency / Control actions -------------------------------------

  async emergencyStop(): Promise<void> {
    await backendFetch<{ ok: boolean }>('/api/control/emergency-stop', {
      method: 'POST',
    });
  },

  // ---- Feature Flags ----------------------------------------------------

  async getFeatureFlags(): Promise<FeatureFlags> {
    return backendFetch<FeatureFlags>('/api/config/feature-flags');
  },

  async updateFeatureFlag(flag: string, value: boolean): Promise<void> {
    await backendFetch<{ ok: boolean }>('/api/config/feature-flags', {
      method: 'PATCH',
      body: JSON.stringify({ flag, value }),
    });
  },
};

export default api;
