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
    return getMockSignals(limit);
  }
  return data ?? [];
}

export async function getActiveTrades(): Promise<Trade[]> {
  const { data, error } = await supabase
    .from('trades')
    .select('*')
    .eq('status', 'OPEN')
    .order('entry_time', { ascending: false });

  if (error) {
    console.error('[Supabase] getActiveTrades error:', error);
    return getMockTrades();
  }
  return data ?? [];
}

export async function getRecentTrades(limit: number = 50): Promise<Trade[]> {
  const { data, error } = await supabase
    .from('trades')
    .select('*')
    .order('created_at', { ascending: false })
    .limit(limit);

  if (error) {
    console.error('[Supabase] getRecentTrades error:', error);
    return getMockTrades();
  }
  return data ?? [];
}

export async function getPerformanceMetrics(): Promise<PerformanceMetrics | null> {
  const { data, error } = await supabase
    .from('performance_metrics')
    .select('*')
    .order('period_end', { ascending: false })
    .limit(1)
    .single();

  if (error) {
    console.error('[Supabase] getPerformanceMetrics error:', error);
    return getMockPerformanceMetrics();
  }
  return data;
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
    return getMockAgentDecisions();
  }
  return data ?? [];
}

export async function getBrierScores(): Promise<BrierScore[]> {
  const { data, error } = await supabase
    .from('brier_scores')
    .select('*')
    .order('period', { ascending: false })
    .limit(35); // 5 agents × 7 markets

  if (error) {
    console.error('[Supabase] getBrierScores error:', error);
    return getMockBrierScores();
  }
  return data ?? [];
}

export async function getLatestPortfolioSnapshot(): Promise<PortfolioSnapshot | null> {
  const { data, error } = await supabase
    .from('portfolio_snapshots')
    .select('*')
    .order('timestamp', { ascending: false })
    .limit(1)
    .single();

  if (error) {
    return getMockPortfolioSnapshot();
  }
  return data;
}

export async function getEquityCurve(
  days: number = 30
): Promise<{ time: string; value: number }[]> {
  const since = new Date();
  since.setDate(since.getDate() - days);

  const { data, error } = await supabase
    .from('portfolio_snapshots')
    .select('timestamp, equity')
    .gte('timestamp', since.toISOString())
    .order('timestamp', { ascending: true });

  if (error || !data) {
    return getMockEquityCurve(days);
  }

  return data.map((d) => ({ time: d.timestamp, value: d.equity }));
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
      symbol: 'BTCUSDT',
      market: 'crypto',
      direction: 'LONG',
      status: 'OPEN',
      entry_price: 67500,
      stop_loss: 65000,
      take_profit: 72000,
      size: 0.15,
      unrealized_pnl: 450,
      unrealized_pnl_pct: 2.1,
      entry_time: new Date(Date.now() - 7200000).toISOString(),
      strategy: 'momentum_breakout',
      created_at: new Date(Date.now() - 7200000).toISOString(),
      updated_at: new Date().toISOString(),
    },
    {
      id: 'trade-2',
      symbol: 'EURUSD',
      market: 'forex',
      direction: 'SHORT',
      status: 'CLOSED',
      entry_price: 1.0920,
      exit_price: 1.0875,
      stop_loss: 1.0960,
      take_profit: 1.0850,
      size: 100000,
      pnl: 450,
      pnl_pct: 0.41,
      entry_time: new Date(Date.now() - 86400000).toISOString(),
      exit_time: new Date(Date.now() - 3600000).toISOString(),
      duration_minutes: 1380,
      strategy: 'session_breakout',
      created_at: new Date(Date.now() - 86400000).toISOString(),
      updated_at: new Date(Date.now() - 3600000).toISOString(),
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
  return {
    id: 'snap-1',
    timestamp: new Date().toISOString(),
    equity: 127840,
    cash: 64200,
    unrealized_pnl: 3420,
    realized_pnl_today: 1240,
    drawdown_pct: -3.2,
    max_drawdown_pct: -8.4,
    open_positions: 4,
    exposure_pct: 49.7,
    created_at: new Date().toISOString(),
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
