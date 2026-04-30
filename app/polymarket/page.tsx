'use client';

import { useEffect, useState, useCallback } from 'react';
import {
  Target,
  TrendingUp,
  RefreshCw,
  AlertCircle,
  ChevronDown,
  ChevronUp,
  Loader2,
  Zap,
  Brain,
  CloudRain,
  Clock,
  BarChart2,
  DollarSign,
  Award,
  Activity,
} from 'lucide-react';
import { cn, formatCurrency, formatPercent } from '@/lib/utils';
import type {
  ArbOpportunity,
  PolymarketSignal,
  PolymarketPosition,
  PolymarketMarket,
  PolymarketStats,
} from '@/lib/types';

// ── Helpers ──────────────────────────────────────────────────────────────────

function formatVolume(v: number): string {
  if (v >= 1e9) return `$${(v / 1e9).toFixed(2)}B`;
  if (v >= 1e6) return `$${(v / 1e6).toFixed(2)}M`;
  if (v >= 1e3) return `$${(v / 1e3).toFixed(1)}K`;
  return `$${v.toFixed(0)}`;
}

function formatAge(isoString: string): string {
  const diff = (Date.now() - new Date(isoString).getTime()) / 1000;
  if (diff < 60)    return 'just now';
  if (diff < 3600)  return `${Math.floor(diff / 60)}m ago`;
  if (diff < 86400) return `${Math.floor(diff / 3600)}h ago`;
  return `${Math.floor(diff / 86400)}d ago`;
}

function truncate(str: string, len: number): string {
  return str.length > len ? str.slice(0, len) + '…' : str;
}

const CATEGORY_COLORS: Record<string, string> = {
  politics:   'bg-nexus-purple/15 text-nexus-purple border-nexus-purple/20',
  sports:     'bg-nexus-blue/15 text-nexus-blue border-nexus-blue/20',
  crypto:     'bg-nexus-yellow/15 text-nexus-yellow border-nexus-yellow/20',
  weather:    'bg-cyan-500/15 text-cyan-400 border-cyan-500/20',
  economics:  'bg-nexus-green/15 text-nexus-green border-nexus-green/20',
};

function categoryBadge(cat?: string): string {
  const key = (cat ?? '').toLowerCase();
  return CATEGORY_COLORS[key] ?? 'bg-white/5 text-muted border-white/10';
}

const STRATEGY_META: Record<string, { label: string; color: string; Icon: React.ElementType }> = {
  arbitrage:    { label: 'ARBITRAGE',    color: 'text-nexus-yellow', Icon: Zap },
  llm_ensemble: { label: 'LLM ENSEMBLE', color: 'text-nexus-purple', Icon: Brain },
  weather:      { label: 'WEATHER',      color: 'text-cyan-400',     Icon: CloudRain },
  latency:      { label: 'LATENCY',      color: 'text-nexus-blue',   Icon: Clock },
  domain:       { label: 'DOMAIN',       color: 'text-nexus-green',  Icon: Target },
};

// ── Skeleton ──────────────────────────────────────────────────────────────────

function Skeleton({ className }: { className?: string }) {
  return <div className={cn('animate-pulse rounded bg-white/5', className)} />;
}

// ── Error Banner ──────────────────────────────────────────────────────────────

function ErrorBanner({ message, onRetry }: { message: string; onRetry: () => void }) {
  return (
    <div className="flex items-center gap-3 px-4 py-3 rounded-lg border border-nexus-red/20 bg-nexus-red/5 text-nexus-red text-sm">
      <AlertCircle size={14} className="flex-shrink-0" />
      <span className="flex-1">{message}</span>
      <button
        onClick={onRetry}
        className="flex items-center gap-1.5 text-xs font-medium hover:text-white transition-colors"
      >
        <RefreshCw size={12} />
        Retry
      </button>
    </div>
  );
}

// ── Section A: Stats Bar ──────────────────────────────────────────────────────

interface StatsBarProps {
  stats: PolymarketStats | null;
  loading: boolean;
  error: string | null;
  onRetry: () => void;
}

function StatsBar({ stats, loading, error, onRetry }: StatsBarProps) {
  if (error) return <ErrorBanner message={error} onRetry={onRetry} />;

  const cards = [
    {
      label: 'Total P&L',
      value: loading ? null : formatCurrency(stats?.total_pnl ?? 0),
      sub:   'unrealized + realized',
      color: (stats?.total_pnl ?? 0) >= 0 ? 'text-nexus-green' : 'text-nexus-red',
      Icon:  TrendingUp,
    },
    {
      label: 'Open Positions',
      value: loading ? null : String(stats?.open_positions ?? 0),
      sub:   'active markets',
      color: 'text-nexus-blue',
      Icon:  BarChart2,
    },
    {
      label: 'Win Rate',
      value: loading ? null : `${((stats?.win_rate ?? 0) * 100).toFixed(1)}%`,
      sub:   'closed positions',
      color: (stats?.win_rate ?? 0) >= 0.5 ? 'text-nexus-green' : 'text-nexus-yellow',
      Icon:  Award,
    },
    {
      label: 'Capital Deployed',
      value: loading ? null : formatCurrency(stats?.total_deployed ?? 0),
      sub:   'USDC in markets',
      color: 'text-nexus-yellow',
      Icon:  DollarSign,
    },
    {
      label: 'Best Signal Edge',
      value: loading ? null : formatPercent((stats?.best_edge ?? 0) * 100, 1),
      sub:   'highest confidence now',
      color: 'text-nexus-purple',
      Icon:  Zap,
    },
  ];

  return (
    <div className="grid grid-cols-2 sm:grid-cols-3 lg:grid-cols-5 gap-3">
      {cards.map(({ label, value, sub, color, Icon }) => (
        <div key={label} className="nexus-card p-4 flex flex-col gap-1.5">
          <div className="flex items-center gap-2 text-muted">
            <Icon size={13} />
            <span className="text-xs uppercase tracking-wide">{label}</span>
          </div>
          {loading || value === null ? (
            <Skeleton className="h-7 w-24 mt-1" />
          ) : (
            <div className={cn('font-mono text-xl font-bold', color)}>{value}</div>
          )}
          <div className="text-xs text-muted">{sub}</div>
        </div>
      ))}
    </div>
  );
}

// ── Section B: Arbitrage Scanner ──────────────────────────────────────────────

interface ArbScannerProps {
  opportunities: ArbOpportunity[];
  loading: boolean;
  error: string | null;
  lastRefresh: Date | null;
  onRetry: () => void;
}

function ArbScanner({ opportunities, loading, error, lastRefresh, onRetry }: ArbScannerProps) {
  return (
    <div className="nexus-card p-0 overflow-hidden">
      {/* Header */}
      <div className="flex items-center justify-between px-4 py-3 border-b border-border">
        <div className="flex items-center gap-2">
          <Zap size={14} className="text-nexus-yellow" />
          <h2 className="font-semibold text-sm text-white">Live Arbitrage Scanner</h2>
          <span className="text-xs px-2 py-0.5 rounded-full bg-white/5 text-muted border border-border">
            API: Gamma
          </span>
        </div>
        <div className="flex items-center gap-3">
          {lastRefresh && (
            <span className="text-xs text-muted hidden sm:block">
              Updated {formatAge(lastRefresh.toISOString())}
            </span>
          )}
          {loading && <Loader2 size={13} className="animate-spin text-nexus-blue" />}
        </div>
      </div>

      {error ? (
        <div className="p-4"><ErrorBanner message={error} onRetry={onRetry} /></div>
      ) : loading && opportunities.length === 0 ? (
        <div className="flex flex-col items-center justify-center py-14 gap-3 text-muted">
          <div className="flex items-center gap-2">
            <Loader2 size={16} className="animate-spin text-nexus-blue" />
            <span className="text-sm">Scanning 1000+ markets for arbitrage…</span>
          </div>
          <div className="space-y-2 w-full max-w-lg px-4">
            {[...Array(3)].map((_, i) => (
              <Skeleton key={i} className="h-10 w-full" />
            ))}
          </div>
        </div>
      ) : opportunities.length === 0 ? (
        <div className="flex flex-col items-center justify-center py-14 gap-2 text-muted">
          <Activity size={28} className="opacity-30" />
          <p className="text-sm">No arbitrage opportunities found — markets are efficient</p>
          <p className="text-xs opacity-60">Auto-refreshes every 30 seconds</p>
        </div>
      ) : (
        <div className="overflow-x-auto">
          <table className="w-full text-sm">
            <thead>
              <tr className="border-b border-border text-xs text-muted">
                <th className="text-left px-4 py-2.5 font-medium">Market Question</th>
                <th className="text-right px-3 py-2.5 font-medium">YES</th>
                <th className="text-right px-3 py-2.5 font-medium">NO</th>
                <th className="text-right px-3 py-2.5 font-medium">Combined</th>
                <th className="text-right px-3 py-2.5 font-medium">Profit/$</th>
                <th className="text-right px-3 py-2.5 font-medium">Max Size</th>
                <th className="text-center px-3 py-2.5 font-medium">Type</th>
                <th className="text-right px-3 py-2.5 font-medium">Age</th>
              </tr>
            </thead>
            <tbody>
              {opportunities.map((opp) => {
                const isProfitable = opp.profit_per_dollar > 0.02;
                return (
                  <tr
                    key={opp.market_id}
                    className={cn(
                      'border-b border-border/50 transition-colors hover:bg-white/3',
                      isProfitable ? 'bg-nexus-green/3' : 'bg-nexus-yellow/3'
                    )}
                  >
                    <td className="px-4 py-3 max-w-xs">
                      <span className="text-white text-xs leading-snug line-clamp-2">
                        {truncate(opp.question, 80)}
                      </span>
                    </td>
                    <td className="px-3 py-3 text-right font-mono text-nexus-green text-xs whitespace-nowrap">
                      {(opp.yes_price * 100).toFixed(1)}¢
                    </td>
                    <td className="px-3 py-3 text-right font-mono text-nexus-red text-xs whitespace-nowrap">
                      {(opp.no_price * 100).toFixed(1)}¢
                    </td>
                    <td className="px-3 py-3 text-right font-mono text-xs whitespace-nowrap">
                      <span className={opp.combined_price < 0.95 ? 'text-nexus-green font-semibold' : 'text-white'}>
                        {(opp.combined_price * 100).toFixed(1)}¢
                      </span>
                    </td>
                    <td className="px-3 py-3 text-right font-mono text-nexus-green font-semibold text-xs whitespace-nowrap">
                      +{(opp.profit_per_dollar * 100).toFixed(2)}%
                    </td>
                    <td className="px-3 py-3 text-right font-mono text-xs whitespace-nowrap text-muted">
                      {formatCurrency(opp.max_size_usdc, 'USD', true)}
                    </td>
                    <td className="px-3 py-3 text-center">
                      <span className="text-[10px] px-1.5 py-0.5 rounded bg-nexus-blue/10 text-nexus-blue border border-nexus-blue/20 uppercase tracking-wide whitespace-nowrap">
                        {opp.type.replace(/_/g, ' ')}
                      </span>
                    </td>
                    <td className="px-3 py-3 text-right text-xs text-muted whitespace-nowrap">
                      {formatAge(opp.detected_at)}
                    </td>
                  </tr>
                );
              })}
            </tbody>
          </table>
        </div>
      )}
    </div>
  );
}

// ── Section C: Signal Feed ────────────────────────────────────────────────────

interface SignalFeedProps {
  signals: PolymarketSignal[];
  loading: boolean;
  error: string | null;
  paperMode: boolean;
  onRetry: () => void;
}

function SignalCard({ signal, paperMode }: { signal: PolymarketSignal; paperMode: boolean }) {
  const meta = STRATEGY_META[signal.strategy] ?? STRATEGY_META.llm_ensemble;
  const StratIcon = meta.Icon;
  const edgePct   = signal.edge * 100;
  const barWidth  = Math.min((edgePct / 20) * 100, 100);

  return (
    <div className="nexus-card p-4 flex flex-col gap-3">
      {/* Question */}
      <p className="text-xs text-white leading-snug">{signal.question}</p>

      {/* Badges row */}
      <div className="flex items-center gap-2 flex-wrap">
        <span
          className={cn(
            'flex items-center gap-1 text-[10px] font-bold px-2 py-0.5 rounded border uppercase tracking-wide',
            meta.color,
            'bg-white/5 border-white/10'
          )}
        >
          <StratIcon size={9} />
          {meta.label}
        </span>
        <span
          className={cn(
            'text-[10px] font-bold px-2 py-0.5 rounded border uppercase',
            signal.direction === 'YES'
              ? 'bg-nexus-green/10 text-nexus-green border-nexus-green/20'
              : 'bg-nexus-red/10 text-nexus-red border-nexus-red/20'
          )}
        >
          {signal.direction}
        </span>
      </div>

      {/* Market price vs agent estimate */}
      <div className="grid grid-cols-2 gap-2 text-xs">
        <div>
          <div className="text-muted mb-0.5">Market Price</div>
          <div className="font-mono text-white font-semibold">
            {(signal.market_price * 100).toFixed(1)}¢
          </div>
        </div>
        <div>
          <div className="text-muted mb-0.5">Agent Estimate</div>
          <div className="font-mono text-nexus-blue font-semibold">
            {(signal.agent_estimate * 100).toFixed(1)}¢
          </div>
        </div>
      </div>

      {/* Edge progress bar */}
      <div>
        <div className="flex justify-between text-xs mb-1">
          <span className="text-muted">Edge</span>
          <span className={cn('font-mono font-semibold', edgePct >= 10 ? 'text-nexus-green' : 'text-nexus-yellow')}>
            +{edgePct.toFixed(1)}%
          </span>
        </div>
        <div className="h-1.5 bg-white/5 rounded-full overflow-hidden">
          <div
            className={cn(
              'h-full rounded-full transition-all',
              edgePct >= 10 ? 'bg-nexus-green' : 'bg-nexus-yellow'
            )}
            style={{ width: `${barWidth}%` }}
          />
        </div>
      </div>

      {/* Kelly size + uncertainty */}
      <div className="grid grid-cols-2 gap-2 text-xs">
        <div>
          <div className="text-muted mb-0.5">Kelly Position</div>
          <div className="font-mono text-nexus-yellow font-semibold">
            {formatCurrency(signal.position_usdc)}
          </div>
        </div>
        <div>
          <div className="text-muted mb-0.5">Swarm Std Dev</div>
          <div className="font-mono text-white">
            {(signal.swarm_std_dev * 100).toFixed(1)}%
          </div>
        </div>
      </div>

      {/* Swarm confidence bar */}
      <div>
        <div className="flex justify-between text-xs mb-1">
          <span className="text-muted">Swarm Confidence</span>
          <span className="font-mono text-muted">
            σ = {(signal.swarm_std_dev * 100).toFixed(1)}%
          </span>
        </div>
        <div className="h-1 bg-white/5 rounded-full overflow-hidden">
          <div
            className="h-full bg-nexus-purple rounded-full"
            style={{ width: `${Math.max(0, 100 - signal.swarm_std_dev * 500)}%` }}
          />
        </div>
      </div>

      {/* Execute button */}
      <div className="flex items-center justify-between">
        <span className="text-xs text-muted">{formatAge(signal.created_at)}</span>
        {paperMode ? (
          <div className="group relative">
            <button
              disabled
              className="text-xs px-3 py-1.5 rounded border border-white/10 text-muted bg-white/3 cursor-not-allowed"
            >
              EXECUTE
            </button>
            <div className="absolute bottom-full right-0 mb-1.5 hidden group-hover:block z-10">
              <div className="bg-card border border-border rounded px-2 py-1 text-xs text-muted whitespace-nowrap shadow-lg">
                Paper Mode — execution disabled
              </div>
            </div>
          </div>
        ) : (
          <button
            className="text-xs px-3 py-1.5 rounded border border-nexus-green/30 text-nexus-green bg-nexus-green/5 hover:bg-nexus-green/10 transition-colors font-medium"
          >
            EXECUTE
          </button>
        )}
      </div>
    </div>
  );
}

function TopSignalFeed({ signals, loading, error, paperMode, onRetry }: SignalFeedProps) {
  return (
    <div className="space-y-3">
      <div className="flex items-center justify-between">
        <div className="flex items-center gap-2">
          <Brain size={14} className="text-nexus-purple" />
          <h2 className="font-semibold text-sm text-white">Top Signal Feed</h2>
          {paperMode && (
            <span className="text-[10px] px-2 py-0.5 rounded-full bg-nexus-yellow/10 text-nexus-yellow border border-nexus-yellow/20">
              PAPER MODE
            </span>
          )}
        </div>
        <span className="text-xs text-muted">{signals.length} signals</span>
      </div>

      {error ? (
        <ErrorBanner message={error} onRetry={onRetry} />
      ) : loading && signals.length === 0 ? (
        <div className="flex items-center gap-2 text-muted text-sm py-6 justify-center">
          <Loader2 size={14} className="animate-spin" />
          Running agent analysis…
        </div>
      ) : signals.length === 0 ? (
        <div className="nexus-card p-6 text-center text-sm text-muted">
          <Brain size={28} className="mx-auto mb-2 opacity-30" />
          No signals available yet — agents are initializing
        </div>
      ) : (
        <div className="grid grid-cols-1 sm:grid-cols-2 xl:grid-cols-3 gap-3">
          {signals.slice(0, 10).map((s) => (
            <SignalCard key={s.id} signal={s} paperMode={paperMode} />
          ))}
        </div>
      )}
    </div>
  );
}

// ── Section D: Active Positions ───────────────────────────────────────────────

interface PositionsTableProps {
  positions: PolymarketPosition[];
  loading: boolean;
  error: string | null;
  onRetry: () => void;
}

function PositionsTable({ positions, loading, error, onRetry }: PositionsTableProps) {
  return (
    <div className="nexus-card p-0 overflow-hidden">
      <div className="flex items-center gap-2 px-4 py-3 border-b border-border">
        <Activity size={14} className="text-nexus-blue" />
        <h2 className="font-semibold text-sm text-white">Active Positions</h2>
        {positions.length > 0 && (
          <span className="ml-auto text-xs text-muted">{positions.length} open</span>
        )}
      </div>

      {error ? (
        <div className="p-4"><ErrorBanner message={error} onRetry={onRetry} /></div>
      ) : loading && positions.length === 0 ? (
        <div className="p-4 space-y-2">
          {[...Array(3)].map((_, i) => <Skeleton key={i} className="h-10 w-full" />)}
        </div>
      ) : positions.length === 0 ? (
        <div className="flex flex-col items-center justify-center py-12 text-muted">
          <Activity size={28} className="opacity-30 mb-2" />
          <p className="text-sm">No open positions</p>
        </div>
      ) : (
        <div className="overflow-x-auto">
          <table className="w-full text-sm">
            <thead>
              <tr className="border-b border-border text-xs text-muted">
                <th className="text-left px-4 py-2.5 font-medium">Market</th>
                <th className="text-center px-3 py-2.5 font-medium">Outcome</th>
                <th className="text-right px-3 py-2.5 font-medium">Size</th>
                <th className="text-right px-3 py-2.5 font-medium">Avg Entry</th>
                <th className="text-right px-3 py-2.5 font-medium">Current</th>
                <th className="text-right px-3 py-2.5 font-medium">Unreal. P&L $</th>
                <th className="text-right px-3 py-2.5 font-medium">Unreal. P&L %</th>
                <th className="text-center px-3 py-2.5 font-medium">Status</th>
              </tr>
            </thead>
            <tbody>
              {positions.map((pos) => {
                const pnlPct = pos.avg_price > 0
                  ? ((pos.current_price - pos.avg_price) / pos.avg_price) * 100
                  : 0;
                const pnlColor = pos.unrealized_pnl >= 0 ? 'text-nexus-green' : 'text-nexus-red';

                return (
                  <tr key={pos.id} className="border-b border-border/50 hover:bg-white/3 transition-colors">
                    <td className="px-4 py-3 max-w-xs">
                      <span className="text-white text-xs leading-snug line-clamp-2">
                        {truncate(pos.question, 70)}
                      </span>
                    </td>
                    <td className="px-3 py-3 text-center">
                      <span
                        className={cn(
                          'text-[10px] font-bold px-2 py-0.5 rounded border uppercase',
                          pos.outcome === 'YES'
                            ? 'bg-nexus-green/10 text-nexus-green border-nexus-green/20'
                            : 'bg-nexus-red/10 text-nexus-red border-nexus-red/20'
                        )}
                      >
                        {pos.outcome}
                      </span>
                    </td>
                    <td className="px-3 py-3 text-right font-mono text-xs text-white">
                      {pos.size.toFixed(2)}
                    </td>
                    <td className="px-3 py-3 text-right font-mono text-xs text-muted">
                      {(pos.avg_price * 100).toFixed(1)}¢
                    </td>
                    <td className="px-3 py-3 text-right font-mono text-xs text-white">
                      {(pos.current_price * 100).toFixed(1)}¢
                    </td>
                    <td className={cn('px-3 py-3 text-right font-mono text-xs font-semibold', pnlColor)}>
                      {pos.unrealized_pnl >= 0 ? '+' : ''}
                      {formatCurrency(pos.unrealized_pnl)}
                    </td>
                    <td className={cn('px-3 py-3 text-right font-mono text-xs font-semibold', pnlColor)}>
                      {pnlPct >= 0 ? '+' : ''}
                      {pnlPct.toFixed(2)}%
                    </td>
                    <td className="px-3 py-3 text-center">
                      <span className="text-[10px] px-2 py-0.5 rounded bg-nexus-blue/10 text-nexus-blue border border-nexus-blue/20">
                        {pos.status}
                      </span>
                    </td>
                  </tr>
                );
              })}
            </tbody>
          </table>
        </div>
      )}
    </div>
  );
}

// ── Section E: Market Scanner ─────────────────────────────────────────────────

function ProbabilityBar({ price }: { price: number }) {
  const pct = Math.round(price * 100);
  return (
    <div className="flex items-center gap-2">
      <div className="flex-1 h-1.5 bg-white/5 rounded-full overflow-hidden">
        <div
          className={cn(
            'h-full rounded-full transition-all',
            pct >= 70 ? 'bg-nexus-green' : pct >= 40 ? 'bg-nexus-yellow' : 'bg-nexus-red'
          )}
          style={{ width: `${pct}%` }}
        />
      </div>
      <span className="text-xs font-mono text-muted w-8 text-right">{pct}%</span>
    </div>
  );
}

interface MarketScannerProps {
  markets: PolymarketMarket[];
  loading: boolean;
  error: string | null;
  onRetry: () => void;
}

function MarketScanner({ markets, loading, error, onRetry }: MarketScannerProps) {
  const [expanded, setExpanded] = useState<string | null>(null);

  return (
    <div className="nexus-card p-0 overflow-hidden">
      <div className="flex items-center gap-2 px-4 py-3 border-b border-border">
        <Target size={14} className="text-nexus-green" />
        <h2 className="font-semibold text-sm text-white">Market Scanner</h2>
        <span className="ml-auto text-xs text-muted">Top by 24h volume</span>
      </div>

      {error ? (
        <div className="p-4"><ErrorBanner message={error} onRetry={onRetry} /></div>
      ) : loading && markets.length === 0 ? (
        <div className="p-3 space-y-2">
          {[...Array(6)].map((_, i) => <Skeleton key={i} className="h-14 w-full" />)}
        </div>
      ) : markets.length === 0 ? (
        <div className="flex flex-col items-center justify-center py-10 text-muted">
          <Target size={28} className="opacity-30 mb-2" />
          <p className="text-sm">No active markets</p>
        </div>
      ) : (
        <div className="divide-y divide-border/50 max-h-[600px] overflow-y-auto">
          {markets.slice(0, 20).map((market) => {
            const isExpanded = expanded === market.id;
            return (
              <div
                key={market.id}
                className="cursor-pointer hover:bg-white/3 transition-colors"
                onClick={() => setExpanded(isExpanded ? null : market.id)}
              >
                <div className="px-4 py-3">
                  <div className="flex items-start justify-between gap-2 mb-2">
                    <p className="text-xs text-white leading-snug line-clamp-2 flex-1">
                      {market.question}
                    </p>
                    <div className="flex items-center gap-1.5 flex-shrink-0">
                      {market.category && (
                        <span
                          className={cn(
                            'text-[10px] px-1.5 py-0.5 rounded border uppercase tracking-wide',
                            categoryBadge(market.category)
                          )}
                        >
                          {market.category}
                        </span>
                      )}
                      {isExpanded ? (
                        <ChevronUp size={12} className="text-muted" />
                      ) : (
                        <ChevronDown size={12} className="text-muted" />
                      )}
                    </div>
                  </div>
                  <div className="flex items-center gap-3">
                    <div className="flex-1">
                      <ProbabilityBar price={market.yes_price} />
                    </div>
                    <span className="text-xs text-muted flex-shrink-0">
                      {formatVolume(market.volume_24h)}
                    </span>
                  </div>
                </div>

                {isExpanded && (
                  <div className="px-4 pb-3 bg-white/3 border-t border-border/30">
                    {market.description && (
                      <p className="text-xs text-muted mt-2 mb-2 leading-relaxed">
                        {market.description}
                      </p>
                    )}
                    <div className="grid grid-cols-2 gap-x-4 gap-y-1 text-xs mt-2">
                      <div className="flex justify-between">
                        <span className="text-muted">YES Price</span>
                        <span className="font-mono text-nexus-green">{(market.yes_price * 100).toFixed(1)}¢</span>
                      </div>
                      <div className="flex justify-between">
                        <span className="text-muted">NO Price</span>
                        <span className="font-mono text-nexus-red">{(market.no_price * 100).toFixed(1)}¢</span>
                      </div>
                      <div className="flex justify-between">
                        <span className="text-muted">Total Volume</span>
                        <span className="font-mono text-white">{formatVolume(market.total_volume)}</span>
                      </div>
                      {market.end_date && (
                        <div className="flex justify-between">
                          <span className="text-muted">Closes</span>
                          <span className="font-mono text-muted">
                            {new Date(market.end_date).toLocaleDateString()}
                          </span>
                        </div>
                      )}
                    </div>
                    {(market.yes_token_id || market.no_token_id) && (
                      <div className="mt-2 text-[10px] text-muted/50 font-mono break-all">
                        YES: {market.yes_token_id.slice(0, 20)}…
                      </div>
                    )}
                  </div>
                )}
              </div>
            );
          })}
        </div>
      )}
    </div>
  );
}

// ── Section F: Agent Performance ──────────────────────────────────────────────

interface AgentPerformanceProps {
  signals: PolymarketSignal[];
  loading: boolean;
}

function AgentPerformance({ signals, loading }: AgentPerformanceProps) {
  const strategies = ['arbitrage', 'llm_ensemble', 'weather', 'latency', 'domain'] as const;

  type Strategy = typeof strategies[number];

  const statsByStrategy: Record<Strategy, { count: number; avgEdge: number; executed: number }> =
    strategies.reduce(
      (acc, s) => {
        const group = signals.filter((sig) => sig.strategy === s);
        const avgEdge = group.length > 0
          ? group.reduce((sum, sig) => sum + sig.edge, 0) / group.length
          : 0;
        acc[s] = {
          count:    group.length,
          avgEdge,
          executed: group.filter((sig) => sig.executed).length,
        };
        return acc;
      },
      {} as Record<Strategy, { count: number; avgEdge: number; executed: number }>
    );

  return (
    <div className="space-y-3">
      <div className="flex items-center gap-2">
        <BarChart2 size={14} className="text-nexus-blue" />
        <h2 className="font-semibold text-sm text-white">Agent Performance</h2>
      </div>
      <div className="grid grid-cols-2 sm:grid-cols-3 lg:grid-cols-5 gap-3">
        {strategies.map((strat) => {
          const meta  = STRATEGY_META[strat];
          const Icon  = meta.Icon;
          const stats = statsByStrategy[strat];

          return (
            <div key={strat} className="nexus-card p-3 flex flex-col gap-2">
              <div className="flex items-center gap-1.5">
                <Icon size={12} className={meta.color} />
                <span className={cn('text-[10px] font-bold uppercase tracking-wide', meta.color)}>
                  {meta.label}
                </span>
              </div>
              {loading ? (
                <>
                  <Skeleton className="h-5 w-12" />
                  <Skeleton className="h-3 w-16" />
                </>
              ) : (
                <>
                  <div className="font-mono text-lg font-bold text-white">{stats.count}</div>
                  <div className="space-y-0.5 text-xs text-muted">
                    <div>Signals: <span className="text-white">{stats.count}</span></div>
                    <div>
                      Avg edge:{' '}
                      <span className="text-nexus-green font-mono">
                        {(stats.avgEdge * 100).toFixed(1)}%
                      </span>
                    </div>
                    <div>Executed: <span className="text-white">{stats.executed}</span></div>
                  </div>
                </>
              )}
            </div>
          );
        })}
      </div>
    </div>
  );
}

// ── Data hooks ────────────────────────────────────────────────────────────────

interface FetchState<T> {
  data: T;
  loading: boolean;
  error: string | null;
  lastRefresh: Date | null;
}

function usePolymarketData<T>(
  endpoint: string,
  defaultData: T,
  intervalMs: number = 30_000
) {
  const [state, setState] = useState<FetchState<T>>({
    data: defaultData,
    loading: true,
    error: null,
    lastRefresh: null,
  });

  const fetch_ = useCallback(async () => {
    try {
      const res  = await fetch(endpoint);
      const json = await res.json();
      setState((prev) => ({
        ...prev,
        data:        json,
        loading:     false,
        error:       null,
        lastRefresh: new Date(),
      }));
    } catch (err) {
      setState((prev) => ({
        ...prev,
        loading: false,
        error:   err instanceof Error ? err.message : 'Fetch failed',
      }));
    }
  }, [endpoint]);

  const retry = useCallback(() => {
    setState((prev) => ({ ...prev, loading: true, error: null }));
    fetch_();
  }, [fetch_]);

  useEffect(() => {
    fetch_();
    const t = setInterval(fetch_, intervalMs);
    return () => clearInterval(t);
  }, [fetch_, intervalMs]);

  return { ...state, retry };
}

// ── Page ──────────────────────────────────────────────────────────────────────

export default function PolymarketPage() {
  // Paper mode: derive from global store if available, default true for safety
  const paperMode = true; // default to paper — override with store.systemStatus.paper_mode if wired

  // Stats (60s refresh)
  const statsState = usePolymarketData<Record<string, unknown>>(
    '/api/polymarket/stats',
    {},
    60_000
  );
  const stats: PolymarketStats | null = statsState.loading
    ? null
    : {
        total_pnl:                 Number(statsState.data.total_pnl ?? 0),
        win_rate:                  Number(statsState.data.win_rate ?? 0),
        open_positions:            Number(statsState.data.open_positions ?? 0),
        best_edge:                 Number(statsState.data.best_edge ?? 0),
        total_deployed:            Number(statsState.data.total_deployed ?? 0),
        opportunities_found_today: Number(statsState.data.opportunities_found_today ?? 0),
      };

  // Arbitrage (30s refresh)
  const arbState = usePolymarketData<{ opportunities?: ArbOpportunity[] }>(
    '/api/polymarket/arbitrage',
    {},
    30_000
  );
  const opportunities: ArbOpportunity[] = arbState.data.opportunities ?? [];

  // Signals (45s refresh)
  const signalsState = usePolymarketData<{ signals?: PolymarketSignal[] }>(
    '/api/polymarket/signals',
    {},
    45_000
  );
  const signals: PolymarketSignal[] = signalsState.data.signals ?? [];

  // Positions (60s refresh)
  const positionsState = usePolymarketData<{ positions?: PolymarketPosition[] }>(
    '/api/polymarket/positions',
    {},
    60_000
  );
  const positions: PolymarketPosition[] = positionsState.data.positions ?? [];

  // Markets (90s refresh)
  const marketsState = usePolymarketData<{ markets?: PolymarketMarket[] }>(
    '/api/polymarket/markets?limit=50',
    {},
    90_000
  );
  const markets: PolymarketMarket[] = marketsState.data.markets ?? [];

  return (
    <div className="space-y-6">
      {/* Page header */}
      <div className="flex items-center justify-between">
        <div className="flex items-center gap-3">
          <div className="p-2 rounded-lg bg-nexus-green/10 border border-nexus-green/20">
            <Target size={18} className="text-nexus-green" />
          </div>
          <div>
            <h1 className="text-base font-bold text-white tracking-tight">
              Polymarket Trading
            </h1>
            <p className="text-xs text-muted mt-0.5">
              Prediction market arbitrage and signal execution
            </p>
          </div>
        </div>
        <div className="flex items-center gap-2">
          {paperMode && (
            <span className="text-xs px-2.5 py-1 rounded-full bg-nexus-yellow/10 text-nexus-yellow border border-nexus-yellow/20 font-medium">
              PAPER MODE
            </span>
          )}
          <span className="text-xs text-muted hidden sm:block">
            {new Date().toLocaleTimeString()}
          </span>
        </div>
      </div>

      {/* Section A: Stats bar */}
      <StatsBar
        stats={stats}
        loading={statsState.loading}
        error={statsState.error}
        onRetry={statsState.retry}
      />

      {/* Main content grid: left 2/3 + right 1/3 */}
      <div className="grid grid-cols-1 xl:grid-cols-3 gap-6">
        {/* Left column: Arb scanner + signals + positions */}
        <div className="xl:col-span-2 space-y-6">
          {/* Section B: Arbitrage Scanner */}
          <ArbScanner
            opportunities={opportunities}
            loading={arbState.loading}
            error={arbState.error}
            lastRefresh={arbState.lastRefresh}
            onRetry={arbState.retry}
          />

          {/* Section C: Signal Feed */}
          <TopSignalFeed
            signals={signals}
            loading={signalsState.loading}
            error={signalsState.error}
            paperMode={paperMode}
            onRetry={signalsState.retry}
          />

          {/* Section D: Active Positions */}
          <PositionsTable
            positions={positions}
            loading={positionsState.loading}
            error={positionsState.error}
            onRetry={positionsState.retry}
          />
        </div>

        {/* Right column: Market scanner */}
        <div className="xl:col-span-1">
          <MarketScanner
            markets={markets}
            loading={marketsState.loading}
            error={marketsState.error}
            onRetry={marketsState.retry}
          />
        </div>
      </div>

      {/* Section F: Agent Performance */}
      <AgentPerformance signals={signals} loading={signalsState.loading} />

      {/* Footer attribution */}
      <p className="text-xs text-muted text-center pb-2">
        Market data via{' '}
        <a
          href="https://gamma-api.polymarket.com"
          target="_blank"
          rel="noopener noreferrer"
          className="hover:text-white transition-colors"
        >
          Polymarket Gamma API
        </a>
        {' · '}
        Signals from{' '}
        <a
          href="https://polymarket.com"
          target="_blank"
          rel="noopener noreferrer"
          className="hover:text-white transition-colors"
        >
          Polymarket
        </a>
        {' · '}
        Prices update every 30 seconds
      </p>
    </div>
  );
}
