'use client';

import React, { useEffect, useState } from 'react';
import {
  Activity,
  TrendingUp,
  TrendingDown,
  AlertCircle,
  CheckCircle2,
  XCircle,
  Minus,
  RefreshCw,
  Bot,
  Wifi,
  WifiOff,
  Brain,
  Zap,
  BarChart3,
  Target,
  Layers,
} from 'lucide-react';
import {
  PieChart,
  Pie,
  Cell,
  ResponsiveContainer,
  Tooltip,
} from 'recharts';
import { useNexusStore } from '@/lib/store';
import { useBotMetrics } from '@/hooks/useBotMetrics';
import { getRecentSignals, getRecentTrades } from '@/lib/supabase';
import { PnLCard } from '@/components/PnLCard';
import { SignalFeed } from '@/components/SignalFeed';
import { TradeTable } from '@/components/TradeTable';
import {
  cn,
  formatCurrency,
  formatPercent,
  formatTimeAgo,
  getDirectionColor,
  getDirectionBg,
} from '@/lib/utils';
import type { Trade, Market, Signal } from '@/lib/types';

const MARKETS: { key: Market; label: string; flag: string }[] = [
  { key: 'crypto', label: 'Crypto', flag: '₿' },
  { key: 'forex', label: 'Forex', flag: '💱' },
  { key: 'commodities', label: 'Commodities', flag: '🥇' },
  { key: 'indian_stocks', label: 'Indian Stocks', flag: '₹' },
  { key: 'us_stocks', label: 'US Stocks', flag: '$' },
];

const CIRCUIT_BREAKERS = [
  { key: 'daily_loss_limit', label: 'Daily Loss Limit' },
  { key: 'max_drawdown', label: 'Max Drawdown' },
  { key: 'position_concentration', label: 'Position Concentration' },
  { key: 'correlation_limit', label: 'Correlation Limit' },
  { key: 'volatility_spike', label: 'Volatility Spike' },
  { key: 'api_failure', label: 'API Failure' },
] as const;

function MarketStatusCard({
  market,
  signals,
  trades,
}: {
  market: { key: Market; label: string; flag: string };
  signals: Signal[];
  trades: Trade[];
}) {
  const latestSignal = signals.find((s) => s.market === market.key);
  const marketTrades = trades.filter((t) => t.market === market.key && t.status === 'OPEN');
  const isActive = marketTrades.length > 0 || !!latestSignal;

  return (
    <div
      className={cn(
        'nexus-card nexus-card-hover p-4',
        isActive && latestSignal?.direction === 'LONG' && 'border-nexus-green/20',
        isActive && latestSignal?.direction === 'SHORT' && 'border-nexus-red/20'
      )}
    >
      <div className="flex items-center justify-between mb-3">
        <div className="flex items-center gap-2">
          <span className="text-lg">{market.flag}</span>
          <span className="font-medium text-sm text-white">{market.label}</span>
        </div>
        <div className="flex items-center gap-1">
          <span className={cn('status-dot', isActive ? 'active' : 'inactive')} />
          <span className="text-xs text-muted">{isActive ? 'Active' : 'Idle'}</span>
        </div>
      </div>

      {latestSignal ? (
        <div className="space-y-2">
          <div className="flex items-center justify-between">
            <span className="text-xs text-muted">{latestSignal.symbol}</span>
            <span
              className={cn(
                'badge text-xs',
                getDirectionBg(latestSignal.direction)
              )}
            >
              {latestSignal.direction}
            </span>
          </div>
          <div className="flex items-center justify-between">
            <span className="text-xs text-muted">Confidence</span>
            <span className="text-xs font-mono text-nexus-blue">
              {(latestSignal.confidence * 100).toFixed(0)}%
            </span>
          </div>
          <div className="flex items-center justify-between">
            <span className="text-xs text-muted">Strength</span>
            <div className="flex items-center gap-1.5">
              <div className="w-16 h-1 bg-border rounded-full overflow-hidden">
                <div
                  className={cn(
                    'h-full rounded-full',
                    latestSignal.direction === 'LONG' ? 'bg-nexus-green' : 'bg-nexus-red'
                  )}
                  style={{ width: `${latestSignal.strength}%` }}
                />
              </div>
              <span className="text-xs text-muted">{latestSignal.strength.toFixed(0)}</span>
            </div>
          </div>
        </div>
      ) : (
        <p className="text-xs text-muted text-center py-3">No recent signals</p>
      )}

      <div className="mt-3 pt-2 border-t border-border flex items-center justify-between">
        <span className="text-xs text-muted">Open Trades</span>
        <span className="text-xs font-mono text-nexus-blue">{marketTrades.length}</span>
      </div>
    </div>
  );
}

function CircuitBreakerStatus({ name, triggered }: { name: string; triggered: boolean }) {
  return (
    <div
      className={cn(
        'flex items-center justify-between px-3 py-2 rounded-lg border',
        triggered
          ? 'bg-nexus-red/5 border-nexus-red/20'
          : 'bg-nexus-green/5 border-nexus-green/10'
      )}
    >
      <span className="text-xs text-gray-300">{name}</span>
      {triggered ? (
        <XCircle size={14} className="text-nexus-red" />
      ) : (
        <CheckCircle2 size={14} className="text-nexus-green" />
      )}
    </div>
  );
}

function BotMetricCard({
  icon: Icon,
  label,
  value,
  subValue,
  color = 'text-nexus-blue',
  iconColor,
}: {
  icon: React.ElementType;
  label: string;
  value: string;
  subValue?: string;
  color?: string;
  iconColor?: string;
}) {
  return (
    <div className="nexus-card p-3 flex items-center gap-3">
      <div className={cn('p-2 rounded-lg bg-white/5', iconColor ?? color)}>
        <Icon size={16} />
      </div>
      <div className="min-w-0 flex-1">
        <p className="text-xs text-muted truncate">{label}</p>
        <p className={cn('text-sm font-mono font-bold truncate', color)}>{value}</p>
        {subValue && <p className="text-xs text-muted truncate">{subValue}</p>}
      </div>
    </div>
  );
}

export default function OverviewPage() {
  const {
    portfolioState,
    signalFeed,
    activeTrades,
    agentStates,
    systemStatus,
  } = useNexusStore();

  const botMetrics = useBotMetrics();

  const [recentTrades, setRecentTrades] = useState<Trade[]>([]);
  const [isLoading, setIsLoading] = useState(true);

  useEffect(() => {
    const load = async () => {
      try {
        const trades = await getRecentTrades(10);
        setRecentTrades(trades);
      } finally {
        setIsLoading(false);
      }
    };
    load();

    const interval = setInterval(load, 30000);
    return () => clearInterval(interval);
  }, []);

  // Agent consensus chart data
  const agentVotes = Object.values(agentStates);
  const bullVotes = agentVotes.filter((a) => a?.decision === 'LONG').length;
  const bearVotes = agentVotes.filter((a) => a?.decision === 'SHORT').length;
  const neutralVotes = agentVotes.filter((a) => a?.decision === 'NEUTRAL').length;

  const consensusData = [
    { name: 'Bull', value: bullVotes || 4, color: '#00ff88' },
    { name: 'Bear', value: bearVotes || 2, color: '#ff4444' },
    { name: 'Neutral', value: neutralVotes || 1, color: '#6b7280' },
  ];

  const totalVotes = consensusData.reduce((s, d) => s + d.value, 0);

  return (
    <div className="space-y-6">
      {/* Row 1: Portfolio summary */}
      <div className="grid grid-cols-1 xl:grid-cols-4 gap-4">
        <div className="xl:col-span-1">
          <PnLCard />
        </div>

        {/* Market status cards */}
        <div className="xl:col-span-3 grid grid-cols-5 gap-3">
          {MARKETS.map((market) => (
            <MarketStatusCard
              key={market.key}
              market={market}
              signals={signalFeed}
              trades={activeTrades}
            />
          ))}
        </div>
      </div>

      {/* Row 1.5: Bot live metrics */}
      <div className="grid grid-cols-2 sm:grid-cols-3 xl:grid-cols-6 gap-3">
        {/* Bot connectivity */}
        <div
          className={cn(
            'nexus-card p-3 flex items-center gap-3',
            botMetrics.reachable ? 'border-nexus-green/20' : 'border-nexus-red/20'
          )}
        >
          <div
            className={cn(
              'p-2 rounded-lg',
              botMetrics.reachable ? 'bg-nexus-green/10 text-nexus-green' : 'bg-nexus-red/10 text-nexus-red'
            )}
          >
            {botMetrics.reachable ? <Wifi size={16} /> : <WifiOff size={16} />}
          </div>
          <div className="min-w-0 flex-1">
            <p className="text-xs text-muted">Bot Status</p>
            <p
              className={cn(
                'text-sm font-mono font-bold',
                botMetrics.reachable ? 'text-nexus-green' : 'text-nexus-red'
              )}
            >
              {botMetrics.reachable ? 'ONLINE' : 'OFFLINE'}
            </p>
            <p className="text-xs text-muted">
              {formatTimeAgo(botMetrics.fetchedAt)}
            </p>
          </div>
        </div>

        {/* Win rate */}
        <BotMetricCard
          icon={Target}
          label="Win Rate"
          value={
            botMetrics.win_rate !== null
              ? `${(botMetrics.win_rate * 100).toFixed(1)}%`
              : '—'
          }
          color={
            botMetrics.win_rate === null
              ? 'text-muted'
              : botMetrics.win_rate >= 0.5
              ? 'text-nexus-green'
              : 'text-nexus-yellow'
          }
        />

        {/* Daily P&L */}
        <BotMetricCard
          icon={botMetrics.daily_pnl !== null && botMetrics.daily_pnl >= 0 ? TrendingUp : TrendingDown}
          label="Daily P&L"
          value={
            botMetrics.daily_pnl !== null
              ? formatCurrency(botMetrics.daily_pnl)
              : '—'
          }
          color={
            botMetrics.daily_pnl === null
              ? 'text-muted'
              : botMetrics.daily_pnl >= 0
              ? 'text-nexus-green'
              : 'text-nexus-red'
          }
        />

        {/* LLM calls today */}
        <BotMetricCard
          icon={Brain}
          label="LLM Calls Today"
          value={botMetrics.llm_calls_today !== null ? String(botMetrics.llm_calls_today) : '—'}
          color="text-nexus-blue"
        />

        {/* Active strategies */}
        <BotMetricCard
          icon={Layers}
          label="Active Strategies"
          value={botMetrics.active_strategies !== null ? String(botMetrics.active_strategies) : '—'}
          color="text-nexus-purple"
        />

        {/* Signals / Trades today */}
        <BotMetricCard
          icon={Zap}
          label="Signals / Trades"
          value={
            botMetrics.signals_today !== null
              ? `${botMetrics.signals_today} / ${botMetrics.trades_today ?? 0}`
              : '— / —'
          }
          subValue="today"
          color="text-nexus-yellow"
        />
      </div>

      {/* Row 2: Signal feed + Agent consensus */}
      <div className="grid grid-cols-1 xl:grid-cols-3 gap-4">
        {/* Signal feed */}
        <div className="xl:col-span-2">
          <SignalFeed limit={10} showFilter />
        </div>

        {/* Agent consensus */}
        <div className="nexus-card p-4">
          <div className="flex items-center justify-between mb-4">
            <h3 className="font-medium text-sm text-white">Agent Consensus</h3>
            <div className="flex items-center gap-1.5">
              <Activity size={12} className="text-nexus-green" />
              <span className="text-xs text-muted">{agentVotes.length}/7 voted</span>
            </div>
          </div>

          <div className="flex items-center justify-center">
            <ResponsiveContainer width={180} height={180}>
              <PieChart>
                <Pie
                  data={consensusData}
                  cx="50%"
                  cy="50%"
                  innerRadius={55}
                  outerRadius={80}
                  paddingAngle={3}
                  dataKey="value"
                >
                  {consensusData.map((entry, index) => (
                    <Cell key={index} fill={entry.color} opacity={0.9} />
                  ))}
                </Pie>
                <Tooltip
                  contentStyle={{
                    background: '#1a1a28',
                    border: '1px solid #2a2a3e',
                    borderRadius: '8px',
                    fontSize: '12px',
                  }}
                  formatter={(value: number) => [`${value} votes`, '']}
                />
              </PieChart>
            </ResponsiveContainer>
          </div>

          <div className="space-y-2 mt-2">
            {consensusData.map((item) => (
              <div key={item.name} className="flex items-center justify-between">
                <div className="flex items-center gap-2">
                  <div
                    className="w-2.5 h-2.5 rounded-full"
                    style={{ backgroundColor: item.color }}
                  />
                  <span className="text-xs text-gray-300">{item.name}</span>
                </div>
                <div className="flex items-center gap-2">
                  <div className="w-20 h-1 bg-border rounded-full overflow-hidden">
                    <div
                      className="h-full rounded-full"
                      style={{
                        width: `${(item.value / totalVotes) * 100}%`,
                        backgroundColor: item.color,
                      }}
                    />
                  </div>
                  <span className="text-xs font-mono text-muted w-6 text-right">
                    {item.value}
                  </span>
                </div>
              </div>
            ))}
          </div>

          {/* Current consensus */}
          <div className="mt-4 pt-3 border-t border-border">
            <div className="text-center">
              <p className="text-xs text-muted mb-1">Current Signal</p>
              <span
                className={cn(
                  'text-sm font-bold',
                  bullVotes > bearVotes ? 'text-nexus-green' : bullVotes < bearVotes ? 'text-nexus-red' : 'text-nexus-yellow'
                )}
              >
                {bullVotes > bearVotes ? '▲ BULLISH' : bullVotes < bearVotes ? '▼ BEARISH' : '◆ NEUTRAL'}
              </span>
            </div>
          </div>
        </div>
      </div>

      {/* Row 3: Recent trades + Circuit breakers */}
      <div className="grid grid-cols-1 xl:grid-cols-3 gap-4">
        {/* Recent trades */}
        <div className="xl:col-span-2">
          <TradeTable trades={recentTrades} isLoading={isLoading} title="Recent Trades" />
        </div>

        {/* Circuit breakers */}
        <div className="nexus-card p-4">
          <div className="flex items-center justify-between mb-4">
            <h3 className="font-medium text-sm text-white">Circuit Breakers</h3>
            <div className="flex items-center gap-1.5">
              {Object.values(systemStatus.circuit_breakers).some(Boolean) ? (
                <>
                  <AlertCircle size={12} className="text-nexus-red" />
                  <span className="text-xs text-nexus-red">Alert</span>
                </>
              ) : (
                <>
                  <CheckCircle2 size={12} className="text-nexus-green" />
                  <span className="text-xs text-nexus-green">All Clear</span>
                </>
              )}
            </div>
          </div>

          <div className="space-y-2">
            {CIRCUIT_BREAKERS.map((cb) => (
              <CircuitBreakerStatus
                key={cb.key}
                name={cb.label}
                triggered={systemStatus.circuit_breakers[cb.key]}
              />
            ))}
          </div>

          <div className="mt-4 pt-3 border-t border-border">
            <div className="grid grid-cols-3 gap-2 text-center">
              <div>
                <div className="text-lg font-mono font-bold text-nexus-green">
                  {Object.values(systemStatus.circuit_breakers).filter(v => !v).length}
                </div>
                <div className="text-xs text-muted">Safe</div>
              </div>
              <div>
                <div className="text-lg font-mono font-bold text-nexus-yellow">0</div>
                <div className="text-xs text-muted">Warning</div>
              </div>
              <div>
                <div className="text-lg font-mono font-bold text-nexus-red">
                  {Object.values(systemStatus.circuit_breakers).filter(Boolean).length}
                </div>
                <div className="text-xs text-muted">Triggered</div>
              </div>
            </div>
          </div>
        </div>
      </div>
    </div>
  );
}
