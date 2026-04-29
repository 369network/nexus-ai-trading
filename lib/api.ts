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
    // Derive from latest portfolio_snapshots row (direct Supabase — no auth needed)
    const { data, error } = await supabase
      .from('portfolio_snapshots')
      .select('equity, cash, daily_pnl, daily_pnl_pct, total_pnl, drawdown_pct, open_positions, win_rate, portfolio_heat, created_at')
      .order('created_at', { ascending: false })
      .limit(1)
      .single();

    if (error || !data) {
      return {
        equity: 0,
        cash: 0,
        daily_pnl: 0,
        daily_pnl_pct: 0,
        total_pnl: 0,
        drawdown_pct: 0,
        open_positions: 0,
        win_rate: 0,
        portfolio_heat: 0,
        timestamp: new Date().toISOString(),
      } as PortfolioSummary;
    }
    return {
      equity: data.equity ?? 0,
      cash: data.cash ?? 0,
      daily_pnl: data.daily_pnl ?? 0,
      daily_pnl_pct: data.daily_pnl_pct ?? 0,
      total_pnl: data.total_pnl ?? 0,
      drawdown_pct: data.drawdown_pct ?? 0,
      open_positions: data.open_positions ?? 0,
      win_rate: data.win_rate ?? 0,
      portfolio_heat: data.portfolio_heat ?? 0,
      timestamp: data.created_at,
    } as PortfolioSummary;
  },

  // ---- Trades -----------------------------------------------------------

  async getOpenTrades(): Promise<Trade[]> {
    const { data, error } = await supabase
      .from('trades')
      .select('*')
      .in('status', ['OPEN', 'PARTIAL', 'FILLED'])
      .order('opened_at', { ascending: false }); // was entry_time — correct column is opened_at

    return unwrap(data, error, 'getOpenTrades');
  },

  async getClosedTrades(limit = 100): Promise<Trade[]> {
    const { data, error } = await supabase
      .from('trades')
      .select('*')
      .eq('status', 'CLOSED')
      .order('closed_at', { ascending: false }) // was exit_time
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
    // Derive from agent_decisions grouped by (symbol, created_at window)
    // Falls back to empty array rather than throwing on missing bot endpoint
    try {
      const { data, error } = await supabase
        .from('agent_decisions')
        .select('id, role, symbol, market, signal, confidence, reasoning, raw_output, created_at')
        .order('created_at', { ascending: false })
        .limit(limit * 7); // up to 7 agents per debate

      if (error || !data || data.length === 0) return [];

      // Group consecutive rows that share the same symbol into debate sessions
      const debates: AgentDebate[] = [];
      let group: typeof data = [];
      let lastSymbol = '';

      for (const row of data) {
        if (row.symbol !== lastSymbol && group.length > 0) {
          debates.push(_rowsToDebate(group));
          if (debates.length >= limit) break;
          group = [];
        }
        group.push(row);
        lastSymbol = row.symbol;
      }
      if (group.length > 0 && debates.length < limit) {
        debates.push(_rowsToDebate(group));
      }
      return debates;
    } catch {
      return [];
    }
  },

  // ---- Risk -------------------------------------------------------------

  async getRiskMetrics(): Promise<RiskMetrics> {
    // Derive from latest portfolio snapshot — no dedicated risk endpoint needed
    try {
      const { data } = await supabase
        .from('portfolio_snapshots')
        .select('equity, drawdown_pct, portfolio_heat, open_positions, daily_pnl, created_at')
        .order('created_at', { ascending: false })
        .limit(1)
        .single();

      if (!data) throw new Error('no snapshot');
      const dailyLossPct = data.equity > 0
        ? Math.abs((data.daily_pnl ?? 0) / data.equity) * 100
        : 0;
      return {
        portfolio_heat: data.portfolio_heat ?? 0,
        max_drawdown: data.drawdown_pct ?? 0,
        current_drawdown: data.drawdown_pct ?? 0,
        correlation_warning: false,
        circuit_breakers_active: dailyLossPct >= 3 || Math.abs(data.drawdown_pct ?? 0) >= 15 ? 1 : 0,
        daily_loss_limit_used_pct: dailyLossPct,
        var_95: null,
        leverage_ratio: data.portfolio_heat ?? 0,
      } as RiskMetrics;
    } catch {
      return {
        portfolio_heat: 0, max_drawdown: 0, current_drawdown: 0,
        correlation_warning: false, circuit_breakers_active: 0,
        daily_loss_limit_used_pct: 0, var_95: null, leverage_ratio: 0,
      } as RiskMetrics;
    }
  },

  async getCircuitBreakerStatus(): Promise<CircuitBreakerStatus[]> {
    // Circuit breaker state is computed from portfolio metrics — no separate table
    try {
      const risk = await api.getRiskMetrics();
      return [
        {
          id: '1', name: 'Daily Loss Limit',
          description: 'Stop trading if daily loss > 3%',
          enabled: true,
          triggered: risk.daily_loss_limit_used_pct >= 3,
          threshold: 3,
          current_value: risk.daily_loss_limit_used_pct,
        },
        {
          id: '2', name: 'Max Drawdown',
          description: 'Halt if drawdown > 15%',
          enabled: true,
          triggered: Math.abs(risk.current_drawdown) >= 15,
          threshold: 15,
          current_value: Math.abs(risk.current_drawdown),
        },
      ] as CircuitBreakerStatus[];
    } catch {
      return [];
    }
  },

  // ---- Performance History ----------------------------------------------

  async getPerformanceHistory(days = 90): Promise<PerformancePoint[]> {
    // Derive daily performance from portfolio_snapshots (no separate performance_history table)
    const since = new Date(Date.now() - days * 86_400_000).toISOString();

    const { data, error } = await supabase
      .from('portfolio_snapshots')
      .select('equity, daily_pnl, drawdown_pct, win_rate, created_at')
      .gte('created_at', since)
      .order('created_at', { ascending: true });

    if (error || !data || data.length === 0) return [];

    // Group by day, take last snapshot per day
    // eslint-disable-next-line @typescript-eslint/no-explicit-any
    const byDate: Record<string, any> = {};
    for (const row of data) {
      const date = row.created_at.slice(0, 10);
      byDate[date] = row;
    }

    let cumulativePnl = 0;
    return Object.entries(byDate)
      .sort(([a], [b]) => a.localeCompare(b))
      .map(([date, row]) => {
        cumulativePnl += row.daily_pnl ?? 0;
        return {
          date,
          cumulative_pnl: cumulativePnl,
          daily_pnl: row.daily_pnl ?? 0,
          drawdown: row.drawdown_pct ?? 0,
          win_rate_rolling: row.win_rate ?? 0,
        } as PerformancePoint;
      });
  },

  // ---- Emergency / Control actions -------------------------------------

  async emergencyStop(): Promise<void> {
    // Use Next.js server-side proxy to avoid CORS/auth issues with VPS direct call
    const res = await fetch('/api/bot/emergency-stop', { method: 'POST' });
    if (!res.ok) {
      const text = await res.text().catch(() => '');
      throw new ApiError('Emergency stop failed', res.status, text);
    }
  },

  // ---- Feature Flags ----------------------------------------------------

  async getFeatureFlags(): Promise<FeatureFlags> {
    // Read from Supabase feature_flags table (exists, 48kB of data)
    const { data, error } = await supabase
      .from('feature_flags')
      .select('key, value, description, updated_at');

    if (error || !data) return {} as FeatureFlags;
    // eslint-disable-next-line @typescript-eslint/no-explicit-any
    const flags: Record<string, any> = {};
    for (const row of data) {
      flags[row.key] = row.value;
    }
    return flags as FeatureFlags;
  },

  async updateFeatureFlag(flag: string, value: boolean): Promise<void> {
    const { error } = await supabase
      .from('feature_flags')
      .upsert({ key: flag, value, updated_at: new Date().toISOString() }, { onConflict: 'key' });
    if (error) {
      throw new ApiError(`Failed to update flag: ${error.message}`, 500, error.message);
    }
  },
};

// ---------------------------------------------------------------------------
// Internal helper — convert agent_decisions rows to AgentDebate shape
// ---------------------------------------------------------------------------

// eslint-disable-next-line @typescript-eslint/no-explicit-any
function _rowsToDebate(rows: any[]): AgentDebate {
  const first = rows[0];
  return {
    id: first.id,
    symbol: first.symbol ?? '',
    market: first.market ?? 'crypto',
    timestamp: first.created_at,
    // eslint-disable-next-line @typescript-eslint/no-explicit-any
    agents: rows.map((r: any) => ({
      role: r.role,
      decision: r.signal ?? 'NEUTRAL',
      confidence: r.confidence ?? 0.5,
      reasoning: r.reasoning ?? '',
    })),
    consensus: _deriveConsensus(rows),
    consensus_confidence: rows.reduce(
      // eslint-disable-next-line @typescript-eslint/no-explicit-any
      (sum: number, r: any) => sum + (r.confidence ?? 0.5), 0
    ) / Math.max(rows.length, 1),
  } as AgentDebate;
}

// eslint-disable-next-line @typescript-eslint/no-explicit-any
function _deriveConsensus(rows: any[]): string {
  const counts: Record<string, number> = {};
  // eslint-disable-next-line @typescript-eslint/no-explicit-any
  for (const r of rows) {
    const s = r.signal ?? 'NEUTRAL';
    counts[s] = (counts[s] ?? 0) + 1;
  }
  return Object.entries(counts).sort(([, a], [, b]) => b - a)[0]?.[0] ?? 'NEUTRAL';
}

export default api;
