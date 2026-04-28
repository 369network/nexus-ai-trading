// ============================================================
// NEXUS ALPHA - Supabase Client + Realtime Subscriptions
// ============================================================

import { createClient as createSupabaseClient, SupabaseClient, RealtimeChannel } from '@supabase/supabase-js';
import type {
  Signal,
  Trade,
  RiskEvent,
  PortfolioSnapshot,
  AgentDecision,
  MarketData,
  PerformanceMetrics,
  BrierScore,
  Market,
  AgentRole,
} from './types';

// ---- Singleton client ----
let supabaseInstance: SupabaseClient | null = null;

export function createClient(): SupabaseClient {
  if (supabaseInstance) return supabaseInstance;

  const url = process.env.NEXT_PUBLIC_SUPABASE_URL;
  const key = process.env.NEXT_PUBLIC_SUPABASE_ANON_KEY;

  if (!url || !key) {
    console.warn('[Supabase] Missing env vars — using mock client');
    // Return a mock-compatible client for development
  }

  supabaseInstance = createSupabaseClient(
    url ?? 'https://placeholder.supabase.co',
    key ?? 'placeholder-key',
    {
      realtime: {
        params: {
          eventsPerSecond: 20,
        },
      },
      auth: {
        persistSession: false,
        autoRefreshToken: true,
      },
    }
  );

  return supabaseInstance;
}

export const supabase = createClient();

// ============================================================
// REALTIME SUBSCRIPTIONS
// ============================================================

export function subscribeToSignals(
  callback: (signal: Signal) => void
): RealtimeChannel {
  const channel = supabase
    .channel('signals-live')
    .on(
      'postgres_changes',
      {
        event: 'INSERT',
        schema: 'public',
        table: 'signals',
      },
      (payload) => {
        callback(payload.new as Signal);
      }
    )
    .subscribe((status) => {
      if (status === 'SUBSCRIBED') {
        console.log('[Supabase] Subscribed to signals');
      }
    });

  return channel;
}

export function subscribeToTrades(
  callback: (trade: Trade, eventType: 'INSERT' | 'UPDATE') => void
): RealtimeChannel {
  const channel = supabase
    .channel('trades-live')
    .on(
      'postgres_changes',
      {
        event: 'INSERT',
        schema: 'public',
        table: 'trades',
      },
      (payload) => {
        callback(payload.new as Trade, 'INSERT');
      }
    )
    .on(
      'postgres_changes',
      {
        event: 'UPDATE',
        schema: 'public',
        table: 'trades',
      },
      (payload) => {
        callback(payload.new as Trade, 'UPDATE');
      }
    )
    .subscribe((status) => {
      if (status === 'SUBSCRIBED') {
        console.log('[Supabase] Subscribed to trades');
      }
    });

  return channel;
}

export function subscribeToRiskEvents(
  callback: (event: RiskEvent) => void
): RealtimeChannel {
  const channel = supabase
    .channel('risk-events-live')
    .on(
      'postgres_changes',
      {
        event: 'INSERT',
        schema: 'public',
        table: 'risk_events',
      },
      (payload) => {
        callback(payload.new as RiskEvent);
      }
    )
    .subscribe((status) => {
      if (status === 'SUBSCRIBED') {
        console.log('[Supabase] Subscribed to risk events');
      }
    });

  return channel;
}

export function subscribeToPortfolioSnapshots(
  callback: (snapshot: PortfolioSnapshot) => void
): RealtimeChannel {
  const channel = supabase
    .channel('portfolio-live')
    .on(
      'postgres_changes',
      {
        event: 'INSERT',
        schema: 'public',
        table: 'portfolio_snapshots',
      },
      (payload) => {
        callback(payload.new as PortfolioSnapshot);
      }
    )
    .subscribe((status) => {
      if (status === 'SUBSCRIBED') {
        console.log('[Supabase] Subscribed to portfolio snapshots');
      }
    });

  return channel;
}

export function subscribeToAgentDecisions(
  callback: (decision: AgentDecision) => void
): RealtimeChannel {
  const channel = supabase
    .channel('agent-decisions-live')
    .on(
      'postgres_changes',
      {
        event: 'INSERT',
        schema: 'public',
        table: 'agent_decisions',
      },
      (payload) => {
        callback(payload.new as AgentDecision);
      }
    )
    .subscribe((status) => {
      if (status === 'SUBSCRIBED') {
        console.log('[Supabase] Subscribed to agent decisions');
      }
    });

  return channel;
}

export function subscribeToMarketData(
  symbol: string,
  callback: (data: MarketData) => void
): RealtimeChannel {
  const channel = supabase
    .channel(`market-data-${symbol}`)
    .on(
      'postgres_changes',
      {
        event: 'INSERT',
        schema: 'public',
        table: 'market_data',
        filter: `symbol=eq.${symbol}`,
      },
      (payload) => {
        callback(payload.new as MarketData);
      }
    )
    .subscribe();

  return channel;
}

// ============================================================
// QUERIES
// ============================================================

// Map DB direction (BUY/SELL/NEUTRAL) to dashboard Direction (LONG/SHORT/NEUTRAL)
function mapDbDirection(dir: string): 'LONG' | 'SHORT' | 'NEUTRAL' {
  if (dir === 'BUY') return 'LONG';
  if (dir === 'SELL') return 'SHORT';
  return 'NEUTRAL';
}

// Map DB signal row to dashboard Signal shape
// eslint-disable-next-line @typescript-eslint/no-explicit-any
function mapSignalRow(row: any): Signal {
  const raw = (row.raw_data ?? {}) as Record<string, unknown>;
  return {
    ...row,
    direction: mapDbDirection(row.direction),
    strength: row.score ?? 50,
    entry: (raw.entry_price as number) ?? 0,
    stop_loss: (raw.stop_loss as number) ?? 0,
    tp1: (raw.take_profit_1 as number) ?? 0,
    tp2: (raw.take_profit_2 as number) ?? 0,
    tp3: (raw.take_profit_3 as number) ?? 0,
    risk_reward: (raw.risk_reward as number) ?? row.expected_value ?? 0,
    reasoning: (raw.reasoning as string) ?? '',
    position_size: (raw.size_pct as number) ?? 0,
    agent_votes: (raw.agent_votes as Signal['agent_votes']) ?? {
      bull: 3, bear: 1, fundamental: 2, technical: 3, sentiment: 2,
    },
  } as Signal;
}

// Map DB agent_decision row to dashboard AgentDecision shape
// eslint-disable-next-line @typescript-eslint/no-explicit-any
function mapAgentDecisionRow(row: any): AgentDecision {
  const raw = (row.raw_output ?? {}) as Record<string, unknown>;
  return {
    ...row,
    decision: mapDbDirection(row.signal ?? 'NEUTRAL'),
    reasoning: row.reasoning ?? (raw.reasoning as string) ?? '',
    symbol: (raw.symbol as string) ?? row.symbol ?? '',
    market: (raw.market as Market) ?? 'crypto',
    key_factors: (raw.key_factors as string[]) ?? [],
    data_sources: (raw.data_sources as string[]) ?? [],
  } as AgentDecision;
}

export async function getRecentSignals(
  market?: Market,
  limit: number = 100
): Promise<Signal[]> {
  let query = supabase
    .from('signals')
    .select('*')
    .order('created_at', { ascending: false })
    .limit(limit);

  if (market) {
    query = query.eq('market', market);
  }

  const { data, error } = await query;
  if (error) {
    console.error('[Supabase] getRecentSignals error:', error);
    return [];
  }
  if (!data || data.length === 0) return [];
  // Filter out test rows and map to dashboard shape
  return data
    .filter((r) => r.symbol !== 'TEST')
    .map(mapSignalRow);
}

export async function getActiveTrades(): Promise<Trade[]> {
  const { data, error } = await supabase
    .from('trades')
    .select('*')
    .in('status', ['OPEN', 'PARTIAL', 'FILLED'])
    .order('opened_at', { ascending: false });

  if (error) {
    console.error('[Supabase] getActiveTrades error:', error);
    return [];
  }
  return data ?? [];
}

export async function getRecentTrades(limit: number = 50): Promise<Trade[]> {
  const { data, error } = await supabase
    .from('trades')
    .select('*')
    .order('opened_at', { ascending: false })
    .limit(limit);

  if (error) {
    console.error('[Supabase] getRecentTrades error:', error);
    return [];
  }
  return data ?? [];
}

export async function getPerformanceMetrics(): Promise<PerformanceMetrics | null> {
  // Compute performance metrics from portfolio_snapshots + trades.
  // (No separate performance_metrics table exists — metrics are derived live.)
  const [snapshotsRes, tradesRes] = await Promise.all([
    supabase
      .from('portfolio_snapshots')
      .select('equity, daily_pnl, total_pnl, drawdown_pct, win_rate, open_positions, portfolio_heat, created_at')
      .order('created_at', { ascending: true }),
    supabase
      .from('trades')
      .select('commission, fees_paid, pnl, pnl_pct, status, opened_at, closed_at, entry_price, quantity, side')
      .order('opened_at', { ascending: true }),
  ]);

  // eslint-disable-next-line @typescript-eslint/no-explicit-any
  const snapshots: any[] = (snapshotsRes.data ?? []).filter(
    // eslint-disable-next-line @typescript-eslint/no-explicit-any
    (s: any) => s.equity > 0 && s.equity < 10_000_000
  );
  // eslint-disable-next-line @typescript-eslint/no-explicit-any
  const trades: any[] = tradesRes.data ?? [];

  if (snapshots.length === 0) return null;

  // eslint-disable-next-line @typescript-eslint/no-explicit-any
  const latest: any = snapshots[snapshots.length - 1];
  // eslint-disable-next-line @typescript-eslint/no-explicit-any
  const first: any = snapshots[0];

  // Total PnL: bot stores equity − initial_capital in total_pnl column
  const total_pnl: number = latest.total_pnl ?? (latest.equity - first.equity);

  // Max drawdown: bot stores as negative % (e.g. −7.78 means 7.78% drawdown)
  const allDrawdowns: number[] = snapshots.map((s) => s.drawdown_pct ?? 0);
  const max_drawdown = allDrawdowns.length > 0 ? Math.min(...allDrawdowns) : 0;

  // Total commission
  // eslint-disable-next-line @typescript-eslint/no-explicit-any
  const total_commission = trades.reduce(
    // eslint-disable-next-line @typescript-eslint/no-explicit-any
    (sum: number, t: any) => sum + parseFloat(t.commission ?? t.fees_paid ?? 0),
    0
  );

  // Closed trades with PnL (populated after bot fix)
  // eslint-disable-next-line @typescript-eslint/no-explicit-any
  const closedTrades = trades.filter((t: any) => t.status === 'CLOSED' && t.pnl != null);
  // Rough trade count: each round-trip = 1 open + 1 close row; closed trades are more reliable
  const total_trades = closedTrades.length > 0
    ? closedTrades.length
    : Math.max(Math.floor(trades.length / 2), 0);

  // eslint-disable-next-line @typescript-eslint/no-explicit-any
  const winners = closedTrades.filter((t: any) => parseFloat(t.pnl ?? 0) > 0);
  // eslint-disable-next-line @typescript-eslint/no-explicit-any
  const losers  = closedTrades.filter((t: any) => parseFloat(t.pnl ?? 0) <= 0);
  const winning_trades = winners.length;
  const losing_trades  = losers.length;

  // eslint-disable-next-line @typescript-eslint/no-explicit-any
  const avg_win = winners.length > 0
    // eslint-disable-next-line @typescript-eslint/no-explicit-any
    ? winners.reduce((s: number, t: any) => s + parseFloat(t.pnl ?? 0), 0) / winners.length
    : 0;
  // eslint-disable-next-line @typescript-eslint/no-explicit-any
  const avg_loss = losers.length > 0
    // eslint-disable-next-line @typescript-eslint/no-explicit-any
    ? losers.reduce((s: number, t: any) => s + parseFloat(t.pnl ?? 0), 0) / losers.length
    : 0;

  // eslint-disable-next-line @typescript-eslint/no-explicit-any
  const gross_profit = winners.reduce((s: number, t: any) => s + parseFloat(t.pnl ?? 0), 0);
  // eslint-disable-next-line @typescript-eslint/no-explicit-any
  const gross_loss   = Math.abs(losers.reduce((s: number, t: any) => s + parseFloat(t.pnl ?? 0), 0));
  const profit_factor = gross_loss > 0 ? gross_profit / gross_loss : 0;

  // Win rate: prefer actual closed-trade ratio; fall back to snapshot's running win_rate
  const effective_win_rate = total_trades > 0 && closedTrades.length > 0
    ? winning_trades / total_trades
    : (latest.win_rate ?? 0);

  const expectancy = effective_win_rate * avg_win + (1 - effective_win_rate) * avg_loss;

  // Average trade duration
  const durationsMin = closedTrades
    // eslint-disable-next-line @typescript-eslint/no-explicit-any
    .filter((t: any) => t.opened_at && t.closed_at)
    // eslint-disable-next-line @typescript-eslint/no-explicit-any
    .map((t: any) =>
      (new Date(t.closed_at).getTime() - new Date(t.opened_at).getTime()) / 60_000
    )
    .filter((d: number) => d > 0);
  const avg_trade_duration_minutes = durationsMin.length > 0
    ? durationsMin.reduce((a: number, b: number) => a + b, 0) / durationsMin.length
    : 0;

  // Sharpe / Sortino / Calmar from equity curve returns
  let sharpe_ratio = 0, sortino_ratio = 0, calmar_ratio = 0;
  if (snapshots.length >= 3) {
    const equities: number[] = snapshots.map((s) => s.equity);
    const returns = equities.slice(1).map((eq, i) => (eq - equities[i]) / equities[i]);
    const n = returns.length;
    const avgR = returns.reduce((a, b) => a + b, 0) / n;
    const variance = returns.reduce((a, b) => a + (b - avgR) ** 2, 0) / n;
    const stdR = Math.sqrt(variance);
    const downReturns = returns.filter(r => r < 0);
    const downStd = downReturns.length > 0
      ? Math.sqrt(downReturns.reduce((a, b) => a + b ** 2, 0) / downReturns.length)
      : 0;
    const annFactor = Math.sqrt(252);
    sharpe_ratio  = stdR > 0   ? parseFloat(((avgR / stdR)   * annFactor).toFixed(3)) : 0;
    sortino_ratio = downStd > 0 ? parseFloat(((avgR / downStd) * annFactor).toFixed(3)) : 0;
    const annReturn = avgR * 252;
    calmar_ratio = max_drawdown < 0
      ? parseFloat(Math.abs(annReturn / max_drawdown).toFixed(3))
      : 0;
  }

  return {
    total_trades,
    winning_trades,
    losing_trades,
    win_rate: effective_win_rate,
    avg_win,
    avg_loss,
    profit_factor,
    sharpe_ratio,
    sortino_ratio,
    calmar_ratio,
    max_drawdown,
    avg_trade_duration_minutes,
    total_pnl,
    total_commission,
    expectancy,
    period_start: first.created_at,
    period_end:   latest.created_at,
  };
}

export async function getAgentHistory(
  role?: AgentRole,
  limit: number = 20
): Promise<AgentDecision[]> {
  let query = supabase
    .from('agent_decisions')
    .select('*')
    .order('created_at', { ascending: false })
    .limit(limit);

  if (role) {
    query = query.eq('role', role);
  }

  const { data, error } = await query;
  if (error) {
    console.error('[Supabase] getAgentHistory error:', error);
    return [];
  }
  if (!data || data.length === 0) return [];
  return data.map(mapAgentDecisionRow);
}

export async function getBrierScores(): Promise<BrierScore[]> {
  const { data, error } = await supabase
    .from('brier_scores')
    .select('*')
    .order('period', { ascending: false })
    .limit(35); // 5 agents × 7 markets

  if (error) {
    console.error('[Supabase] getBrierScores error:', error);
    return [];
  }
  return data ?? [];
}

export async function getLatestPortfolioSnapshot(): Promise<PortfolioSnapshot | null> {
  const { data, error } = await supabase
    .from('portfolio_snapshots')
    .select('*')
    .order('created_at', { ascending: false })
    .limit(1)
    .single();

  if (error || !data) return null;

  // Normalise to the shape expected by the store's updatePortfolio()
  return {
    ...data,
    timestamp: data.created_at,
    realized_pnl_today: data.daily_pnl ?? 0,
    unrealized_pnl: 0,
    max_drawdown_pct: data.drawdown_pct ?? 0,
    exposure_pct: (data.portfolio_heat ?? 0) * 100,
  } as PortfolioSnapshot;
}

export async function getEquityCurve(
  days: number = 30
): Promise<{ time: string; value: number }[]> {
  const since = new Date();
  since.setDate(since.getDate() - days);

  const { data, error } = await supabase
    .from('portfolio_snapshots')
    .select('created_at, equity')
    .gte('created_at', since.toISOString())
    .order('created_at', { ascending: true });

  if (error || !data) {
    console.error('[Supabase] getEquityCurve error:', error);
    return [];
  }

  // Filter out obviously invalid rows (test inserts, equity = 0 or unrealistically high)
  return data
    .filter((d) => d.equity > 0 && d.equity < 10_000_000)
    .map((d) => ({ time: d.created_at, value: d.equity }));
}

export async function getMarketData(
  symbol: string,
  timeframe: string,
  limit: number = 200
): Promise<MarketData[]> {
  const { data, error } = await supabase
    .from('market_data')
    .select('*')
    .eq('symbol', symbol)
    .eq('timeframe', timeframe)
    .order('timestamp', { ascending: true })
    .limit(limit);

  if (error || !data) {
    return [];
  }

  return data;
}

// ============================================================
// MOCK DATA FOR DEVELOPMENT (when Supabase isn't configured)
// ============================================================

function getMockSignals(limit: number = 10): Signal[] {
  const symbols = ['BTCUSDT', 'ETHUSDT', 'EURUSD', 'XAUUSD', 'NIFTY50'];
  const markets: Market[] = ['crypto', 'crypto', 'forex', 'commodities', 'indian_stocks'];
  const directions = ['LONG', 'SHORT', 'NEUTRAL'] as const;

  return Array.from({ length: Math.min(limit, 10) }, (_, i) => ({
    id: `sig-${i}`,
    symbol: symbols[i % symbols.length],
    market: markets[i % markets.length],
    direction: directions[i % 3],
    strength: 60 + Math.random() * 40,
    confidence: 0.6 + Math.random() * 0.35,
    entry: 45000 + Math.random() * 1000,
    stop_loss: 44000,
    tp1: 46000,
    tp2: 47000,
    tp3: 48000,
    agent_votes: { bull: 3, bear: 1, fundamental: 2, technical: 3, sentiment: 2 },
    reasoning: 'Strong momentum with bullish divergence on RSI. Volume surge confirms breakout.',
    risk_reward: 2.5,
    position_size: 0.05,
    created_at: new Date(Date.now() - i * 300000).toISOString(),
  }));
}

function getMockTrades(): Trade[] {
  return [
    {
      id: 'trade-1',
      symbol: 'BTC/USDT',
      market: 'crypto',
      direction: 'LONG',
      status: 'OPEN',
      entry_price: 67500,
      stop_loss: 65000,
      take_profit_1: 72000,
      quantity: 0.15,
      unrealized_pnl: 450,
      unrealized_pnl_pct: 2.1,
      entry_time: new Date(Date.now() - 7200000).toISOString(),
      opened_at: new Date(Date.now() - 7200000).toISOString(),
      strategy: 'momentum_breakout',
    },
    {
      id: 'trade-2',
      symbol: 'ETH/USDT',
      market: 'crypto',
      direction: 'SHORT',
      status: 'CLOSED',
      entry_price: 3200,
      exit_price: 3150,
      stop_loss: 3280,
      take_profit_1: 3100,
      quantity: 1.5,
      pnl: 75,
      pnl_pct: 1.56,
      entry_time: new Date(Date.now() - 86400000).toISOString(),
      opened_at: new Date(Date.now() - 86400000).toISOString(),
      closed_at: new Date(Date.now() - 3600000).toISOString(),
      duration_minutes: 1380,
      strategy: 'trend_following',
    },
  ];
}

function getMockPerformanceMetrics(): PerformanceMetrics {
  return {
    total_trades: 247,
    winning_trades: 158,
    losing_trades: 89,
    win_rate: 0.6397,
    avg_win: 312,
    avg_loss: -187,
    profit_factor: 2.64,
    sharpe_ratio: 1.87,
    sortino_ratio: 2.43,
    calmar_ratio: 1.21,
    max_drawdown: -8.4,
    avg_trade_duration_minutes: 187,
    total_pnl: 18420,
    total_commission: 1240,
    expectancy: 148,
    period_start: new Date(Date.now() - 90 * 86400000).toISOString(),
    period_end: new Date().toISOString(),
  };
}

function getMockAgentDecisions(): AgentDecision[] {
  const roles: AgentRole[] = ['bull', 'bear', 'fundamental', 'technical', 'sentiment', 'risk', 'portfolio'];
  return roles.map((role, i) => ({
    id: `dec-${i}`,
    role,
    symbol: 'BTCUSDT',
    market: 'crypto' as Market,
    decision: i % 3 === 0 ? 'SHORT' : 'LONG',
    confidence: 0.65 + Math.random() * 0.3,
    reasoning: `${role} analysis indicates strong momentum with key support at 66k. Volume pattern confirms institutional accumulation.`,
    key_factors: ['RSI oversold', 'Volume surge', 'Support level hold'],
    data_sources: ['binance', 'coingecko', 'glassnode'],
    brier_score: 0.15 + Math.random() * 0.2,
    created_at: new Date(Date.now() - i * 60000).toISOString(),
  }));
}

function getMockBrierScores(): BrierScore[] {
  const agents: AgentRole[] = ['bull', 'bear', 'fundamental', 'technical', 'sentiment'];
  const markets: Market[] = ['crypto', 'forex', 'commodities', 'indian_stocks', 'us_stocks'];

  return agents.flatMap((agent) =>
    markets.map((market) => ({
      agent,
      market,
      score: 0.1 + Math.random() * 0.3,
      trades_evaluated: Math.floor(20 + Math.random() * 80),
      period: '2024-Q4',
    }))
  );
}

function getMockPortfolioSnapshot(): PortfolioSnapshot {
  // Returns zeros so the dashboard shows "—" until real data arrives.
  // Never show fake equity/P&L numbers.
  return {
    id: 'snap-loading',
    created_at: new Date().toISOString(),
    timestamp: new Date().toISOString(),
    equity: 0,
    cash: 0,
    daily_pnl: 0,
    daily_pnl_pct: 0,
    total_pnl: 0,
    drawdown_pct: 0,
    open_positions: 0,
  };
}

function getMockEquityCurve(days: number): { time: string; value: number }[] {
  const points: { time: string; value: number }[] = [];
  let equity = 100000;
  const now = Date.now();

  for (let i = days; i >= 0; i--) {
    equity = equity * (1 + (Math.random() - 0.45) * 0.015);
    points.push({
      time: new Date(now - i * 86400000).toISOString(),
      value: Math.round(equity * 100) / 100,
    });
  }

  return points;
}
