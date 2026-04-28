'use client';

import { useEffect, useState, useMemo } from 'react';
import {
  AreaChart,
  Area,
  XAxis,
  YAxis,
  ResponsiveContainer,
  Tooltip,
  CartesianGrid,
} from 'recharts';
import { TrendingUp, Award, BarChart2, Activity, CalendarDays } from 'lucide-react';
import { TradeTable } from '@/components/TradeTable';
import { PnLCalendar } from '@/components/PnLCalendar';
import { getEquityCurve, getPerformanceMetrics, getRecentTrades, getDailyPnLHistory } from '@/lib/supabase';
import type { DayData } from '@/lib/supabase';
import { cn, formatCurrency, formatPercent, formatNumber, getPnlColor } from '@/lib/utils';
import type { PerformanceMetrics, Trade } from '@/lib/types';

// ─── Sub-components ───────────────────────────────────────────

function MetricCard({
  label,
  value,
  sub,
  color = 'text-white',
}: {
  label: string;
  value: string | null;
  sub?: string;
  color?: string;
}) {
  return (
    <div className="nexus-card nexus-card-hover p-4">
      <div className="metric-label mb-2">{label}</div>
      <div className={cn('metric-value', value === null ? 'text-muted' : color)}>
        {value ?? '—'}
      </div>
      {sub && <div className="text-xs text-muted mt-1">{sub}</div>}
    </div>
  );
}

function EmptyState({ title, message }: { title: string; message: string }) {
  return (
    <div className="nexus-card p-6 flex flex-col items-center justify-center min-h-[160px]">
      <Activity size={28} className="text-muted mb-3 opacity-40" />
      <div className="text-sm font-medium text-muted">{title}</div>
      <div className="text-xs text-muted mt-1 opacity-60 text-center max-w-xs">{message}</div>
    </div>
  );
}

function EquityCurveChart({ curve }: { curve: { time: string; value: number }[] }) {
  if (curve.length < 2) {
    return (
      <EmptyState
        title="Equity curve unavailable"
        message="At least 2 portfolio snapshots are needed. Data accumulates as the bot trades."
      />
    );
  }

  const allSameDay = curve.every(
    (p) => new Date(p.time).toDateString() === new Date(curve[0].time).toDateString()
  );

  const data = curve.map((p) => ({
    time: allSameDay
      ? new Date(p.time).toLocaleTimeString('en-US', { hour: '2-digit', minute: '2-digit', hour12: false })
      : new Date(p.time).toLocaleDateString('en-US', { month: 'short', day: 'numeric' }),
    equity: p.value,
  }));

  return (
    <ResponsiveContainer width="100%" height={240}>
      <AreaChart data={data} margin={{ top: 4, right: 4, left: -10, bottom: 0 }}>
        <XAxis
          dataKey="time"
          tick={{ fontSize: 10, fill: '#6b7280' }}
          tickLine={false}
          interval={Math.max(0, Math.floor(data.length / 8) - 1)}
        />
        <YAxis
          tick={{ fontSize: 10, fill: '#6b7280' }}
          tickLine={false}
          tickFormatter={(v) => `$${(v / 1000).toFixed(1)}K`}
        />
        <CartesianGrid stroke="#1e1e2e" strokeDasharray="3 3" vertical={false} />
        <Tooltip
          contentStyle={{ background: '#1a1a28', border: '1px solid #2a2a3e', borderRadius: '6px', fontSize: '11px' }}
          formatter={(v: number) => [formatCurrency(v), 'Equity']}
        />
        <Area type="monotone" dataKey="equity" stroke="#00ff88" fill="rgba(0,255,136,0.07)" strokeWidth={2} />
      </AreaChart>
    </ResponsiveContainer>
  );
}

// ─── Strategy Breakdown ───────────────────────────────────────

interface StrategyRow {
  strategy: string;
  trades: number;
  wins: number;
  totalPnl: number;
  avgPnl: number;
  bestTrade: number;
  sharpe: number;
}

function StrategyBreakdown({ trades }: { trades: Trade[] }) {
  const rows = useMemo<StrategyRow[]>(() => {
    const closed = trades.filter((t) => t.status === 'CLOSED' && t.pnl != null);
    const byStrategy: Record<string, Trade[]> = {};

    for (const t of closed) {
      const key = t.strategy ?? 'Unknown';
      if (!byStrategy[key]) byStrategy[key] = [];
      byStrategy[key].push(t);
    }

    return Object.entries(byStrategy)
      .map(([strategy, stratTrades]) => {
        const pnls = stratTrades.map((t) => t.pnl ?? 0);
        const totalPnl = pnls.reduce((s, v) => s + v, 0);
        const avgPnl = pnls.length > 0 ? totalPnl / pnls.length : 0;
        const wins = stratTrades.filter((t) => (t.pnl ?? 0) > 0).length;
        const bestTrade = pnls.length > 0 ? Math.max(...pnls) : 0;

        // Simple Sharpe approximation: mean / stddev of PnL
        const mean = avgPnl;
        const variance =
          pnls.length > 1
            ? pnls.reduce((s, v) => s + (v - mean) ** 2, 0) / (pnls.length - 1)
            : 0;
        const stddev = Math.sqrt(variance);
        const sharpe = stddev > 0 ? mean / stddev : 0;

        return { strategy, trades: stratTrades.length, wins, totalPnl, avgPnl, bestTrade, sharpe };
      })
      .sort((a, b) => b.totalPnl - a.totalPnl);
  }, [trades]);

  if (rows.length === 0) return null;

  return (
    <div className="nexus-card p-4">
      <div className="flex items-center justify-between mb-4">
        <h3 className="font-medium text-sm text-white">Strategy Breakdown</h3>
        <BarChart2 size={14} className="text-muted" />
      </div>
      <div className="overflow-x-auto">
        <table className="w-full text-xs">
          <thead>
            <tr className="text-muted border-b border-border">
              <th className="text-left pb-2 font-medium">Strategy</th>
              <th className="text-right pb-2 font-medium">Trades</th>
              <th className="text-right pb-2 font-medium">Win Rate</th>
              <th className="text-right pb-2 font-medium">Total P&L</th>
              <th className="text-right pb-2 font-medium">Avg P&L</th>
              <th className="text-right pb-2 font-medium">Best Trade</th>
              <th className="text-right pb-2 font-medium">Sharpe</th>
            </tr>
          </thead>
          <tbody className="divide-y divide-border/40">
            {rows.map((row) => {
              const winRate = row.trades > 0 ? (row.wins / row.trades) * 100 : 0;
              return (
                <tr key={row.strategy} className="hover:bg-white/[0.02] transition-colors">
                  <td className="py-2 text-white font-medium">{row.strategy}</td>
                  <td className="py-2 text-right text-muted font-mono">{row.trades}</td>
                  <td className={cn('py-2 text-right font-mono', winRate >= 50 ? 'text-nexus-green' : 'text-nexus-yellow')}>
                    {winRate.toFixed(1)}%
                  </td>
                  <td className={cn('py-2 text-right font-mono font-bold', getPnlColor(row.totalPnl))}>
                    {row.totalPnl >= 0 ? '+' : ''}{formatCurrency(row.totalPnl)}
                  </td>
                  <td className={cn('py-2 text-right font-mono', getPnlColor(row.avgPnl))}>
                    {row.avgPnl >= 0 ? '+' : ''}{formatCurrency(row.avgPnl)}
                  </td>
                  <td className="py-2 text-right font-mono text-nexus-green">
                    +{formatCurrency(row.bestTrade)}
                  </td>
                  <td className={cn('py-2 text-right font-mono', row.sharpe >= 1 ? 'text-nexus-green' : row.sharpe >= 0 ? 'text-nexus-yellow' : 'text-nexus-red')}>
                    {row.sharpe.toFixed(2)}
                  </td>
                </tr>
              );
            })}
          </tbody>
        </table>
      </div>
    </div>
  );
}

// ─── Main Page ────────────────────────────────────────────────

export default function PerformancePage() {
  const [metrics, setMetrics] = useState<PerformanceMetrics | null>(null);
  const [equityCurve, setEquityCurve] = useState<{ time: string; value: number }[]>([]);
  const [trades, setTrades] = useState<Trade[]>([]);
  const [calendarData, setCalendarData] = useState<DayData[]>([]);
  const [loading, setLoading] = useState(true);

  useEffect(() => {
    const load = async () => {
      setLoading(true);
      const [m, curve, trds] = await Promise.all([
        getPerformanceMetrics(),
        getEquityCurve(90),
        getRecentTrades(50),
      ]);
      setMetrics(m);
      setEquityCurve(curve);
      setTrades(trds);
      setLoading(false);
    };
    load();
  }, []);

  useEffect(() => {
    getDailyPnLHistory(365).then(setCalendarData);
  }, []);

  // Sort trades for best/worst
  const closedTrades = trades.filter((t) => t.status === 'CLOSED' && t.pnl != null);
  const sortedByPnl = [...closedTrades].sort((a, b) => (b.pnl ?? 0) - (a.pnl ?? 0));
  const bestTrades = sortedByPnl.slice(0, 5);
  const worstTrades = sortedByPnl.slice(-5).reverse();

  return (
    <div className="space-y-4">
      {/* Key metrics */}
      <div className="grid grid-cols-6 gap-3">
        <MetricCard
          label="Total P&L"
          value={metrics ? formatCurrency(metrics.total_pnl) : null}
          color={metrics ? getPnlColor(metrics.total_pnl) : 'text-muted'}
        />
        <MetricCard
          label="Sharpe Ratio"
          value={metrics ? formatNumber(metrics.sharpe_ratio) : null}
          color={metrics && metrics.sharpe_ratio > 1.5 ? 'text-nexus-green' : 'text-nexus-yellow'}
          sub="vs. benchmark"
        />
        <MetricCard
          label="Sortino Ratio"
          value={metrics ? formatNumber(metrics.sortino_ratio) : null}
          color={metrics && metrics.sortino_ratio > 2 ? 'text-nexus-green' : 'text-nexus-yellow'}
        />
        <MetricCard
          label="Win Rate"
          value={metrics ? formatPercent(metrics.win_rate * 100, 1) : null}
          color={metrics && metrics.win_rate > 0.55 ? 'text-nexus-green' : 'text-nexus-yellow'}
          sub={metrics ? `${metrics.winning_trades}W / ${metrics.losing_trades}L` : 'Awaiting trades'}
        />
        <MetricCard
          label="Profit Factor"
          value={metrics ? formatNumber(metrics.profit_factor) : null}
          color={metrics && metrics.profit_factor > 2 ? 'text-nexus-green' : 'text-nexus-yellow'}
        />
        <MetricCard
          label="Max Drawdown"
          value={metrics ? formatPercent(metrics.max_drawdown, 1) : null}
          color="text-nexus-red"
          sub={metrics ? `Calmar: ${formatNumber(metrics.calmar_ratio)}` : undefined}
        />
      </div>

      {/* P&L Calendar heatmap */}
      <div className="nexus-card p-5">
        <div className="flex items-center gap-2 mb-4">
          <CalendarDays size={14} className="text-muted" />
          <h3 className="text-sm font-semibold text-white">Daily P&L Calendar (365 days)</h3>
        </div>
        <PnLCalendar data={calendarData} />
      </div>

      {/* Equity curve */}
      <div className="nexus-card p-4">
        <div className="flex items-center justify-between mb-4">
          <h3 className="font-medium text-sm text-white">Equity Curve</h3>
          <div className="flex items-center gap-1.5 text-xs">
            <div className="w-3 h-0.5 bg-nexus-green" />
            <span className="text-muted">NEXUS ALPHA</span>
          </div>
        </div>
        <EquityCurveChart curve={equityCurve} />
      </div>

      {/* Best / Worst trades */}
      <div className="grid grid-cols-2 gap-4">
        <div className="nexus-card p-4">
          <div className="flex items-center gap-2 mb-3">
            <Award size={14} className="text-nexus-green" />
            <h3 className="font-medium text-sm text-white">Best Trades</h3>
          </div>
          {bestTrades.length === 0 ? (
            <div className="text-xs text-muted py-4 text-center">No closed trades yet</div>
          ) : (
            <div className="space-y-2">
              {bestTrades.map((t) => (
                <div key={t.id} className="flex items-center justify-between text-xs">
                  <div>
                    <span className="text-white font-medium">{t.symbol}</span>
                    <span className="text-muted ml-2">{t.strategy ?? '—'}</span>
                  </div>
                  <span className="font-mono text-nexus-green font-bold">
                    +{formatCurrency(t.pnl ?? 0)}
                  </span>
                </div>
              ))}
            </div>
          )}
        </div>

        <div className="nexus-card p-4">
          <div className="flex items-center gap-2 mb-3">
            <TrendingUp size={14} className="text-nexus-red" style={{ transform: 'scaleY(-1)' }} />
            <h3 className="font-medium text-sm text-white">Worst Trades</h3>
          </div>
          {worstTrades.length === 0 ? (
            <div className="text-xs text-muted py-4 text-center">No closed trades yet</div>
          ) : (
            <div className="space-y-2">
              {worstTrades.map((t) => (
                <div key={t.id} className="flex items-center justify-between text-xs">
                  <div>
                    <span className="text-white font-medium">{t.symbol}</span>
                    <span className="text-muted ml-2">{t.strategy ?? '—'}</span>
                  </div>
                  <span className="font-mono text-nexus-red font-bold">
                    {formatCurrency(t.pnl ?? 0)}
                  </span>
                </div>
              ))}
            </div>
          )}
        </div>
      </div>

      {/* Strategy Breakdown — computed from closed trades */}
      {!loading && trades.length > 0 && <StrategyBreakdown trades={trades} />}

      {/* Full trade history */}
      <TradeTable trades={trades} title="Trade History" showExport />

      {/* No trades placeholder */}
      {!loading && trades.length === 0 && (
        <EmptyState
          title="No trade history"
          message="Trades appear here as the bot opens and closes positions."
        />
      )}
    </div>
  );
}
