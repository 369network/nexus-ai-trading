'use client';

import { useEffect, useState, useRef } from 'react';
import * as d3 from 'd3';
import {
  AreaChart,
  Area,
  BarChart,
  Bar,
  PieChart,
  Pie,
  Cell,
  XAxis,
  YAxis,
  ResponsiveContainer,
  Tooltip,
  ReferenceLine,
} from 'recharts';
import { Shield, AlertTriangle, CheckCircle2, XCircle, TrendingDown } from 'lucide-react';
import { RiskHeatmap } from '@/components/RiskHeatmap';
import { useNexusStore } from '@/lib/store';
import { getEquityCurve } from '@/lib/supabase';
import { cn, formatPercent, formatCurrency, formatTimeAgo } from '@/lib/utils';
import type { RiskLayer, CircuitBreaker } from '@/lib/types';

const RISK_LAYERS: RiskLayer[] = [
  {
    layer: 1,
    name: 'Signal Validation',
    description: 'Pre-trade signal quality checks',
    status: 'GREEN',
    checks: [
      { name: 'Confidence threshold', passed: true, current_value: 0.78, threshold: 0.65, message: 'OK' },
      { name: 'Strength minimum', passed: true, current_value: 72, threshold: 60, message: 'OK' },
      { name: 'Risk/Reward min', passed: true, current_value: 2.1, threshold: 1.5, message: 'OK' },
    ],
  },
  {
    layer: 2,
    name: 'Position Sizing',
    description: 'Kelly criterion & volatility adjustment',
    status: 'GREEN',
    checks: [
      { name: 'Kelly fraction max', passed: true, current_value: 0.04, threshold: 0.05, message: 'OK' },
      { name: 'Max position size', passed: true, current_value: 0.08, threshold: 0.10, message: 'OK' },
    ],
  },
  {
    layer: 3,
    name: 'Portfolio Limits',
    description: 'Exposure and concentration checks',
    status: 'YELLOW',
    checks: [
      { name: 'Total exposure', passed: false, current_value: 67, threshold: 60, message: 'Above 60% threshold' },
      { name: 'Single market max', passed: true, current_value: 28, threshold: 30, message: 'OK' },
      { name: 'Correlation limit', passed: true, current_value: 0.52, threshold: 0.70, message: 'OK' },
    ],
  },
  {
    layer: 4,
    name: 'Drawdown Control',
    description: 'Real-time drawdown monitoring',
    status: 'GREEN',
    checks: [
      { name: 'Daily drawdown', passed: true, current_value: -1.8, threshold: -3.0, message: 'OK' },
      { name: 'Max drawdown', passed: true, current_value: -8.4, threshold: -15.0, message: 'OK' },
      { name: 'Trailing stop active', passed: true, current_value: 1, threshold: 0, message: 'Active' },
    ],
  },
  {
    layer: 5,
    name: 'Market Conditions',
    description: 'Regime & liquidity checks',
    status: 'GREEN',
    checks: [
      { name: 'VIX threshold', passed: true, current_value: 18.4, threshold: 35.0, message: 'OK' },
      { name: 'Liquidity check', passed: true, current_value: 1, threshold: 1, message: 'Adequate' },
      { name: 'Correlation regime', passed: true, current_value: 0.42, threshold: 0.85, message: 'Normal' },
    ],
  },
];

const CIRCUIT_BREAKER_DATA: CircuitBreaker[] = [
  { id: '1', name: 'Daily Loss Limit', description: 'Stop trading if daily loss > 3%', enabled: true, triggered: false, threshold: 3, current_value: 1.8 },
  { id: '2', name: 'Max Drawdown', description: 'Halt if drawdown > 15%', enabled: true, triggered: false, threshold: 15, current_value: 8.4 },
  { id: '3', name: 'Position Concentration', description: 'Alert if single position > 10%', enabled: true, triggered: false, threshold: 10, current_value: 8.2 },
  { id: '4', name: 'Correlation Limit', description: 'Reduce sizing if avg corr > 0.7', enabled: true, triggered: false, threshold: 0.7, current_value: 0.52 },
  { id: '5', name: 'Volatility Spike', description: 'Reduce size on VIX > 35', enabled: true, triggered: false, threshold: 35, current_value: 18.4 },
  { id: '6', name: 'API Failure', description: 'Halt all trading on data feed loss', enabled: true, triggered: false, threshold: 0, current_value: 0 },
];

const MARKET_EXPOSURE = [
  { market: 'Crypto', exposure: 28, color: '#f7931a' },
  { market: 'Forex', exposure: 15, color: '#0088ff' },
  { market: 'Commodities', exposure: 12, color: '#ffaa00' },
  { market: 'Indian Stocks', exposure: 8, color: '#00ff88' },
  { market: 'US Stocks', exposure: 4, color: '#8844ff' },
  { market: 'Cash', exposure: 33, color: '#4b5563' },
];

function RiskLayerPanel({ layer }: { layer: RiskLayer }) {
  const [expanded, setExpanded] = useState(false);
  const statusConfig = {
    GREEN: { bg: 'bg-nexus-green/5', border: 'border-nexus-green/20', text: 'text-nexus-green', icon: CheckCircle2 },
    YELLOW: { bg: 'bg-nexus-yellow/5', border: 'border-nexus-yellow/20', text: 'text-nexus-yellow', icon: AlertTriangle },
    RED: { bg: 'bg-nexus-red/5', border: 'border-nexus-red/20', text: 'text-nexus-red', icon: XCircle },
  };
  const cfg = statusConfig[layer.status];
  const StatusIcon = cfg.icon;

  return (
    <div
      className={cn('nexus-card p-4 cursor-pointer border', cfg.bg, cfg.border)}
      onClick={() => setExpanded(!expanded)}
    >
      <div className="flex items-center justify-between">
        <div className="flex items-center gap-3">
          <div className={cn('w-7 h-7 rounded-full flex items-center justify-center text-xs font-bold', cfg.bg, cfg.text, 'border', cfg.border)}>
            {layer.layer}
          </div>
          <div>
            <div className="font-medium text-sm text-white">{layer.name}</div>
            <div className="text-xs text-muted">{layer.description}</div>
          </div>
        </div>
        <div className="flex items-center gap-2">
          <StatusIcon size={16} className={cfg.text} />
          <span className={cn('text-xs font-bold', cfg.text)}>{layer.status}</span>
          <span className="text-muted text-xs">{expanded ? '▲' : '▼'}</span>
        </div>
      </div>

      {expanded && (
        <div className="mt-3 space-y-2 border-t border-border pt-3">
          {layer.checks.map((check) => (
            <div key={check.name} className="flex items-center justify-between text-xs">
              <div className="flex items-center gap-2">
                {check.passed ? (
                  <CheckCircle2 size={12} className="text-nexus-green" />
                ) : (
                  <AlertTriangle size={12} className="text-nexus-yellow" />
                )}
                <span className="text-gray-300">{check.name}</span>
              </div>
              <div className="flex items-center gap-2">
                <span className={cn('font-mono', check.passed ? 'text-nexus-green' : 'text-nexus-yellow')}>
                  {check.current_value}
                </span>
                <span className="text-muted">/ {check.threshold}</span>
              </div>
            </div>
          ))}
        </div>
      )}
    </div>
  );
}

function CircuitBreakerCard({ cb }: { cb: CircuitBreaker }) {
  const pct = cb.threshold > 0 ? (cb.current_value / cb.threshold) * 100 : 0;
  const isWarning = pct > 70;
  const isTriggered = cb.triggered;

  return (
    <div
      className={cn(
        'nexus-card p-4 border',
        isTriggered ? 'border-nexus-red/30 bg-nexus-red/5' :
        isWarning ? 'border-nexus-yellow/20 bg-nexus-yellow/5' :
        'border-border'
      )}
    >
      <div className="flex items-center justify-between mb-2">
        <span className="text-xs font-medium text-white">{cb.name}</span>
        {isTriggered ? (
          <XCircle size={14} className="text-nexus-red" />
        ) : (
          <CheckCircle2 size={14} className="text-nexus-green" />
        )}
      </div>
      <p className="text-xs text-muted mb-3">{cb.description}</p>
      {cb.threshold > 0 && (
        <>
          <div className="flex justify-between text-xs mb-1">
            <span className="text-muted">
              Current: <span className={cn('font-mono', isWarning ? 'text-nexus-yellow' : 'text-white')}>
                {cb.current_value.toFixed(1)}
              </span>
            </span>
            <span className="text-muted">Limit: {cb.threshold}</span>
          </div>
          <div className="h-1.5 bg-border rounded-full overflow-hidden">
            <div
              className={cn('h-full rounded-full transition-all', isTriggered ? 'bg-nexus-red' : isWarning ? 'bg-nexus-yellow' : 'bg-nexus-green')}
              style={{ width: `${Math.min(100, pct)}%` }}
            />
          </div>
        </>
      )}
      {cb.last_trigger_time && (
        <div className="text-xs text-muted mt-2">Last triggered: {formatTimeAgo(cb.last_trigger_time)}</div>
      )}
    </div>
  );
}

function CorrelationMatrix() {
  const ref = useRef<SVGSVGElement>(null);
  const symbols = ['BTC', 'ETH', 'SOL', 'EURUSD', 'GOLD', 'OIL', 'NIFTY', 'SPX'];

  useEffect(() => {
    if (!ref.current) return;

    // Mock correlation matrix
    const correlations: number[][] = symbols.map((_, i) =>
      symbols.map((_, j) => {
        if (i === j) return 1;
        const base = Math.random() * 0.8 - 0.3;
        // Crypto pairs are more correlated
        if (i < 3 && j < 3) return 0.6 + Math.random() * 0.3;
        return base;
      })
    );

    const svg = d3.select(ref.current);
    svg.selectAll('*').remove();

    const margin = { top: 30, right: 10, bottom: 10, left: 40 };
    const width = (ref.current.clientWidth || 400) - margin.left - margin.right;
    const height = width;
    const cellSize = width / symbols.length;

    const g = svg
      .attr('width', width + margin.left + margin.right)
      .attr('height', height + margin.top + margin.bottom)
      .append('g')
      .attr('transform', `translate(${margin.left},${margin.top})`);

    const colorScale = d3.scaleLinear<string>()
      .domain([-1, 0, 1])
      .range(['#ff4444', '#1a1a28', '#00ff88']);

    // Cells
    symbols.forEach((row, i) => {
      symbols.forEach((col, j) => {
        const val = correlations[i][j];
        g.append('rect')
          .attr('x', j * cellSize)
          .attr('y', i * cellSize)
          .attr('width', cellSize - 2)
          .attr('height', cellSize - 2)
          .attr('rx', 3)
          .attr('fill', colorScale(val))
          .attr('opacity', 0.85);

        g.append('text')
          .attr('x', j * cellSize + cellSize / 2)
          .attr('y', i * cellSize + cellSize / 2)
          .attr('dy', '0.35em')
          .attr('text-anchor', 'middle')
          .attr('font-size', '9px')
          .attr('fill', Math.abs(val) > 0.5 ? '#fff' : '#9ca3af')
          .text(val.toFixed(2));
      });
    });

    // Row labels
    symbols.forEach((sym, i) => {
      g.append('text')
        .attr('x', -5)
        .attr('y', i * cellSize + cellSize / 2)
        .attr('dy', '0.35em')
        .attr('text-anchor', 'end')
        .attr('font-size', '10px')
        .attr('fill', '#9ca3af')
        .text(sym);
    });

    // Col labels
    symbols.forEach((sym, j) => {
      g.append('text')
        .attr('x', j * cellSize + cellSize / 2)
        .attr('y', -8)
        .attr('text-anchor', 'middle')
        .attr('font-size', '10px')
        .attr('fill', '#9ca3af')
        .text(sym);
    });
  }, []);

  return (
    <div className="nexus-card p-4">
      <h3 className="font-medium text-sm text-white mb-3">Correlation Matrix</h3>
      <svg ref={ref} className="w-full" />
    </div>
  );
}

function DrawdownChart({ curve }: { curve: { time: string; value: number }[] }) {
  const peak = curve.reduce((max, p) => Math.max(max, p.value), 0);

  const data = curve.map((p) => ({
    time: new Date(p.time).toLocaleDateString('en-US', { month: 'short', day: 'numeric' }),
    equity: p.value,
    drawdown: p.value < peak ? ((p.value - peak) / peak) * 100 : 0,
  }));

  return (
    <div className="nexus-card p-4 col-span-2">
      <div className="flex items-center justify-between mb-4">
        <h3 className="font-medium text-sm text-white">Equity Curve & Drawdown</h3>
        <div className="flex items-center gap-3 text-xs">
          <div className="flex items-center gap-1.5">
            <div className="w-3 h-0.5 bg-nexus-blue" />
            <span className="text-muted">Equity</span>
          </div>
          <div className="flex items-center gap-1.5">
            <div className="w-3 h-0.5 bg-nexus-red" />
            <span className="text-muted">Drawdown</span>
          </div>
        </div>
      </div>
      <ResponsiveContainer width="100%" height={200}>
        <AreaChart data={data} margin={{ top: 4, right: 4, left: -10, bottom: 0 }}>
          <XAxis dataKey="time" tick={{ fontSize: 10, fill: '#6b7280' }} tickLine={false} interval={Math.floor(data.length / 6)} />
          <YAxis yAxisId="equity" tick={{ fontSize: 10, fill: '#6b7280' }} tickLine={false} tickFormatter={(v) => `$${(v / 1000).toFixed(0)}K`} />
          <YAxis yAxisId="dd" orientation="right" tick={{ fontSize: 10, fill: '#6b7280' }} tickLine={false} tickFormatter={(v) => `${v.toFixed(1)}%`} />
          <Tooltip
            contentStyle={{ background: '#1a1a28', border: '1px solid #2a2a3e', borderRadius: '6px', fontSize: '11px' }}
          />
          <Area yAxisId="equity" type="monotone" dataKey="equity" stroke="#0088ff" fill="rgba(0, 136, 255, 0.08)" strokeWidth={1.5} />
          <Area yAxisId="dd" type="monotone" dataKey="drawdown" stroke="#ff4444" fill="rgba(255, 68, 68, 0.12)" strokeWidth={1.5} />
          <ReferenceLine yAxisId="dd" y={-10} stroke="#ff4444" strokeDasharray="3 3" opacity={0.5} />
        </AreaChart>
      </ResponsiveContainer>
    </div>
  );
}

export default function RiskPage() {
  const { portfolioState, riskEvents, activeTrades } = useNexusStore();
  const [equityCurve, setEquityCurve] = useState<{ time: string; value: number }[]>([]);

  useEffect(() => {
    getEquityCurve(30).then(setEquityCurve);
  }, []);

  return (
    <div className="space-y-4">
      {/* Risk Layer Status */}
      <div>
        <h2 className="font-medium text-sm text-muted uppercase tracking-wider mb-3">
          Risk Layer Status
        </h2>
        <div className="grid grid-cols-5 gap-3">
          {RISK_LAYERS.map((layer) => (
            <RiskLayerPanel key={layer.layer} layer={layer} />
          ))}
        </div>
      </div>

      {/* Circuit Breakers */}
      <div>
        <h2 className="font-medium text-sm text-muted uppercase tracking-wider mb-3">
          Circuit Breakers
        </h2>
        <div className="grid grid-cols-3 gap-3">
          {CIRCUIT_BREAKER_DATA.map((cb) => (
            <CircuitBreakerCard key={cb.id} cb={cb} />
          ))}
        </div>
      </div>

      {/* Drawdown chart + Exposure */}
      <div className="grid grid-cols-3 gap-4">
        <DrawdownChart curve={equityCurve.length ? equityCurve : portfolioState.equityCurve} />

        {/* Portfolio exposure */}
        <div className="nexus-card p-4">
          <h3 className="font-medium text-sm text-white mb-4">Portfolio Exposure</h3>
          <ResponsiveContainer width="100%" height={160}>
            <PieChart>
              <Pie
                data={MARKET_EXPOSURE}
                cx="50%"
                cy="50%"
                innerRadius={45}
                outerRadius={70}
                dataKey="exposure"
                paddingAngle={2}
              >
                {MARKET_EXPOSURE.map((entry, i) => (
                  <Cell key={i} fill={entry.color} opacity={0.85} />
                ))}
              </Pie>
              <Tooltip
                contentStyle={{ background: '#1a1a28', border: '1px solid #2a2a3e', borderRadius: '6px', fontSize: '11px' }}
                formatter={(v: number) => [`${v}%`, '']}
              />
            </PieChart>
          </ResponsiveContainer>
          <div className="space-y-1 mt-2">
            {MARKET_EXPOSURE.map((m) => (
              <div key={m.market} className="flex items-center justify-between text-xs">
                <div className="flex items-center gap-1.5">
                  <div className="w-2 h-2 rounded-full" style={{ backgroundColor: m.color }} />
                  <span className="text-gray-300">{m.market}</span>
                </div>
                <span className="font-mono text-white">{m.exposure}%</span>
              </div>
            ))}
          </div>
        </div>
      </div>

      {/* Risk Heatmap */}
      <RiskHeatmap activeTrades={activeTrades} />

      {/* Recent risk events */}
      {riskEvents.length > 0 && (
        <div className="nexus-card p-4">
          <h3 className="font-medium text-sm text-white mb-4">Recent Risk Events</h3>
          <div className="space-y-2">
            {riskEvents.slice(0, 10).map((event) => (
              <div key={event.id} className="flex items-center justify-between p-2 rounded-lg bg-white/2 border border-border text-xs">
                <div className="flex items-center gap-3">
                  <span
                    className={cn(
                      'badge',
                      event.severity === 'CRITICAL' ? 'bg-nexus-red/10 text-nexus-red' :
                      event.severity === 'HIGH' ? 'bg-orange-500/10 text-orange-400' :
                      'bg-nexus-yellow/10 text-nexus-yellow'
                    )}
                  >
                    {event.severity}
                  </span>
                  <span className="text-white">{event.description}</span>
                </div>
                <span className="text-muted">{formatTimeAgo(event.created_at)}</span>
              </div>
            ))}
          </div>
        </div>
      )}
    </div>
  );
}
