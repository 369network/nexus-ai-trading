'use client';

import { useEffect, useState } from 'react';
import {
  LineChart,
  Line,
  BarChart,
  Bar,
  AreaChart,
  Area,
  XAxis,
  YAxis,
  ResponsiveContainer,
  Tooltip,
  Legend,
  CartesianGrid,
  Cell,
  ReferenceLine,
} from 'recharts';
import { TrendingUp, Award, BarChart2, Calendar } from 'lucide-react';
import { TradeTable } from '@/components/TradeTable';
import { getEquityCurve, getPerformanceMetrics, getRecentTrades } from '@/lib/supabase';
import { cn, formatCurrency, formatPercent, formatNumber, getPnlColor } from '@/lib/utils';
import type { PerformanceMetrics, Trade, MonthlyPnL, StrategyPerformance } from '@/lib/types';

// Mock monthly P&L for heatmap
function generateMonthlyPnL(): MonthlyPnL[] {
  const months = [];
  for (let m = 0; m < 12; m++) {
    const pnl = (Math.random() - 0.4) * 8000;
    months.push({
      year: 2024,
      month: m,
      pnl,
      pnl_pct: pnl / 100000 * 100,
      trades: Math.floor(15 + Math.random() * 35),
    });
  }
  return months;
}

const STRATEGY_DATA: StrategyPerformance[] = [
  { strategy: 'Momentum Breakout', market: 'crypto', total_trades: 84, win_rate: 0.67, total_pnl: 8420, avg_pnl_per_trade: 100.2, sharpe: 1.92, max_drawdown: -6.2 },
  { strategy: 'Session Breakout', market: 'forex', total_trades: 62, win_rate: 0.61, total_pnl: 4180, avg_pnl_per_trade: 67.4, sharpe: 1.54, max_drawdown: -4.8 },
  { strategy: 'Mean Reversion', market: 'us_stocks', total_trades: 45, win_rate: 0.71, total_pnl: 3240, avg_pnl_per_trade: 72.0, sharpe: 1.78, max_drawdown: -3.1 },
  { strategy: 'Seasonal Trend', market: 'commodities', total_trades: 28, win_rate: 0.64, total_pnl: 1820, avg_pnl_per_trade: 65.0, sharpe: 1.22, max_drawdown: -5.4 },
  { strategy: 'ORB Breakout', market: 'indian_stocks', total_trades: 38, win_rate: 0.58, total_pnl: 1680, avg_pnl_per_trade: 44.2, sharpe: 1.15, max_drawdown: -3.9 },
];

const MONTH_NAMES = ['Jan', 'Feb', 'Mar', 'Apr', 'May', 'Jun', 'Jul', 'Aug', 'Sep', 'Oct', 'Nov', 'Dec'];

function MetricCard({
  label,
  value,
  sub,
  color = 'text-white',
  trend,
}: {
  label: string;
  value: string;
  sub?: string;
  color?: string;
  trend?: 'up' | 'down' | 'neutral';
}) {
  return (
    <div className="nexus-card nexus-card-hover p-4">
      <div className="metric-label mb-2">{label}</div>
      <div className={cn('metric-value', color)}>{value}</div>
      {sub && <div className="text-xs text-muted mt-1">{sub}</div>}
      {trend && (
        <div className={cn('text-xs mt-1', trend === 'up' ? 'text-nexus-green' : trend === 'down' ? 'text-nexus-red' : 'text-muted')}>
          {trend === 'up' ? '▲' : trend === 'down' ? '▼' : '◆'} vs last period
        </div>
      )}
    </div>
  );
}

function MonthlyHeatmap({ data }: { data: MonthlyPnL[] }) {
  const maxAbs = Math.max(...data.map((d) => Math.abs(d.pnl_pct)));

  const getColor = (val: number) => {
    const intensity = Math.abs(val) / maxAbs;
    if (val > 0) return `rgba(0, 255, 136, ${0.15 + intensity * 0.7})`;
    return `rgba(255, 68, 68, ${0.15 + intensity * 0.7})`;
  };

  return (
    <div className="nexus-card p-4">
      <div className="flex items-center justify-between mb-4">
        <h3 className="font-medium text-sm text-white">Monthly P&L Heatmap (2024)</h3>
        <Calendar size={14} className="text-muted" />
      </div>
      <div className="grid grid-cols-12 gap-1.5">
        {data.map((d) => (
          <div key={d.month} className="flex flex-col items-center gap-1">
            <div
              className="w-full rounded-md p-2 text-center cursor-pointer hover:opacity-80 transition-opacity"
              style={{ backgroundColor: getColor(d.pnl_pct) }}
              title={`${MONTH_NAMES[d.month]}: ${formatCurrency(d.pnl)} (${formatPercent(d.pnl_pct)})`}
            >
              <div
                className={cn('text-xs font-mono font-bold', d.pnl_pct >= 0 ? 'text-nexus-green' : 'text-nexus-red')}
              >
                {d.pnl_pct >= 0 ? '+' : ''}{d.pnl_pct.toFixed(1)}%
              </div>
            </div>
            <span className="text-xs text-muted">{MONTH_NAMES[d.month].slice(0, 3)}</span>
            <span className="text-xs text-muted">{d.trades}T</span>
          </div>
        ))}
      </div>
      <div className="flex justify-between mt-3 text-xs text-muted">
        <span>Best: <span className="text-nexus-green">{MONTH_NAMES[data.reduce((bestIdx, d, i) => d.pnl_pct > data[bestIdx].pnl_pct ? i : bestIdx, 0)]}</span></span>
        <span>Worst: <span className="text-nexus-red">{MONTH_NAMES[data.reduce((worstIdx, d, i) => d.pnl_pct < data[worstIdx].pnl_pct ? i : worstIdx, 0)]}</span></span>
      </div>
    </div>
  );
}

function DurationHistogram() {
  const bins = [
    { label: '<1h', count: 28 },
    { label: '1-4h', count: 52 },
    { label: '4-12h', count: 71 },
    { label: '12-24h', count: 43 },
    { label: '1-3d', count: 32 },
    { label: '3-7d', count: 15 },
    { label: '>7d', count: 6 },
  ];

  return (
    <div className="nexus-card p-4">
      <h3 className="font-medium text-sm text-white mb-4">Trade Duration Distribution</h3>
      <ResponsiveContainer width="100%" height={160}>
        <BarChart data={bins} margin={{ top: 4, right: 4, left: -15, bottom: 0 }}>
          <XAxis dataKey="label" tick={{ fontSize: 10, fill: '#6b7280' }} tickLine={false} />
          <YAxis tick={{ fontSize: 10, fill: '#6b7280' }} tickLine={false} />
          <Tooltip
            contentStyle={{ background: '#1a1a28', border: '1px solid #2a2a3e', borderRadius: '6px', fontSize: '11px' }}
          />
          <Bar dataKey="count" fill="#0088ff" opacity={0.8} radius={[3, 3, 0, 0]} />
        </BarChart>
      </ResponsiveContainer>
    </div>
  );
}

function MarketPnLBreakdown() {
  const markets = [
    { market: 'Crypto', pnl: 8420, color: '#f7931a' },
    { market: 'Forex', pnl: 4180, color: '#0088ff' },
    { market: 'US Stocks', pnl: 3240, color: '#8844ff' },
    { market: 'Commodities', pnl: 1820, color: '#ffaa00' },
    { market: 'Indian Stocks', pnl: 1680, color: '#00ff88' },
  ];

  return (
    <div className="nexus-card p-4">
      <h3 className="font-medium text-sm text-white mb-4">P&L by Market</h3>
      <ResponsiveContainer width="100%" height={200}>
        <BarChart data={markets} layout="vertical" margin={{ top: 0, right: 30, left: 60, bottom: 0 }}>
          <XAxis type="number" tick={{ fontSize: 10, fill: '#6b7280' }} tickLine={false} tickFormatter={(v) => `$${(v / 1000).toFixed(0)}K`} />
          <YAxis type="category" dataKey="market" tick={{ fontSize: 10, fill: '#9ca3af' }} tickLine={false} />
          <Tooltip
            contentStyle={{ background: '#1a1a28', border: '1px solid #2a2a3e', borderRadius: '6px', fontSize: '11px' }}
            formatter={(v: number) => [formatCurrency(v), 'P&L']}
          />
          <Bar dataKey="pnl" radius={[0, 4, 4, 0]}>
            {markets.map((entry, i) => (
              <Cell key={i} fill={entry.color} opacity={0.8} />
            ))}
          </Bar>
        </BarChart>
      </ResponsiveContainer>
    </div>
  );
}

export default function PerformancePage() {
  const [metrics, setMetrics] = useState<PerformanceMetrics | null>(null);
  const [equityCurve, setEquityCurve] = useState<{ time: string; value: number }[]>([]);
  const [trades, setTrades] = useState<Trade[]>([]);
  const [monthlyPnL] = useState(generateMonthlyPnL());

  useEffect(() => {
    const load = async () => {
      const [m, curve, trds] = await Promise.all([
        getPerformanceMetrics(),
        getEquityCurve(90),
        getRecentTrades(50),
      ]);
      if (m) setMetrics(m);
      setEquityCurve(curve);
      setTrades(trds);
    };
    load();
  }, []);

  const m = metrics ?? {
    total_trades: 247,
    winning_trades: 158,
    losing_trades: 89,
    win_rate: 0.640,
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

  // Benchmark comparison (buy & hold SPX)
  const benchmarkData = equityCurve.map((p, i) => ({
    time: new Date(p.time).toLocaleDateString('en-US', { month: 'short', day: 'numeric' }),
    equity: p.value,
    benchmark: 100000 * (1 + 0.12 * i / equityCurve.length), // SPX proxy
  }));

  // Best/worst trades
  const sortedTrades = [...trades].sort((a, b) => (b.pnl ?? 0) - (a.pnl ?? 0));
  const bestTrades = sortedTrades.slice(0, 5);
  const worstTrades = sortedTrades.slice(-5).reverse();

  return (
    <div className="space-y-4">
      {/* Key metrics */}
      <div className="grid grid-cols-6 gap-3">
        <MetricCard
          label="Total P&L"
          value={formatCurrency(m.total_pnl)}
          color="text-nexus-green"
          trend="up"
        />
        <MetricCard
          label="Sharpe Ratio"
          value={formatNumber(m.sharpe_ratio)}
          color={m.sharpe_ratio > 1.5 ? 'text-nexus-green' : 'text-nexus-yellow'}
          sub="vs. benchmark"
        />
        <MetricCard
          label="Sortino Ratio"
          value={formatNumber(m.sortino_ratio)}
          color={m.sortino_ratio > 2 ? 'text-nexus-green' : 'text-nexus-yellow'}
        />
        <MetricCard
          label="Win Rate"
          value={formatPercent(m.win_rate * 100, 1)}
          color={m.win_rate > 0.55 ? 'text-nexus-green' : 'text-nexus-yellow'}
          sub={`${m.winning_trades}W / ${m.losing_trades}L`}
        />
        <MetricCard
          label="Profit Factor"
          value={formatNumber(m.profit_factor)}
          color={m.profit_factor > 2 ? 'text-nexus-green' : 'text-nexus-yellow'}
        />
        <MetricCard
          label="Max Drawdown"
          value={formatPercent(m.max_drawdown, 1)}
          color="text-nexus-red"
          sub={`Calmar: ${formatNumber(m.calmar_ratio)}`}
        />
      </div>

      {/* Equity curve */}
      <div className="nexus-card p-4">
        <div className="flex items-center justify-between mb-4">
          <h3 className="font-medium text-sm text-white">Equity Curve vs Benchmark</h3>
          <div className="flex items-center gap-4 text-xs">
            <div className="flex items-center gap-1.5">
              <div className="w-3 h-0.5 bg-nexus-green" />
              <span className="text-muted">NEXUS ALPHA</span>
            </div>
            <div className="flex items-center gap-1.5">
              <div className="w-3 h-0.5 bg-muted border-dashed border-t border-muted" />
              <span className="text-muted">S&P 500</span>
            </div>
          </div>
        </div>
        <ResponsiveContainer width="100%" height={240}>
          <AreaChart data={benchmarkData} margin={{ top: 4, right: 4, left: -10, bottom: 0 }}>
            <XAxis dataKey="time" tick={{ fontSize: 10, fill: '#6b7280' }} tickLine={false} interval={Math.floor(benchmarkData.length / 8)} />
            <YAxis tick={{ fontSize: 10, fill: '#6b7280' }} tickLine={false} tickFormatter={(v) => `$${(v / 1000).toFixed(0)}K`} />
            <CartesianGrid stroke="#1e1e2e" strokeDasharray="3 3" vertical={false} />
            <Tooltip
              contentStyle={{ background: '#1a1a28', border: '1px solid #2a2a3e', borderRadius: '6px', fontSize: '11px' }}
              formatter={(v: number, name: string) => [formatCurrency(v), name === 'equity' ? 'NEXUS ALPHA' : 'S&P 500']}
            />
            <Area type="monotone" dataKey="benchmark" stroke="#6b7280" fill="rgba(107, 114, 128, 0.05)" strokeWidth={1} strokeDasharray="4 2" />
            <Area type="monotone" dataKey="equity" stroke="#00ff88" fill="rgba(0, 255, 136, 0.07)" strokeWidth={2} />
          </AreaChart>
        </ResponsiveContainer>
      </div>

      {/* Strategy comparison */}
      <div className="nexus-card p-4">
        <div className="flex items-center justify-between mb-4">
          <h3 className="font-medium text-sm text-white">Strategy Comparison</h3>
          <BarChart2 size={14} className="text-muted" />
        </div>
        <table className="nexus-table">
          <thead>
            <tr>
              <th>Strategy</th>
              <th>Market</th>
              <th className="text-right">Trades</th>
              <th className="text-right">Win Rate</th>
              <th className="text-right">Total P&L</th>
              <th className="text-right">Avg / Trade</th>
              <th className="text-right">Sharpe</th>
              <th className="text-right">Max DD</th>
            </tr>
          </thead>
          <tbody>
            {STRATEGY_DATA.map((s) => (
              <tr key={s.strategy}>
                <td className="font-medium text-white">{s.strategy}</td>
                <td className="text-muted capitalize">{s.market.replace('_', ' ')}</td>
                <td className="text-right font-mono">{s.total_trades}</td>
                <td className={cn('text-right font-mono', s.win_rate > 0.6 ? 'text-nexus-green' : 'text-nexus-yellow')}>
                  {formatPercent(s.win_rate * 100, 1)}
                </td>
                <td className={cn('text-right font-mono font-medium', getPnlColor(s.total_pnl))}>
                  {formatCurrency(s.total_pnl)}
                </td>
                <td className={cn('text-right font-mono text-xs', getPnlColor(s.avg_pnl_per_trade))}>
                  {formatCurrency(s.avg_pnl_per_trade)}
                </td>
                <td className={cn('text-right font-mono', s.sharpe > 1.5 ? 'text-nexus-green' : 'text-nexus-yellow')}>
                  {s.sharpe.toFixed(2)}
                </td>
                <td className="text-right font-mono text-nexus-red">{formatPercent(s.max_drawdown)}</td>
              </tr>
            ))}
          </tbody>
        </table>
      </div>

      {/* Monthly heatmap + duration + market breakdown */}
      <div className="grid grid-cols-3 gap-4">
        <MonthlyHeatmap data={monthlyPnL} />
        <div className="space-y-4">
          <DurationHistogram />
          <MarketPnLBreakdown />
        </div>

        {/* Best / Worst trades */}
        <div className="space-y-4">
          <div className="nexus-card p-4">
            <div className="flex items-center gap-2 mb-3">
              <Award size={14} className="text-nexus-green" />
              <h3 className="font-medium text-sm text-white">Best Trades</h3>
            </div>
            <div className="space-y-2">
              {bestTrades.slice(0, 5).map((t, i) => (
                <div key={t.id} className="flex items-center justify-between text-xs">
                  <div>
                    <span className="text-white font-medium">{t.symbol}</span>
                    <span className="text-muted ml-2">{t.strategy}</span>
                  </div>
                  <span className="font-mono text-nexus-green font-bold">
                    +{formatCurrency(t.pnl ?? 0)}
                  </span>
                </div>
              ))}
            </div>
          </div>

          <div className="nexus-card p-4">
            <div className="flex items-center gap-2 mb-3">
              <TrendingUp size={14} className="text-nexus-red" style={{ transform: 'scaleY(-1)' }} />
              <h3 className="font-medium text-sm text-white">Worst Trades</h3>
            </div>
            <div className="space-y-2">
              {worstTrades.slice(0, 5).map((t, i) => (
                <div key={t.id} className="flex items-center justify-between text-xs">
                  <div>
                    <span className="text-white font-medium">{t.symbol}</span>
                    <span className="text-muted ml-2">{t.strategy}</span>
                  </div>
                  <span className="font-mono text-nexus-red font-bold">
                    {formatCurrency(t.pnl ?? 0)}
                  </span>
                </div>
              ))}
            </div>
          </div>
        </div>
      </div>

      {/* Full trade history */}
      <TradeTable trades={trades} title="Trade History" showExport />
    </div>
  );
}
