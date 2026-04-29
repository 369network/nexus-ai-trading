// ============================================================
// NEXUS ALPHA - Core TypeScript Interfaces
// Matches Supabase schema exactly
// ============================================================

export type Market = 'crypto' | 'forex' | 'commodities' | 'indian_stocks' | 'us_stocks';
export type Direction = 'LONG' | 'SHORT' | 'NEUTRAL';
export type TradeStatus = 'OPEN' | 'CLOSED' | 'PARTIAL' | 'STOPPED_OUT' | 'TAKE_PROFIT' | 'CANCELLED';
export type Severity = 'LOW' | 'MEDIUM' | 'HIGH' | 'CRITICAL';
export type AgentRole = 'bull' | 'bear' | 'fundamental' | 'technical' | 'sentiment' | 'risk' | 'portfolio';
export type Timeframe = '1m' | '5m' | '15m' | '1h' | '4h' | '1d';

// ---- Market Data ----
export interface MarketData {
  id: string;
  symbol: string;
  market: Market;
  timestamp: string;
  open: number;
  high: number;
  low: number;
  close: number;
  volume: number;
  timeframe: Timeframe;
  vwap?: number;
  atr?: number;
  rsi?: number;
  created_at: string;
}

// ---- Agent Votes ----
export interface AgentVotes {
  bull: number;
  bear: number;
  fundamental: number;
  technical: number;
  sentiment: number;
}

// ---- Signal ----
// Matches actual Supabase `signals` table schema.
// Extra display fields (entry, stop_loss, tp1, agent_votes, reasoning, risk_reward)
// are stored in raw_data JSONB and promoted to top-level by getRecentSignals().
export interface Signal {
  id: string;
  symbol: string;
  market: Market;
  direction: Direction;      // dashboard uses LONG/SHORT/NEUTRAL (mapped from BUY/SELL/NEUTRAL in DB)
  strength: number;          // 0-100 (mapped from DB `score` column)
  confidence: number;        // 0-1
  // DB columns
  score?: number;            // raw DB column (= strength)
  strategy?: string;
  llm_weight?: number;
  technical_weight?: number;
  sentiment_weight?: number;
  onchain_weight?: number;
  expected_value?: number;
  is_executed?: boolean;
  raw_data?: Record<string, unknown>;
  // Extra display fields (extracted from raw_data by query layer)
  entry?: number;
  stop_loss?: number;
  tp1?: number;
  tp2?: number;
  tp3?: number;
  agent_votes?: AgentVotes;
  reasoning?: string;
  risk_reward?: number;
  position_size?: number;
  created_at: string;
  expires_at?: string;
  metadata?: Record<string, unknown>;
}

// ---- Trade ----
export interface Trade {
  id: string;
  symbol: string;
  market: Market;
  direction: Direction;
  side?: Direction;          // DB alias for direction
  status: TradeStatus;
  entry_price: number;
  exit_price?: number;
  stop_loss: number;
  take_profit_1: number;    // DB column name (was take_profit)
  take_profit_2?: number;
  take_profit_3?: number;
  quantity: number;          // DB column name (was size)
  quantity_filled?: number;
  position_value?: number;
  pnl?: number;              // realized P&L in USD
  pnl_pct?: number;          // realized P&L %
  unrealized_pnl?: number;
  unrealized_pnl_pct?: number;
  entry_time: string;
  opened_at: string;         // DB column name for entry timestamp
  closed_at?: string;
  exit_time?: string;
  duration_minutes?: number;
  strategy?: string;
  signal_id?: string;
  commission?: number;
  fees_paid?: number;
  slippage?: number;
  slippage_pct?: number;
  execution_mode?: string;
  exchange?: string;
  agent_confidence?: number;
  meta?: Record<string, unknown>;
  trade_metadata?: Record<string, unknown>;
  client_order_id?: string;
  exchange_order_id?: string;
}

// ---- Agent Decision ----
// Matches actual Supabase `agent_decisions` table schema.
// The `signal` column stores BUY/SELL/NEUTRAL; mapped to `decision` (LONG/SHORT/NEUTRAL) by query layer.
export interface AgentDecision {
  id: string;
  role: AgentRole;
  // DB columns
  signal?: string;           // raw DB column: BUY/SELL/NEUTRAL
  debate_id?: string;
  raw_output?: Record<string, unknown>;
  latency_ms?: number;
  // Display fields (mapped from DB or extracted from raw_output)
  decision: Direction;       // mapped from signal: BUY→LONG, SELL→SHORT
  confidence: number;        // 0-1
  reasoning: string;
  symbol?: string;           // extracted from raw_output
  market?: Market;           // extracted from raw_output
  key_factors?: string[];
  data_sources?: string[];
  brier_score?: number;
  created_at: string;
}

// ---- Risk Event ----
export interface RiskEvent {
  id: string;
  type: string;
  description: string;
  severity: Severity;
  market: Market;
  symbol?: string;
  circuit_breaker_triggered: boolean;
  layer: number;             // 1-5 risk layers
  action_taken?: string;
  created_at: string;
}

// ---- Portfolio Snapshot ----
export interface PortfolioSnapshot {
  id: string;
  // Actual DB columns
  equity: number;
  cash: number;
  positions_value?: number;
  daily_pnl?: number;
  daily_pnl_pct?: number;
  total_pnl?: number;
  drawdown_pct: number;
  open_positions: number;
  win_rate?: number;
  portfolio_heat?: number;
  market_exposure?: Record<string, number>;
  created_at: string;
  // Legacy aliases (kept for backward compat with store updatePortfolio)
  timestamp?: string;
  unrealized_pnl?: number;
  realized_pnl_today?: number;
  max_drawdown_pct?: number;
  exposure_pct?: number;
  margin_used?: number;
}

// ---- Performance Metrics ----
export interface PerformanceMetrics {
  total_trades: number;
  winning_trades: number;
  losing_trades: number;
  win_rate: number;
  avg_win: number;
  avg_loss: number;
  profit_factor: number;
  sharpe_ratio: number;
  sortino_ratio: number;
  calmar_ratio: number;
  max_drawdown: number;
  avg_trade_duration_minutes: number;
  total_pnl: number;
  total_commission: number;
  expectancy: number;
  period_start: string;
  period_end: string;
}

// ---- Circuit Breaker ----
export interface CircuitBreaker {
  id: string;
  name: string;
  description: string;
  enabled: boolean;
  triggered: boolean;
  last_trigger_time?: string;
  threshold: number;
  current_value: number;
  market?: Market;
}

// ---- Risk Layer ----
export interface RiskLayer {
  layer: number;
  name: string;
  description: string;
  status: 'GREEN' | 'YELLOW' | 'RED';
  checks: RiskCheck[];
}

export interface RiskCheck {
  name: string;
  passed: boolean;
  current_value: number | string;
  threshold: number;
  message: string;
}

// ---- System Status ----
export interface SystemStatus {
  paper_mode: boolean;
  markets_active: {
    crypto: boolean;
    forex: boolean;
    commodities: boolean;
    indian_stocks: boolean;
    us_stocks: boolean;
  };
  circuit_breakers: {
    daily_loss_limit: boolean;
    max_drawdown: boolean;
    position_concentration: boolean;
    correlation_limit: boolean;
    volatility_spike: boolean;
    api_failure: boolean;
  };
  last_heartbeat: string;
  uptime_seconds: number;
  agent_count: number;
  active_trades: number;
}

// ---- Forex Session ----
export interface ForexSession {
  name: 'Sydney' | 'Tokyo' | 'London' | 'New York';
  open_utc: string;
  close_utc: string;
  active: boolean;
  pairs: string[];
}

// ---- Economic Event ----
export interface EconomicEvent {
  id: string;
  datetime: string;
  currency: string;
  title: string;
  impact: 'LOW' | 'MEDIUM' | 'HIGH';
  forecast?: string;
  previous?: string;
  actual?: string;
}

// ---- Whale Alert ----
export interface WhaleAlert {
  id: string;
  symbol: string;
  amount_usd: number;
  amount_tokens: number;
  from_exchange?: string;
  to_exchange?: string;
  transaction_type: 'EXCHANGE_DEPOSIT' | 'EXCHANGE_WITHDRAWAL' | 'TRANSFER' | 'MINT' | 'BURN';
  timestamp: string;
  hash: string;
}

// ---- Fear & Greed ----
export interface FearGreedIndex {
  value: number;
  classification: 'Extreme Fear' | 'Fear' | 'Neutral' | 'Greed' | 'Extreme Greed';
  timestamp: string;
  previous_value: number;
  previous_close: number;
  weekly_average: number;
  monthly_average: number;
}

// ---- COT Data ----
export interface COTData {
  symbol: string;
  report_date: string;
  commercial_long: number;
  commercial_short: number;
  non_commercial_long: number;
  non_commercial_short: number;
  net_non_commercial: number;
  signal: 'BULLISH' | 'BEARISH' | 'NEUTRAL';
}

// ---- Option Chain Summary ----
export interface OptionChainSummary {
  symbol: string;
  expiry: string;
  pcr: number;              // Put-Call Ratio
  max_pain: number;
  call_oi_buildup: number[];
  put_oi_buildup: number[];
  strikes: number[];
  atm_iv: number;
}

// ---- FII/DII Flow ----
export interface FIIDIIFlow {
  date: string;
  fii_buy: number;
  fii_sell: number;
  fii_net: number;
  dii_buy: number;
  dii_sell: number;
  dii_net: number;
}

// ---- Strategy Performance ----
export interface StrategyPerformance {
  strategy: string;
  market: Market;
  total_trades: number;
  win_rate: number;
  total_pnl: number;
  avg_pnl_per_trade: number;
  sharpe: number;
  max_drawdown: number;
}

// ---- Brier Score ----
export interface BrierScore {
  agent: AgentRole;
  market: Market;
  score: number;           // 0-1, lower is better
  trades_evaluated: number;
  period: string;
}

// ---- Monthly PnL ----
export interface MonthlyPnL {
  year: number;
  month: number;
  pnl: number;
  pnl_pct: number;
  trades: number;
}

// ---- Agent Debate (full debate record with per-agent votes) ----
export interface AgentDebate {
  id: string;
  symbol: string;
  market: Market;
  agents: AgentDecision[];
  final_decision: Direction;
  consensus_score: number;   // 0–1, how aligned the agents were
  timestamp: string;         // ISO-8601
}

// ---- Portfolio Summary (dashboard top-level card) ----
export interface PortfolioSummary {
  total_value: number;
  daily_pnl: number;
  daily_pnl_pct: number;
  total_pnl: number;
  win_rate: number;           // 0–100 percentage
  total_trades: number;
  open_positions: number;
  market_exposure: Record<Market, number>; // market → USD notional
}

// ---- Risk Metrics (consolidated risk dashboard data) ----
export interface RiskMetrics {
  portfolio_heat: number;              // 0–1 fraction of capital at risk
  max_drawdown: number;                // worst historical drawdown %
  current_drawdown: number;            // current drawdown from peak %
  correlation_warning: boolean;        // true when cross-market correlation is high
  circuit_breakers_active: number;     // count of active circuit breakers
  daily_loss_limit_used_pct: number;   // 0–100, how much of daily loss limit is consumed
  var_95: number | null;               // Value at Risk at 95% confidence
  leverage_ratio: number;              // total notional / net asset value
}

// ---- Circuit Breaker Status ----
export interface CircuitBreakerStatus {
  name: string;
  is_active: boolean;
  triggered_at: string | null;    // ISO-8601, null when not active
  auto_reset_at: string | null;   // ISO-8601, null when manual reset required
  reason: string | null;
}

// ---- Performance History Point ----
export interface PerformancePoint {
  date: string;                // ISO-8601 date (YYYY-MM-DD)
  cumulative_pnl: number;
  daily_pnl: number;
  drawdown: number;            // negative value representing drawdown %
  win_rate_rolling: number;    // rolling 20-trade win rate, 0–100
}

// ---- Feature Flags (mirrors feature_flags.yaml) ----
export interface FeatureFlags {
  // Trading modes
  live_trading_enabled: boolean;
  paper_trading_enabled: boolean;

  // Markets
  crypto_enabled: boolean;
  forex_enabled: boolean;
  commodities_enabled: boolean;
  indian_stocks_enabled: boolean;
  us_stocks_enabled: boolean;

  // Agent features
  llm_agent_enabled: boolean;
  technical_agent_enabled: boolean;
  sentiment_agent_enabled: boolean;
  onchain_agent_enabled: boolean;
  agent_debate_enabled: boolean;

  // Risk controls
  circuit_breaker_enabled: boolean;
  auto_position_sizing: boolean;
  max_leverage_override: number | null;

  // Dashboard
  realtime_updates_enabled: boolean;
  advanced_charts_enabled: boolean;
  export_enabled: boolean;

  // Extensible — any additional flags from feature_flags.yaml
  [key: string]: boolean | number | string | null;
}

// ---- Market Status (open/closed per market) ----
export type MarketStatusState = 'open' | 'closed' | 'pre_market' | 'after_hours';

export interface MarketStatusInfo {
  market: Market;
  status: MarketStatusState;
  label: string;
  opens_at: string | null;    // ISO-8601, next open if currently closed
  closes_at: string | null;   // ISO-8601, time until close if currently open
  session: string | null;     // e.g. 'London' / 'New York' for forex
}

// ---- Generic API helpers ----
export interface ApiResponse<T> {
  data: T;
  error: string | null;
  timestamp: string;
}

export interface PaginatedResponse<T> {
  data: T[];
  total: number;
  page: number;
  page_size: number;
  has_more: boolean;
}

export interface LoadingState<T> {
  data: T | null;
  loading: boolean;
  error: Error | null;
}

// ---- Zustand Store Types ----
export interface PortfolioState {
  equity: number;
  cash: number;
  dailyPnl: number;
  dailyPnlPct: number;
  drawdown: number;
  maxDrawdown: number;
  openPositions: number;
  exposurePct: number;
  equityCurve: { time: string; value: number }[];
}

export interface NexusStore {
  // State
  portfolioState: PortfolioState;
  signalFeed: Signal[];
  activeTrades: Trade[];
  riskEvents: RiskEvent[];
  agentStates: Partial<Record<AgentRole, AgentDecision>>;
  marketPrices: Record<string, number>;
  systemStatus: SystemStatus;
  isConnected: boolean;
  lastUpdate: string | null;

  // Actions
  updatePortfolio: (snapshot: PortfolioSnapshot) => void;
  addSignal: (signal: Signal) => void;
  setSignalFeed: (signals: Signal[]) => void;
  addTrade: (trade: Trade) => void;
  updateTrade: (trade: Trade) => void;
  setActiveTrades: (trades: Trade[]) => void;
  addRiskEvent: (event: RiskEvent) => void;
  setRiskEvents: (events: RiskEvent[]) => void;
  updateAgentState: (role: AgentRole, decision: AgentDecision) => void;
  setMarketPrice: (symbol: string, price: number) => void;
  setMarketPrices: (prices: Record<string, number>) => void;
  updateSystemStatus: (status: Partial<SystemStatus>) => void;
  setConnected: (connected: boolean) => void;
  setLastUpdate: (time: string) => void;
}
