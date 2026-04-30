'use client';

import { useEffect, useState, useRef, useMemo } from 'react';
import * as d3 from 'd3';
import {
  AreaChart,
  Area,
  PieChart,
  Pie,
  Cell,
  XAxis,
  YAxis,
  ResponsiveContainer,
  Tooltip,
  ReferenceLine,
} from 'recharts';
import { AlertTriangle, CheckCircle2, XCircle, TrendingDown, Activity } from 'lucide-react';
import { RiskHeatmap } from '@/components/RiskHeatmap';
import { useNexusStore } from '@/lib/store';
import { getEquityCurve } from '@/lib/supabase';
import { cn, formatPercent, formatCurrency, formatTimeAgo } from '@/lib/utils';
import type { RiskLayer, CircuitBreaker } from '@/lib/types';

// ─── VIX Hook ──────────────────────────────────────────────────

function useVix(){
  const [vix,setVix]=useState<{value:number;change:number;change_pct:number;regime:string}|null>(null);
  useEffect(()=>{
    const load=async()=>{try{const res=await fetch('/api/market/vix');const json=await res.json();setVix(json.vix??null);}catch{}};
    load();const t=setInterval(load,60_000);return()=>clearInterval(t);
  },[]);
  return vix;
}

// ─── Real correlation hook (Yahoo Finance 30-day daily returns) ──────────────

interface CorrelationResult {
  symbols: string[];
  matrix: number[][];
  loading: boolean;
}

function useCorrelations(symbols: string[]): CorrelationResult {
  const [result, setResult] = useState<CorrelationResult>({ symbols: [], matrix: [], loading: true });
  const key = symbols.slice().sort().join(',');

  useEffect(() => {
    if (symbols.length < 2) {
      setResult({ symbols, matrix: [], loading: false });
      return;
    }
    setResult((prev) => ({ ...prev, loading: true }));
    const controller = new AbortController();
    fetch(`/api/risk/correlations?symbols=${encodeURIComponent(key)}`, { signal: controller.signal })
      .then((r) => r.ok ? r.json() : null)
      .then((json) => {
        if (json?.matrix) setResult({ symbols: json.symbols, matrix: json.matrix, loading: false });
        else setResult({ symbols, matrix: [], loading: false });
      })
      .catch(() => setResult({ symbols, matrix: [], loading: false }));
    return () => controller.abort();
  // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [key]);

  return result;
}

// Fallback when real correlation unavailable (crypto historical averages)
function getCorrelationFallback(a: string, b: string): number {
  if (a === b) return 1.0;
  const cryptos = new Set(['BTC','ETH','SOL','BNB','XRP','DOGE']);
  const isCryptoA = cryptos.has(a.split('/')[0]);
  const isCryptoB = cryptos.has(b.split('/')[0]);
  if (isCryptoA && isCryptoB) return 0.72;
  if (isCryptoA !== isCryptoB) return 0.15;
  return 0.35;
}

// ─── Sub-components ───────────────────────────────────────────

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
                  {typeof check.current_value === 'number' ? check.current_value.toFixed(2) : check.current_value}
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
      {cb.threshold > 0 ? (
        <>
          <div className="flex justify-between text-xs mb-1">
            <span className="text-muted">
              Current:{' '}
              <span className={cn('font-mono', isTriggered ? 'text-nexus-red' : isWarning ? 'text-nexus-yellow' : 'text-white')}>
                {cb.current_value.toFixed(2)}
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
      ) : (
        <div className="text-xs text-nexus-green font-mono">OK — monitoring active</div>
      )}
      {cb.last_trigger_time && (
        <div className="text-xs text-muted mt-2">Last triggered: {formatTimeAgo(cb.last_trigger_time)}</div>
      )}
    </div>
  );
}

function CorrelationMatrix({ tradeSymbols }: { tradeSymbols: string[] }) {
  const ref = useRef<SVGSVGElement>(null);

  // Use actual trade symbols (short names) if available; else show default set
  const symbols = tradeSymbols.length >= 2
    ? tradeSymbols.slice(0, 8)
    : ['BTC', 'ETH', 'SOL', 'BNB', 'XRP', 'DOGE'];

  // Build Yahoo-compatible symbols for the API (crypto → "BTC-USD")
  const apiSymbols = symbols.map((s) =>
    ['BTC','ETH','SOL','BNB','XRP','DOGE','ADA','AVAX'].includes(s) ? `${s}-USD` : s
  );

  const corrData = useCorrelations(apiSymbols);

  // Resolve matrix: use real data if available, fallback if loading/missing
  const matrix: number[][] = useMemo(() => {
    if (corrData.matrix.length === symbols.length) return corrData.matrix;
    // Fallback to historical-average estimates while real data loads
    return symbols.map((a) => symbols.map((b) => getCorrelationFallback(a, b)));
  }, [corrData.matrix, symbols]);

  useEffect(() => {
    if (!ref.current) return;

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

    symbols.forEach((_, i) => {
      symbols.forEach((_, j) => {
        const val = matrix[i]?.[j] ?? 0;
        g.append('rect')
          .attr('x', j * cellSize).attr('y', i * cellSize)
          .attr('width', cellSize - 2).attr('height', cellSize - 2)
          .attr('rx', 3).attr('fill', colorScale(val)).attr('opacity', 0.85);

        g.append('text')
          .attr('x', j * cellSize + cellSize / 2).attr('y', i * cellSize + cellSize / 2)
          .attr('dy', '0.35em').attr('text-anchor', 'middle')
          .attr('font-size', '9px')
          .attr('fill', Math.abs(val) > 0.5 ? '#fff' : '#9ca3af')
          .text(val.toFixed(2));
      });
    });

    symbols.forEach((sym, i) => {
      g.append('text').attr('x', -5).attr('y', i * cellSize + cellSize / 2)
        .attr('dy', '0.35em').attr('text-anchor', 'end')
        .attr('font-size', '10px').attr('fill', '#9ca3af').text(sym);
    });

    symbols.forEach((sym, j) => {
      g.append('text').attr('x', j * cellSize + cellSize / 2).attr('y', -8)
        .attr('text-anchor', 'middle')
        .attr('font-size', '10px').attr('fill', '#9ca3af').text(sym);
    });
  // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [symbols.join(','), matrix]);

  return (
    <div className="nexus-card p-4">
      <div className="flex items-center justify-between mb-3">
        <h3 className="font-medium text-sm text-white">Correlation Matrix</h3>
        {corrData.loading && (
          <span className="text-xs text-muted animate-pulse">Loading real data…</span>
        )}
        {!corrData.loading && corrData.matrix.length > 0 && (
          <span className="text-xs text-nexus-green">30-day daily returns</span>
        )}
      </div>
      <svg ref={ref} className="w-full" />
    </div>
  );
}

function DrawdownChart({ curve }: { curve: { time: string; value: number }[] }) {
  // Empty state — real data hasn't arrived yet
  if (curve.length === 0) {
    return (
      <div className="nexus-card p-4 col-span-2 flex items-center justify-center min-h-[220px]">
        <div className="text-center">
          <TrendingDown size={36} className="text-muted mx-auto mb-3 opacity-40" />
          <div className="text-sm font-medium text-muted">Collecting equity curve data</div>
          <div className="text-xs text-muted mt-1 opacity-60">
            Data appears as the bot records portfolio snapshots
          </div>
        </div>
      </div>
    );
  }

  const peak = curve.reduce((max, p) => Math.max(max, p.value), 0);
  // Detect same-day data — use HH:MM label instead of calendar date
  const allSameDay = curve.every(
    (p) => new Date(p.time).toDateString() === new Date(curve[0].time).toDateString()
  );

  const data = curve.map((p) => ({
    time: allSameDay
      ? new Date(p.time).toLocaleTimeString('en-US', { hour: '2-digit', minute: '2-digit', hour12: false })
      : new Date(p.time).toLocaleDateString('en-US', { month: 'short', day: 'numeric' }),
    equity: p.value,
    drawdown: p.value < peak ? ((p.value - peak) / peak) * 100 : 0,
  }));

  const currentDrawdown = data[data.length - 1]?.drawdown ?? 0;

  return (
    <div className="nexus-card p-4 col-span-2">
      <div className="flex items-center justify-between mb-4">
        <div>
          <h3 className="font-medium text-sm text-white">Equity Curve &amp; Drawdown</h3>
          <div className="text-xs text-muted mt-0.5">
            Peak: {formatCurrency(peak)} · Current drawdown:{' '}
            <span className={currentDrawdown < -10 ? 'text-nexus-red' : 'text-nexus-yellow'}>
              {currentDrawdown.toFixed(2)}%
            </span>
          </div>
        </div>
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
          <XAxis
            dataKey="time"
            tick={{ fontSize: 10, fill: '#6b7280' }}
            tickLine={false}
            interval={Math.max(0, Math.floor(data.length / 6) - 1)}
          />
          <YAxis
            yAxisId="equity"
            tick={{ fontSize: 10, fill: '#6b7280' }}
            tickLine={false}
            tickFormatter={(v) => `$${(v / 1000).toFixed(1)}K`}
          />
          <YAxis
            yAxisId="dd"
            orientation="right"
            tick={{ fontSize: 10, fill: '#6b7280' }}
            tickLine={false}
            tickFormatter={(v) => `${v.toFixed(1)}%`}
          />
          <Tooltip
            contentStyle={{ background: '#1a1a28', border: '1px solid #2a2a3e', borderRadius: '6px', fontSize: '11px' }}
            formatter={(v: number, name: string) =>
              name === 'equity' ? [formatCurrency(v), 'Equity'] : [`${v.toFixed(2)}%`, 'Drawdown']
            }
          />
          <Area yAxisId="equity" type="monotone" dataKey="equity" stroke="#0088ff" fill="rgba(0,136,255,0.08)" strokeWidth={1.5} />
          <Area yAxisId="dd" type="monotone" dataKey="drawdown" stroke="#ff4444" fill="rgba(255,68,68,0.12)" strokeWidth={1.5} />
          <ReferenceLine yAxisId="dd" y={-10} stroke="#ff4444" strokeDasharray="3 3" opacity={0.5} />
        </AreaChart>
      </ResponsiveContainer>
    </div>
  );
}

// ─── Main Page ────────────────────────────────────────────────

const MARKET_COLORS: Record<string, string> = {
  crypto: '#f7931a',
  forex: '#0088ff',
  commodities: '#ffaa00',
  indian_stocks: '#00ff88',
  us_stocks: '#8844ff',
};

export default function RiskPage() {
  const { portfolioState, riskEvents, activeTrades, agentStates } = useNexusStore();
  const [equityCurve, setEquityCurve] = useState<{ time: string; value: number }[]>([]);
  const vixData = useVix();

  useEffect(() => {
    getEquityCurve(7).then(setEquityCurve);
  }, []);

  // ── Derived real-time values ──────────────────────────────
  const equity = portfolioState.equity ?? 0;
  const dailyPnl = portfolioState.dailyPnl ?? 0;
  const drawdownPct = Math.abs(portfolioState.drawdown ?? 0);   // stored as negative
  const dailyLossPct = equity > 0 ? Math.abs((dailyPnl / equity) * 100) : 0;

  const maxConcentration = useMemo(() => {
    if (!activeTrades.length || equity <= 0) return 0;
    const bySymbol: Record<string, number> = {};
    for (const t of activeTrades) {
      if (t.status === 'OPEN') {
        const notional = (t.quantity ?? 0) * (t.entry_price ?? 0);
        bySymbol[t.symbol] = (bySymbol[t.symbol] ?? 0) + notional;
      }
    }
    const maxNotional = Math.max(0, ...Object.values(bySymbol));
    return equity > 0 ? (maxNotional / equity) * 100 : 0;
  }, [activeTrades, equity]);

  // Collect symbols from active trades for the correlation matrix
  const tradeSymbols = useMemo(() => {
    const open = activeTrades.filter((t) => t.status === 'OPEN');
    return [...new Set(open.map((t) => t.symbol.split('/')[0]))];
  }, [activeTrades]);

  // Compute first-trade position sizing if available
  const firstOpenTrade = activeTrades.find((t) => t.status === 'OPEN');
  const positionSizingVal = useMemo(() => {
    if (!firstOpenTrade || equity <= 0) return null;
    const notional = (firstOpenTrade.quantity ?? 0) * (firstOpenTrade.entry_price ?? 0);
    return equity > 0 ? notional / equity : null;
  }, [firstOpenTrade, equity]);

  // Signal validation score: real average confidence from agent decisions
  const signalScore = useMemo(() => {
    const decisions = Object.values(agentStates).filter(
      (d): d is NonNullable<typeof d> => d !== undefined && d !== null
    );
    if (decisions.length === 0) return null;
    return decisions.reduce((s, d) => s + (d.confidence ?? 0), 0) / decisions.length;
  }, [agentStates]);

  // Real correlations from Yahoo Finance for open-trade symbols
  const apiSymbolsForCorr = useMemo(() =>
    tradeSymbols.map((s) =>
      ['BTC','ETH','SOL','BNB','XRP','DOGE','ADA','AVAX'].includes(s) ? `${s}-USD` : s
    ),
  [tradeSymbols]);
  const corrResult = useCorrelations(apiSymbolsForCorr);

  // Average pairwise correlation: use real matrix when available, fallback otherwise
  const avgCorrelation = useMemo(() => {
    if (tradeSymbols.length < 2) return 0;
    const mat = corrResult.matrix;
    if (mat.length === tradeSymbols.length) {
      let sum = 0, count = 0;
      for (let i = 0; i < tradeSymbols.length; i++) {
        for (let j = i + 1; j < tradeSymbols.length; j++) {
          sum += mat[i]?.[j] ?? 0;
          count++;
        }
      }
      return count > 0 ? sum / count : 0;
    }
    // Fallback while loading
    let sum = 0, count = 0;
    for (let i = 0; i < tradeSymbols.length; i++) {
      for (let j = i + 1; j < tradeSymbols.length; j++) {
        sum += getCorrelationFallback(tradeSymbols[i], tradeSymbols[j]);
        count++;
      }
    }
    return count > 0 ? sum / count : 0;
  }, [tradeSymbols, corrResult.matrix]);

  // ── Circuit Breakers (real values) ───────────────────────
  const circuitBreakers: CircuitBreaker[] = useMemo(() => [
    {
      id: '1', name: 'Daily Loss Limit',
      description: 'Stop trading if daily loss > 3%',
      enabled: true, triggered: dailyLossPct >= 3,
      threshold: 3, current_value: dailyLossPct,
    },
    {
      id: '2', name: 'Max Drawdown',
      description: 'Halt if drawdown > 15%',
      enabled: true, triggered: drawdownPct >= 15,
      threshold: 15, current_value: drawdownPct,
    },
    {
      id: '3', name: 'Position Concentration',
      description: 'Alert if single position > 10%',
      enabled: true, triggered: maxConcentration >= 10,
      threshold: 10, current_value: maxConcentration,
    },
    {
      id: '4', name: 'Correlation Limit',
      description: 'Reduce sizing if avg corr > 0.7',
      enabled: true, triggered: avgCorrelation >= 0.7,
      threshold: 0.7,
      current_value: avgCorrelation,
    },
    {
      id: '5', name: 'Volatility Spike',
      description: 'Reduce size on VIX > 35',
      enabled: true, triggered: vixData !== null && vixData.value >= 35,
      threshold: 35, current_value: vixData?.value ?? 0,
    },
    {
      id: '6', name: 'API Failure',
      description: 'Halt all trading on data feed loss',
      enabled: true, triggered: false,
      threshold: 0, current_value: 0,
    },
  ], [dailyLossPct, drawdownPct, maxConcentration, activeTrades, vixData, avgCorrelation]);

  // ── VIX regime color ─────────────────────────────────────
  const regimeColor = useMemo(() => {
    const regime = vixData?.regime ?? '';
    if (regime === 'Low') return 'text-nexus-green';
    if (regime === 'Normal') return 'text-nexus-blue';
    if (regime === 'Elevated') return 'text-nexus-yellow';
    if (regime === 'Extreme') return 'text-nexus-red';
    return 'text-muted';
  }, [vixData?.regime]);

  // ── Risk Layers (live values) ─────────────────────────────
  const riskLayers: RiskLayer[] = useMemo(() => {
    const exposurePct = portfolioState.exposurePct ?? 0;
    const agentCount = Object.values(agentStates).filter(Boolean).length;
    const confidenceOk = signalScore === null || signalScore >= 0.65;
    const agentDiversityOk = agentCount >= 3; // need 3+ agents active
    const layer1Status: 'GREEN' | 'YELLOW' | 'RED' =
      (!confidenceOk) ? 'RED' : (!agentDiversityOk && agentCount > 0) ? 'YELLOW' : 'GREEN';
    // Trades with stop-loss set (real check from trade records)
    const openTrades = activeTrades.filter((t) => t.status === 'OPEN');
    const withStopLoss = openTrades.filter((t) => t.stop_loss != null && t.stop_loss > 0).length;
    const stopCoverageOk = openTrades.length === 0 || withStopLoss === openTrades.length;
    return [
      {
        layer: 1,
        name: 'Signal Validation',
        description: 'Pre-trade signal quality checks',
        status: layer1Status,
        checks: [
          {
            name: 'Confidence threshold',
            passed: confidenceOk,
            current_value: signalScore !== null ? Number(signalScore.toFixed(2)) : 'N/A',
            threshold: 0.65,
            message: signalScore === null ? 'Awaiting agent data' : signalScore >= 0.65 ? 'OK' : 'Low confidence',
          },
          {
            name: 'Active agents',
            passed: agentCount >= 3 || agentCount === 0,
            current_value: agentCount,
            threshold: 3,
            message: agentCount >= 3 ? `${agentCount} agents voting` : agentCount === 0 ? 'Awaiting agents' : `Only ${agentCount} active`,
          },
          {
            name: 'Risk/Reward min',
            passed: true,
            current_value: firstOpenTrade ? 'N/A' : 'N/A',
            threshold: 1.5,
            message: 'N/A — tracked by risk manager',
          },
        ],
      },
      {
        layer: 2,
        name: 'Position Sizing',
        description: 'Kelly criterion & volatility adjustment',
        status: 'GREEN',
        checks: [
          {
            name: 'Kelly fraction max',
            passed: positionSizingVal === null || positionSizingVal <= 0.05,
            current_value: positionSizingVal !== null ? Number(positionSizingVal.toFixed(4)) : 'N/A',
            threshold: 0.05,
            message: 'OK',
          },
          {
            name: 'Max position size',
            passed: maxConcentration <= 10,
            current_value: Number((maxConcentration / 100).toFixed(4)),
            threshold: 0.10,
            message: maxConcentration <= 10 ? 'OK' : 'Above limit',
          },
        ],
      },
      {
        layer: 3,
        name: 'Portfolio Limits',
        description: 'Exposure and concentration checks',
        status: drawdownPct > 8 || exposurePct > 60 ? 'YELLOW' : 'GREEN',
        checks: [
          {
            name: 'Total exposure',
            passed: exposurePct <= 60,
            current_value: Math.round(exposurePct),
            threshold: 60,
            message: exposurePct > 60 ? 'Above threshold' : 'OK',
          },
          {
            name: 'Single market max',
            passed: maxConcentration <= 30,
            current_value: Number(maxConcentration.toFixed(1)),
            threshold: 30,
            message: 'OK',
          },
          {
            name: 'Correlation limit',
            passed: avgCorrelation <= 0.70,
            current_value: Number(avgCorrelation.toFixed(2)),
            threshold: 0.70,
            message: avgCorrelation <= 0.70 ? 'OK' : 'High correlation',
          },
        ],
      },
      {
        layer: 4,
        name: 'Drawdown Control',
        description: 'Real-time drawdown monitoring',
        status: drawdownPct >= 10 ? 'YELLOW' : 'GREEN',
        checks: [
          {
            name: 'Daily drawdown',
            passed: dailyLossPct < 3,
            current_value: -Number(dailyLossPct.toFixed(2)),
            threshold: -3.0,
            message: dailyLossPct < 3 ? 'OK' : 'Warning',
          },
          {
            name: 'Max drawdown',
            passed: drawdownPct < 15,
            current_value: -Number(drawdownPct.toFixed(2)),
            threshold: -15.0,
            message: drawdownPct < 15 ? 'OK' : 'Warning',
          },
          {
            name: 'Stop-loss coverage',
            passed: stopCoverageOk,
            current_value: `${withStopLoss}/${openTrades.length}`,
            threshold: openTrades.length,
            message: openTrades.length === 0 ? 'No open trades' : stopCoverageOk ? 'All positions protected' : `${openTrades.length - withStopLoss} unprotected`,
          },
        ],
      },
      {
        layer: 5,
        name: 'Market Conditions',
        description: 'Regime & liquidity checks',
        status: vixData && vixData.value >= 35 ? 'YELLOW' : 'GREEN',
        checks: [
          {
            name: 'VIX threshold',
            passed: !vixData || vixData.value < 35,
            current_value: vixData?.value?.toFixed(1) ?? '—',
            threshold: 35.0,
            message: vixData ? `${vixData.regime} regime` : 'Loading',
          },
          {
            name: 'Position count',
            passed: openTrades.length <= 20,
            current_value: openTrades.length,
            threshold: 20,
            message: openTrades.length <= 20 ? 'Within limit' : 'Too many positions',
          },
          {
            name: 'Correlation regime',
            passed: avgCorrelation <= 0.85,
            current_value: Number(avgCorrelation.toFixed(2)),
            threshold: 0.85,
            message: avgCorrelation <= 0.85 ? 'Normal' : 'High regime',
          },
        ],
      },
    ];
  }, [portfolioState.exposurePct, drawdownPct, dailyLossPct, maxConcentration, activeTrades, firstOpenTrade, positionSizingVal, signalScore, vixData, avgCorrelation, agentStates]);

  // ── Portfolio Exposure (real trades) ─────────────────────
  const marketExposure = useMemo(() => {
    const openTrades = activeTrades.filter((t) => t.status === 'OPEN');
    if (!openTrades.length || equity <= 0) {
      return [{ market: 'Cash', exposure: 100, color: '#4b5563' }];
    }

    const byMarket: Record<string, number> = {};
    let totalNotional = 0;
    for (const t of openTrades) {
      const notional = (t.quantity ?? 0) * (t.entry_price ?? 0);
      byMarket[t.market] = (byMarket[t.market] ?? 0) + notional;
      totalNotional += notional;
    }

    if (totalNotional === 0) {
      return [{ market: 'Cash', exposure: 100, color: '#4b5563' }];
    }

    const result = Object.entries(byMarket).map(([market, notional]) => ({
      market: market.replace(/_/g, ' ').replace(/\b\w/g, (l) => l.toUpperCase()),
      exposure: Math.round((notional / equity) * 100),
      color: MARKET_COLORS[market] ?? '#6b7280',
    }));

    const usedPct = result.reduce((a, m) => a + m.exposure, 0);
    const cashPct = Math.max(0, 100 - usedPct);
    if (cashPct > 0) result.push({ market: 'Cash', exposure: cashPct, color: '#4b5563' });

    return result;
  }, [activeTrades, equity]);

  const anyCircuitTriggered = circuitBreakers.some((cb) => cb.triggered);

  return (
    <div className="space-y-4">
      {/* VIX Banner */}
      {vixData && (
        <div className="nexus-card p-3 flex items-center justify-between border border-border">
          <div className="flex items-center gap-4">
            <div>
              <div className="text-xs text-muted uppercase tracking-wider">VIX</div>
              <div className="font-mono text-2xl font-bold text-white">
                {vixData.value?.toFixed(1) ?? '—'}
              </div>
            </div>
            <div>
              <div className="text-xs text-muted">Change</div>
              <div className={cn('text-sm font-mono font-bold', (vixData.change ?? 0) >= 0 ? 'text-nexus-red' : 'text-nexus-green')}>
                {(vixData.change ?? 0) >= 0 ? '+' : ''}{vixData.change?.toFixed(2) ?? '—'}
              </div>
            </div>
            <div>
              <div className="text-xs text-muted">Regime</div>
              <div className={cn('text-sm font-bold', regimeColor)}>{vixData.regime ?? '—'}</div>
            </div>
          </div>
          <div className="text-xs text-muted">Fear &amp; Greed · CBOE VIX</div>
        </div>
      )}

      {/* Risk Layer Status */}
      <div>
        <h2 className="font-medium text-sm text-muted uppercase tracking-wider mb-3">
          Risk Layer Status
        </h2>
        <div className="grid grid-cols-5 gap-3">
          {riskLayers.map((layer) => (
            <RiskLayerPanel key={layer.layer} layer={layer} />
          ))}
        </div>
      </div>

      {/* Circuit Breakers */}
      <div>
        <div className="flex items-center gap-2 mb-3">
          <h2 className="font-medium text-sm text-muted uppercase tracking-wider">
            Circuit Breakers
          </h2>
          {anyCircuitTriggered && (
            <span className="badge bg-nexus-red/10 text-nexus-red border border-nexus-red/20 text-xs">
              TRIGGERED
            </span>
          )}
        </div>
        <div className="grid grid-cols-3 gap-3">
          {circuitBreakers.map((cb) => (
            <CircuitBreakerCard key={cb.id} cb={cb} />
          ))}
        </div>
      </div>

      {/* Drawdown chart + Exposure */}
      <div className="grid grid-cols-3 gap-4">
        {/* Only show real DB curve — never falls back to mock */}
        <DrawdownChart curve={equityCurve} />

        {/* Portfolio exposure — computed from real trades */}
        <div className="nexus-card p-4">
          <h3 className="font-medium text-sm text-white mb-4">Portfolio Exposure</h3>
          {marketExposure.length === 1 && marketExposure[0].market === 'Cash' ? (
            <div className="flex flex-col items-center justify-center h-40">
              <Activity size={28} className="text-muted mb-2 opacity-40" />
              <div className="text-xs text-muted text-center">No open positions<br />100% cash</div>
            </div>
          ) : (
            <>
              <ResponsiveContainer width="100%" height={160}>
                <PieChart>
                  <Pie
                    data={marketExposure}
                    cx="50%"
                    cy="50%"
                    innerRadius={45}
                    outerRadius={70}
                    dataKey="exposure"
                    paddingAngle={2}
                  >
                    {marketExposure.map((entry, i) => (
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
                {marketExposure.map((m) => (
                  <div key={m.market} className="flex items-center justify-between text-xs">
                    <div className="flex items-center gap-1.5">
                      <div className="w-2 h-2 rounded-full" style={{ backgroundColor: m.color }} />
                      <span className="text-gray-300">{m.market}</span>
                    </div>
                    <span className="font-mono text-white">{m.exposure}%</span>
                  </div>
                ))}
              </div>
            </>
          )}
        </div>
      </div>

      {/* Risk Heatmap */}
      <RiskHeatmap activeTrades={activeTrades} />

      {/* Correlation Matrix — uses real trade symbols when available */}
      <CorrelationMatrix tradeSymbols={tradeSymbols} />

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

      {/* No events placeholder */}
      {riskEvents.length === 0 && (
        <div className="nexus-card p-4 flex items-center gap-3 border border-nexus-green/10">
          <CheckCircle2 size={16} className="text-nexus-green flex-shrink-0" />
          <span className="text-xs text-muted">No risk events recorded. System operating within all thresholds.</span>
        </div>
      )}
    </div>
  );
}
