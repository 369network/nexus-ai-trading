'use client';

import { useState, useCallback, useRef } from 'react';
import {
  AreaChart,
  Area,
  XAxis,
  YAxis,
  ResponsiveContainer,
  Tooltip,
  CartesianGrid,
  ReferenceLine,
  ComposedChart,
  Bar,
} from 'recharts';
import {
  FlaskConical,
  Play,
  Loader2,
  AlertCircle,
  Download,
  ChevronLeft,
  ChevronRight,
  TrendingUp,
  TrendingDown,
  Target,
  BarChart2,
  Clock,
  Activity,
  DollarSign,
  Percent,
} from 'lucide-react';
import { cn, formatCurrency, formatPercent, formatNumber, getPnlColor } from '@/lib/utils';

// ─── Types ────────────────────────────────────────────────────────────────────

type Symbol = 'BTCUSDT' | 'ETHUSDT' | 'SOLUSDT' | 'BNBUSDT' | 'XRPUSDT' | 'ADAUSDT';
type Interval = '1h' | '4h' | '1d';
type Strategy = 'rsi' | 'macd' | 'bbands' | 'ema';
type ExitReason = 'TP' | 'SL' | 'Signal';

interface Candle {
  time: number;
  open: number;
  high: number;
  low: number;
  close: number;
  volume: number;
}

interface Trade {
  id: number;
  direction: 'LONG';
  entryTime: number;
  exitTime: number;
  entryPrice: number;
  exitPrice: number;
  pnl: number;
  pnlPct: number;
  duration: number;
  exitReason: ExitReason;
  positionSize: number;
}

interface EquityPoint {
  time: number;
  label: string;
  equity: number;
  drawdown: number;
}

interface BacktestResult {
  totalReturn: number;
  totalPnl: number;
  winRate: number;
  profitFactor: number;
  maxDrawdown: number;
  sharpe: number;
  totalTrades: number;
  avgDuration: number;
  equityCurve: EquityPoint[];
  trades: Trade[];
  initialCapital: number;
}

interface Config {
  symbol: Symbol;
  interval: Interval;
  strategy: Strategy;
  initialCapital: number;
  positionSizePct: number;
  stopLossPct: number;
  takeProfitPct: number;
  // RSI
  rsiPeriod: number;
  rsiOversold: number;
  rsiOverbought: number;
  // MACD
  macdFast: number;
  macdSlow: number;
  macdSignal: number;
  // Bollinger Bands
  bbPeriod: number;
  bbStdDev: number;
  // EMA Crossover
  emaFast: number;
  emaSlow: number;
}

// ─── Technical Indicators ─────────────────────────────────────────────────────

function computeEMA(closes: number[], period: number): number[] {
  const result: number[] = new Array(closes.length).fill(NaN);
  if (closes.length < period) return result;
  const k = 2 / (period + 1);
  let ema = closes.slice(0, period).reduce((a, b) => a + b, 0) / period;
  result[period - 1] = ema;
  for (let i = period; i < closes.length; i++) {
    ema = closes[i] * k + ema * (1 - k);
    result[i] = ema;
  }
  return result;
}

function computeRSI(closes: number[], period: number): number[] {
  const result: number[] = new Array(closes.length).fill(NaN);
  if (closes.length < period + 1) return result;

  let avgGain = 0;
  let avgLoss = 0;

  for (let i = 1; i <= period; i++) {
    const diff = closes[i] - closes[i - 1];
    if (diff > 0) avgGain += diff;
    else avgLoss += Math.abs(diff);
  }
  avgGain /= period;
  avgLoss /= period;

  const rs = avgLoss === 0 ? 100 : avgGain / avgLoss;
  result[period] = 100 - 100 / (1 + rs);

  for (let i = period + 1; i < closes.length; i++) {
    const diff = closes[i] - closes[i - 1];
    const gain = diff > 0 ? diff : 0;
    const loss = diff < 0 ? Math.abs(diff) : 0;
    avgGain = (avgGain * (period - 1) + gain) / period;
    avgLoss = (avgLoss * (period - 1) + loss) / period;
    const rsi_rs = avgLoss === 0 ? 100 : avgGain / avgLoss;
    result[i] = 100 - 100 / (1 + rsi_rs);
  }
  return result;
}

function computeMACD(
  closes: number[],
  fast: number,
  slow: number,
  signal: number
): { macd: number[]; signal: number[]; histogram: number[] } {
  const fastEMA = computeEMA(closes, fast);
  const slowEMA = computeEMA(closes, slow);
  const macdLine: number[] = closes.map((_, i) => {
    if (isNaN(fastEMA[i]) || isNaN(slowEMA[i])) return NaN;
    return fastEMA[i] - slowEMA[i];
  });

  const validMacd = macdLine.map((v) => (isNaN(v) ? 0 : v));
  const signalEMAArr = computeEMA(validMacd, signal);

  const histogram: number[] = macdLine.map((m, i) => {
    if (isNaN(m) || isNaN(signalEMAArr[i])) return NaN;
    return m - signalEMAArr[i];
  });

  return { macd: macdLine, signal: signalEMAArr, histogram };
}

function computeBollingerBands(
  closes: number[],
  period: number,
  stdDev: number
): { upper: number[]; middle: number[]; lower: number[] } {
  const upper: number[] = new Array(closes.length).fill(NaN);
  const middle: number[] = new Array(closes.length).fill(NaN);
  const lower: number[] = new Array(closes.length).fill(NaN);

  for (let i = period - 1; i < closes.length; i++) {
    const slice = closes.slice(i - period + 1, i + 1);
    const mean = slice.reduce((a, b) => a + b, 0) / period;
    const variance = slice.reduce((a, b) => a + (b - mean) ** 2, 0) / period;
    const sd = Math.sqrt(variance);
    middle[i] = mean;
    upper[i] = mean + stdDev * sd;
    lower[i] = mean - stdDev * sd;
  }
  return { upper, middle, lower };
}

function computeDrawdown(equity: number[]): number[] {
  const result: number[] = new Array(equity.length).fill(0);
  let peak = equity[0];
  for (let i = 0; i < equity.length; i++) {
    if (equity[i] > peak) peak = equity[i];
    result[i] = peak > 0 ? ((equity[i] - peak) / peak) * 100 : 0;
  }
  return result;
}

function computeSharpe(returns: number[], riskFreeRate: number = 0): number {
  if (returns.length < 2) return 0;
  const mean = returns.reduce((a, b) => a + b, 0) / returns.length;
  const variance = returns.reduce((a, b) => a + (b - mean) ** 2, 0) / returns.length;
  const std = Math.sqrt(variance);
  if (std === 0) return 0;
  const periodsPerYear = 252;
  return ((mean - riskFreeRate / periodsPerYear) / std) * Math.sqrt(periodsPerYear);
}

// ─── Backtesting Engine ───────────────────────────────────────────────────────

function formatCandleTime(ts: number, interval: Interval): string {
  const d = new Date(ts);
  const mon = d.toLocaleString('en-US', { month: 'short' });
  const day = String(d.getDate()).padStart(2, '0');
  const hr = String(d.getHours()).padStart(2, '0');
  const min = String(d.getMinutes()).padStart(2, '0');
  if (interval === '1d') return `${mon} ${day}`;
  return `${mon} ${day} ${hr}:${min}`;
}

function runBacktest(candles: Candle[], config: Config): BacktestResult {
  const closes = candles.map((c) => c.close);
  const highs = candles.map((c) => c.high);
  const lows = candles.map((c) => c.low);
  const n = candles.length;

  // Compute indicators
  const rsi = computeRSI(closes, config.rsiPeriod);
  const { macd: macdLine, signal: macdSignal } = computeMACD(
    closes,
    config.macdFast,
    config.macdSlow,
    config.macdSignal
  );
  const bb = computeBollingerBands(closes, config.bbPeriod, config.bbStdDev);
  const emaFastArr = computeEMA(closes, config.emaFast);
  const emaSlowArr = computeEMA(closes, config.emaSlow);

  // Generate raw signals: 1 = buy, -1 = sell, 0 = hold
  const signals: number[] = new Array(n).fill(0);

  for (let i = 1; i < n; i++) {
    switch (config.strategy) {
      case 'rsi': {
        const prev = rsi[i - 1];
        const curr = rsi[i];
        if (!isNaN(prev) && !isNaN(curr)) {
          if (prev < config.rsiOversold && curr >= config.rsiOversold) signals[i] = 1;
          else if (prev < config.rsiOverbought && curr >= config.rsiOverbought) signals[i] = -1;
        }
        break;
      }
      case 'macd': {
        const prevM = macdLine[i - 1];
        const currM = macdLine[i];
        const prevS = macdSignal[i - 1];
        const currS = macdSignal[i];
        if (!isNaN(prevM) && !isNaN(currM) && !isNaN(prevS) && !isNaN(currS)) {
          if (prevM < prevS && currM >= currS) signals[i] = 1;
          else if (prevM > prevS && currM <= currS) signals[i] = -1;
        }
        break;
      }
      case 'bbands': {
        const prevClose = closes[i - 1];
        const currClose = closes[i];
        const prevUpper = bb.upper[i - 1];
        const currUpper = bb.upper[i];
        const prevLower = bb.lower[i - 1];
        const currLower = bb.lower[i];
        if (
          !isNaN(prevUpper) &&
          !isNaN(currUpper) &&
          !isNaN(prevLower) &&
          !isNaN(currLower)
        ) {
          if (prevClose <= prevUpper && currClose > currUpper) signals[i] = 1;
          else if (prevClose >= prevLower && currClose < currLower) signals[i] = -1;
        }
        break;
      }
      case 'ema': {
        const prevFast = emaFastArr[i - 1];
        const currFast = emaFastArr[i];
        const prevSlow = emaSlowArr[i - 1];
        const currSlow = emaSlowArr[i];
        if (!isNaN(prevFast) && !isNaN(currFast) && !isNaN(prevSlow) && !isNaN(currSlow)) {
          if (prevFast < prevSlow && currFast >= currSlow) signals[i] = 1;
          else if (prevFast > prevSlow && currFast <= currSlow) signals[i] = -1;
        }
        break;
      }
    }
  }

  // Simulate trades
  const COMMISSION = 0.001; // 0.1% each way
  let capital = config.initialCapital;
  const trades: Trade[] = [];
  let tradeId = 0;

  // Position state
  let inPosition = false;
  let entryPrice = 0;
  let entryTime = 0;
  let entryCapital = 0;
  let entryIndex = 0;
  let stopPrice = 0;
  let tpPrice = 0;

  const equityArr: number[] = [];
  const timeArr: number[] = [];

  for (let i = 0; i < n; i++) {
    const candle = candles[i];
    const price = candle.close;
    const high = highs[i];
    const low = lows[i];

    if (!inPosition) {
      equityArr.push(capital);
      timeArr.push(candle.time);

      if (signals[i] === 1) {
        // Enter long
        const positionValue = capital * (config.positionSizePct / 100);
        const afterCommission = positionValue * (1 - COMMISSION);
        entryPrice = price;
        entryTime = candle.time;
        entryCapital = afterCommission;
        entryIndex = i;
        stopPrice = price * (1 - config.stopLossPct / 100);
        tpPrice = price * (1 + config.takeProfitPct / 100);
        inPosition = true;
      }
    } else {
      // Check SL/TP on this candle (using high/low)
      let exitPrice = 0;
      let exitReason: ExitReason | null = null;

      if (low <= stopPrice) {
        exitPrice = stopPrice;
        exitReason = 'SL';
      } else if (high >= tpPrice) {
        exitPrice = tpPrice;
        exitReason = 'TP';
      } else if (signals[i] === -1) {
        exitPrice = price;
        exitReason = 'Signal';
      }

      if (exitReason !== null) {
        const exitValue = entryCapital * (exitPrice / entryPrice) * (1 - COMMISSION);
        const pnl = exitValue - entryCapital;
        const pnlPct = (exitPrice / entryPrice - 1) * 100 - COMMISSION * 2 * 100;
        capital = capital - entryCapital / (1 - COMMISSION) + exitValue;

        trades.push({
          id: ++tradeId,
          direction: 'LONG',
          entryTime,
          exitTime: candle.time,
          entryPrice,
          exitPrice,
          pnl,
          pnlPct,
          duration: i - entryIndex,
          exitReason,
          positionSize: entryCapital,
        });

        inPosition = false;
        equityArr.push(capital);
        timeArr.push(candle.time);
      } else {
        // Mark to market
        const mtm = entryCapital * (price / entryPrice);
        const unrealizedCapital = capital - entryCapital / (1 - COMMISSION) + mtm;
        equityArr.push(unrealizedCapital);
        timeArr.push(candle.time);
      }
    }
  }

  // Close any open position at last bar
  if (inPosition) {
    const lastCandle = candles[n - 1];
    const exitPrice = lastCandle.close;
    const exitValue = entryCapital * (exitPrice / entryPrice) * (1 - COMMISSION);
    const pnl = exitValue - entryCapital;
    const pnlPct = (exitPrice / entryPrice - 1) * 100 - COMMISSION * 2 * 100;
    capital = capital - entryCapital / (1 - COMMISSION) + exitValue;
    trades.push({
      id: ++tradeId,
      direction: 'LONG',
      entryTime,
      exitTime: lastCandle.time,
      entryPrice,
      exitPrice,
      pnl,
      pnlPct,
      duration: n - 1 - entryIndex,
      exitReason: 'Signal',
      positionSize: entryCapital,
    });
  }

  // Compute metrics
  const drawdownArr = computeDrawdown(equityArr);
  const maxDrawdown = Math.min(...drawdownArr);

  const winningTrades = trades.filter((t) => t.pnl > 0);
  const losingTrades = trades.filter((t) => t.pnl <= 0);
  const grossProfit = winningTrades.reduce((a, t) => a + t.pnl, 0);
  const grossLoss = Math.abs(losingTrades.reduce((a, t) => a + t.pnl, 0));
  const profitFactor = grossLoss === 0 ? (grossProfit > 0 ? Infinity : 0) : grossProfit / grossLoss;
  const winRate = trades.length > 0 ? (winningTrades.length / trades.length) * 100 : 0;
  const totalPnl = capital - config.initialCapital;
  const totalReturn = (totalPnl / config.initialCapital) * 100;
  const avgDuration =
    trades.length > 0 ? trades.reduce((a, t) => a + t.duration, 0) / trades.length : 0;

  // Daily returns for Sharpe (one return per equity point)
  const returns: number[] = [];
  for (let i = 1; i < equityArr.length; i++) {
    if (equityArr[i - 1] > 0) {
      returns.push((equityArr[i] - equityArr[i - 1]) / equityArr[i - 1]);
    }
  }
  const sharpe = computeSharpe(returns);

  const equityCurve: EquityPoint[] = equityArr.map((eq, i) => ({
    time: timeArr[i],
    label: formatCandleTime(timeArr[i], config.interval),
    equity: Math.round(eq * 100) / 100,
    drawdown: Math.round(drawdownArr[i] * 100) / 100,
  }));

  return {
    totalReturn,
    totalPnl,
    winRate,
    profitFactor,
    maxDrawdown,
    sharpe,
    totalTrades: trades.length,
    avgDuration,
    equityCurve,
    trades,
    initialCapital: config.initialCapital,
  };
}

// ─── Default Config ───────────────────────────────────────────────────────────

const DEFAULT_CONFIG: Config = {
  symbol: 'BTCUSDT',
  interval: '1d',
  strategy: 'rsi',
  initialCapital: 10000,
  positionSizePct: 10,
  stopLossPct: 2,
  takeProfitPct: 4,
  rsiPeriod: 14,
  rsiOversold: 30,
  rsiOverbought: 70,
  macdFast: 12,
  macdSlow: 26,
  macdSignal: 9,
  bbPeriod: 20,
  bbStdDev: 2.0,
  emaFast: 9,
  emaSlow: 21,
};

// ─── Sub-components ───────────────────────────────────────────────────────────

interface MetricCardProps {
  label: string;
  value: string;
  sub?: string;
  color?: string;
  icon: React.ReactNode;
}

function MetricCard({ label, value, sub, color, icon }: MetricCardProps) {
  return (
    <div className="nexus-card nexus-card-hover p-4 flex flex-col gap-2">
      <div className="flex items-center justify-between">
        <span className="metric-label">{label}</span>
        <span className="text-muted">{icon}</span>
      </div>
      <div className={cn('metric-value text-2xl', color ?? 'text-white')}>{value}</div>
      {sub && <div className="text-xs text-muted">{sub}</div>}
    </div>
  );
}

interface NumberInputProps {
  label: string;
  value: number;
  onChange: (v: number) => void;
  min?: number;
  max?: number;
  step?: number;
  suffix?: string;
}

function NumberInput({ label, value, onChange, min, max, step = 1, suffix }: NumberInputProps) {
  return (
    <div className="flex flex-col gap-1">
      <label className="text-xs text-muted uppercase tracking-wider">{label}</label>
      <div className="relative">
        <input
          type="number"
          value={value}
          min={min}
          max={max}
          step={step}
          onChange={(e) => onChange(parseFloat(e.target.value) || 0)}
          className="w-full bg-background border border-border rounded-lg px-3 py-2 text-sm font-mono text-white focus:outline-none focus:border-nexus-blue/60 transition-colors"
        />
        {suffix && (
          <span className="absolute right-3 top-1/2 -translate-y-1/2 text-xs text-muted pointer-events-none">
            {suffix}
          </span>
        )}
      </div>
    </div>
  );
}

interface CustomTooltipProps {
  active?: boolean;
  payload?: Array<{ value: number; name: string }>;
  label?: string;
  initialCapital?: number;
}

function EquityTooltip({ active, payload, label, initialCapital }: CustomTooltipProps) {
  if (!active || !payload || !payload.length) return null;
  const equity = payload[0]?.value ?? 0;
  const dd = payload[1]?.value;
  const pnl = equity - (initialCapital ?? 10000);
  return (
    <div className="nexus-tooltip">
      <div className="text-xs text-muted mb-1">{label}</div>
      <div className="text-sm font-mono font-semibold text-white">{formatCurrency(equity)}</div>
      <div className={cn('text-xs font-mono', getPnlColor(pnl))}>
        {pnl >= 0 ? '+' : ''}{formatCurrency(pnl)}
      </div>
      {dd !== undefined && (
        <div className="text-xs font-mono text-nexus-red mt-0.5">DD: {dd.toFixed(2)}%</div>
      )}
    </div>
  );
}

function DrawdownTooltip({ active, payload, label }: CustomTooltipProps) {
  if (!active || !payload || !payload.length) return null;
  const dd = payload[0]?.value ?? 0;
  return (
    <div className="nexus-tooltip">
      <div className="text-xs text-muted mb-1">{label}</div>
      <div className="text-sm font-mono text-nexus-red">{dd.toFixed(2)}%</div>
    </div>
  );
}

// ─── Main Page ─────────────────────────────────────────────────────────────────

export default function BacktestPage() {
  const [config, setConfig] = useState<Config>(DEFAULT_CONFIG);
  const [status, setStatus] = useState<'idle' | 'loading' | 'computing' | 'done' | 'error'>('idle');
  const [error, setError] = useState<string>('');
  const [result, setResult] = useState<BacktestResult | null>(null);
  const [tradePage, setTradePage] = useState(0);
  const TRADES_PER_PAGE = 20;
  const abortRef = useRef<AbortController | null>(null);

  const updateConfig = useCallback(<K extends keyof Config>(key: K, value: Config[K]) => {
    setConfig((prev) => ({ ...prev, [key]: value }));
  }, []);

  const handleRun = useCallback(async () => {
    if (abortRef.current) abortRef.current.abort();
    const controller = new AbortController();
    abortRef.current = controller;

    setStatus('loading');
    setError('');
    setResult(null);
    setTradePage(0);

    try {
      const url = `https://api.binance.com/api/v3/klines?symbol=${config.symbol}&interval=${config.interval}&limit=1000`;
      const resp = await fetch(url, { signal: controller.signal });
      if (!resp.ok) throw new Error(`Binance API error: ${resp.status} ${resp.statusText}`);
      const raw: unknown[][] = await resp.json();

      const candles: Candle[] = raw.map((k) => ({
        time: k[0] as number,
        open: parseFloat(k[1] as string),
        high: parseFloat(k[2] as string),
        low: parseFloat(k[3] as string),
        close: parseFloat(k[4] as string),
        volume: parseFloat(k[5] as string),
      }));

      setStatus('computing');

      // Defer heavy computation so UI can update first
      setTimeout(() => {
        try {
          const res = runBacktest(candles, config);
          setResult(res);
          setStatus('done');
        } catch (e) {
          setError(e instanceof Error ? e.message : 'Computation error');
          setStatus('error');
        }
      }, 0);
    } catch (e) {
      if ((e as Error).name === 'AbortError') return;
      setError(e instanceof Error ? e.message : 'Unknown error');
      setStatus('error');
    }
  }, [config]);

  const handleExportCSV = useCallback(() => {
    if (!result) return;
    const headers = [
      'ID', 'Direction', 'Entry Time', 'Exit Time', 'Entry Price', 'Exit Price',
      'P&L $', 'P&L %', 'Duration (candles)', 'Exit Reason',
    ];
    const rows = result.trades.map((t) => [
      t.id,
      t.direction,
      new Date(t.entryTime).toISOString(),
      new Date(t.exitTime).toISOString(),
      t.entryPrice.toFixed(4),
      t.exitPrice.toFixed(4),
      t.pnl.toFixed(2),
      t.pnlPct.toFixed(2),
      t.duration,
      t.exitReason,
    ]);
    const csv = [headers.join(','), ...rows.map((r) => r.join(','))].join('\n');
    const blob = new Blob([csv], { type: 'text/csv;charset=utf-8;' });
    const url = URL.createObjectURL(blob);
    const link = document.createElement('a');
    link.href = url;
    link.download = `backtest_${config.symbol}_${config.strategy}_${Date.now()}.csv`;
    link.click();
    URL.revokeObjectURL(url);
  }, [result, config]);

  const paginatedTrades = result
    ? result.trades.slice(
        tradePage * TRADES_PER_PAGE,
        (tradePage + 1) * TRADES_PER_PAGE
      )
    : [];
  const totalPages = result ? Math.ceil(result.trades.length / TRADES_PER_PAGE) : 0;

  const isRunning = status === 'loading' || status === 'computing';

  // Strategy-specific param panels
  const renderStrategyParams = () => {
    switch (config.strategy) {
      case 'rsi':
        return (
          <div className="grid grid-cols-3 gap-3">
            <NumberInput
              label="RSI Period"
              value={config.rsiPeriod}
              onChange={(v) => updateConfig('rsiPeriod', v)}
              min={2}
              max={100}
            />
            <NumberInput
              label="Oversold"
              value={config.rsiOversold}
              onChange={(v) => updateConfig('rsiOversold', v)}
              min={5}
              max={49}
              suffix="%"
            />
            <NumberInput
              label="Overbought"
              value={config.rsiOverbought}
              onChange={(v) => updateConfig('rsiOverbought', v)}
              min={51}
              max={95}
              suffix="%"
            />
          </div>
        );
      case 'macd':
        return (
          <div className="grid grid-cols-3 gap-3">
            <NumberInput
              label="Fast EMA"
              value={config.macdFast}
              onChange={(v) => updateConfig('macdFast', v)}
              min={2}
              max={50}
            />
            <NumberInput
              label="Slow EMA"
              value={config.macdSlow}
              onChange={(v) => updateConfig('macdSlow', v)}
              min={5}
              max={200}
            />
            <NumberInput
              label="Signal EMA"
              value={config.macdSignal}
              onChange={(v) => updateConfig('macdSignal', v)}
              min={2}
              max={50}
            />
          </div>
        );
      case 'bbands':
        return (
          <div className="grid grid-cols-2 gap-3">
            <NumberInput
              label="BB Period"
              value={config.bbPeriod}
              onChange={(v) => updateConfig('bbPeriod', v)}
              min={5}
              max={200}
            />
            <NumberInput
              label="Std Dev Multiplier"
              value={config.bbStdDev}
              onChange={(v) => updateConfig('bbStdDev', v)}
              min={0.5}
              max={5}
              step={0.1}
            />
          </div>
        );
      case 'ema':
        return (
          <div className="grid grid-cols-2 gap-3">
            <NumberInput
              label="Fast EMA"
              value={config.emaFast}
              onChange={(v) => updateConfig('emaFast', v)}
              min={2}
              max={100}
            />
            <NumberInput
              label="Slow EMA"
              value={config.emaSlow}
              onChange={(v) => updateConfig('emaSlow', v)}
              min={5}
              max={300}
            />
          </div>
        );
    }
  };

  const STRATEGY_LABELS: Record<Strategy, string> = {
    rsi: 'RSI Mean Reversion',
    macd: 'MACD Trend Following',
    bbands: 'Bollinger Band Breakout',
    ema: 'EMA Crossover',
  };

  // Y-axis domain for equity chart
  const equityMin = result
    ? Math.min(...result.equityCurve.map((p) => p.equity)) * 0.995
    : 0;
  const equityMax = result
    ? Math.max(...result.equityCurve.map((p) => p.equity)) * 1.005
    : 0;

  return (
    <div className="flex flex-col gap-6 max-w-7xl mx-auto">
      {/* Page Header */}
      <div className="flex items-center gap-3">
        <div className="p-2.5 rounded-xl bg-nexus-blue/10 border border-nexus-blue/20">
          <FlaskConical size={20} className="text-nexus-blue" />
        </div>
        <div>
          <h1 className="text-xl font-bold tracking-tight text-white">Backtester</h1>
          <p className="text-xs text-muted">Simulate strategies on historical Binance data</p>
        </div>
      </div>

      {/* Config Panel */}
      <div className="nexus-card p-5 flex flex-col gap-5">
        <h2 className="text-sm font-semibold text-white uppercase tracking-wider">Configuration</h2>

        {/* Row 1: Symbol / Interval / Strategy */}
        <div className="grid grid-cols-1 sm:grid-cols-3 gap-3">
          {/* Symbol */}
          <div className="flex flex-col gap-1">
            <label className="text-xs text-muted uppercase tracking-wider">Symbol</label>
            <select
              value={config.symbol}
              onChange={(e) => updateConfig('symbol', e.target.value as Symbol)}
              className="w-full bg-background border border-border rounded-lg px-3 py-2 text-sm text-white focus:outline-none focus:border-nexus-blue/60 transition-colors"
            >
              {['BTCUSDT', 'ETHUSDT', 'SOLUSDT', 'BNBUSDT', 'XRPUSDT', 'ADAUSDT'].map((s) => (
                <option key={s} value={s}>{s}</option>
              ))}
            </select>
          </div>

          {/* Interval */}
          <div className="flex flex-col gap-1">
            <label className="text-xs text-muted uppercase tracking-wider">Interval</label>
            <select
              value={config.interval}
              onChange={(e) => updateConfig('interval', e.target.value as Interval)}
              className="w-full bg-background border border-border rounded-lg px-3 py-2 text-sm text-white focus:outline-none focus:border-nexus-blue/60 transition-colors"
            >
              <option value="1h">1 Hour</option>
              <option value="4h">4 Hours</option>
              <option value="1d">1 Day</option>
            </select>
          </div>

          {/* Strategy */}
          <div className="flex flex-col gap-1">
            <label className="text-xs text-muted uppercase tracking-wider">Strategy</label>
            <select
              value={config.strategy}
              onChange={(e) => updateConfig('strategy', e.target.value as Strategy)}
              className="w-full bg-background border border-border rounded-lg px-3 py-2 text-sm text-white focus:outline-none focus:border-nexus-blue/60 transition-colors"
            >
              {(Object.entries(STRATEGY_LABELS) as [Strategy, string][]).map(([k, v]) => (
                <option key={k} value={k}>{v}</option>
              ))}
            </select>
          </div>
        </div>

        {/* Row 2: Capital / Position Size / SL / TP */}
        <div className="grid grid-cols-2 sm:grid-cols-4 gap-3">
          <NumberInput
            label="Initial Capital"
            value={config.initialCapital}
            onChange={(v) => updateConfig('initialCapital', v)}
            min={100}
            step={1000}
            suffix="$"
          />
          <NumberInput
            label="Position Size"
            value={config.positionSizePct}
            onChange={(v) => updateConfig('positionSizePct', v)}
            min={1}
            max={100}
            suffix="%"
          />
          <NumberInput
            label="Stop Loss"
            value={config.stopLossPct}
            onChange={(v) => updateConfig('stopLossPct', v)}
            min={0.1}
            max={50}
            step={0.1}
            suffix="%"
          />
          <NumberInput
            label="Take Profit"
            value={config.takeProfitPct}
            onChange={(v) => updateConfig('takeProfitPct', v)}
            min={0.1}
            max={100}
            step={0.1}
            suffix="%"
          />
        </div>

        {/* Row 3: Strategy-specific params */}
        <div>
          <div className="text-xs text-muted uppercase tracking-wider mb-2">
            {STRATEGY_LABELS[config.strategy]} Parameters
          </div>
          {renderStrategyParams()}
        </div>

        {/* Run button */}
        <button
          onClick={handleRun}
          disabled={isRunning}
          className={cn(
            'w-full flex items-center justify-center gap-2 py-3 rounded-xl font-semibold text-sm transition-all duration-200',
            isRunning
              ? 'bg-nexus-green/20 text-nexus-green cursor-not-allowed border border-nexus-green/30'
              : 'bg-nexus-green text-black hover:bg-nexus-green/90 active:scale-[0.99] shadow-nexus-green'
          )}
        >
          {isRunning ? (
            <>
              <Loader2 size={16} className="animate-spin" />
              {status === 'loading' ? 'Fetching 1000 candles from Binance...' : 'Computing...'}
            </>
          ) : (
            <>
              <Play size={16} />
              Run Backtest
            </>
          )}
        </button>
      </div>

      {/* Error State */}
      {status === 'error' && (
        <div className="nexus-card border-nexus-red/30 p-4 flex items-start gap-3">
          <AlertCircle size={18} className="text-nexus-red flex-shrink-0 mt-0.5" />
          <div>
            <div className="text-sm font-semibold text-nexus-red">Backtest Failed</div>
            <div className="text-xs text-muted mt-1">{error}</div>
          </div>
        </div>
      )}

      {/* Results */}
      {status === 'done' && result && (
        <div className="flex flex-col gap-6 animate-fade-in">
          {/* 8 Metric Cards */}
          <div className="grid grid-cols-2 sm:grid-cols-4 gap-3">
            <MetricCard
              label="Total Return"
              value={formatPercent(result.totalReturn)}
              sub={`from ${formatCurrency(result.initialCapital)}`}
              color={result.totalReturn >= 0 ? 'text-nexus-green' : 'text-nexus-red'}
              icon={<TrendingUp size={14} />}
            />
            <MetricCard
              label="Total P&L"
              value={formatCurrency(result.totalPnl)}
              sub={result.totalPnl >= 0 ? 'Profit' : 'Loss'}
              color={getPnlColor(result.totalPnl)}
              icon={<DollarSign size={14} />}
            />
            <MetricCard
              label="Win Rate"
              value={`${result.winRate.toFixed(1)}%`}
              sub={`${result.trades.filter((t) => t.pnl > 0).length} / ${result.totalTrades} trades`}
              color={result.winRate >= 50 ? 'text-nexus-green' : 'text-nexus-yellow'}
              icon={<Target size={14} />}
            />
            <MetricCard
              label="Profit Factor"
              value={
                isFinite(result.profitFactor)
                  ? formatNumber(result.profitFactor, 2)
                  : result.profitFactor > 0
                  ? '∞'
                  : '0.00'
              }
              sub="Gross profit / loss"
              color={result.profitFactor >= 1.5 ? 'text-nexus-green' : result.profitFactor >= 1 ? 'text-nexus-yellow' : 'text-nexus-red'}
              icon={<BarChart2 size={14} />}
            />
            <MetricCard
              label="Max Drawdown"
              value={`${result.maxDrawdown.toFixed(2)}%`}
              sub="Peak to trough"
              color={result.maxDrawdown > -10 ? 'text-nexus-yellow' : 'text-nexus-red'}
              icon={<TrendingDown size={14} />}
            />
            <MetricCard
              label="Sharpe Ratio"
              value={isFinite(result.sharpe) ? formatNumber(result.sharpe, 2) : '—'}
              sub="Annualized (daily returns)"
              color={
                result.sharpe >= 1.5
                  ? 'text-nexus-green'
                  : result.sharpe >= 0.5
                  ? 'text-nexus-yellow'
                  : 'text-nexus-red'
              }
              icon={<Activity size={14} />}
            />
            <MetricCard
              label="Total Trades"
              value={String(result.totalTrades)}
              sub={`${config.symbol} · ${config.interval}`}
              icon={<Percent size={14} />}
            />
            <MetricCard
              label="Avg Duration"
              value={`${result.avgDuration.toFixed(1)}`}
              sub="candles per trade"
              icon={<Clock size={14} />}
            />
          </div>

          {/* Equity Curve Chart */}
          <div className="nexus-card p-5">
            <div className="flex items-center justify-between mb-4">
              <div>
                <div className="text-sm font-semibold text-white">Equity Curve</div>
                <div className="text-xs text-muted mt-0.5">
                  {config.symbol} · {config.interval} · {STRATEGY_LABELS[config.strategy]}
                </div>
              </div>
              <div className="text-xs font-mono text-muted">
                {result.equityCurve.length} data points
              </div>
            </div>
            <div className="h-64">
              <ResponsiveContainer width="100%" height="100%">
                <ComposedChart
                  data={result.equityCurve}
                  margin={{ top: 4, right: 8, left: 0, bottom: 0 }}
                >
                  <defs>
                    <linearGradient id="equityGradient" x1="0" y1="0" x2="0" y2="1">
                      <stop offset="5%" stopColor="#00ff88" stopOpacity={0.25} />
                      <stop offset="95%" stopColor="#00ff88" stopOpacity={0.02} />
                    </linearGradient>
                  </defs>
                  <CartesianGrid strokeDasharray="3 3" stroke="#1e1e2e" vertical={false} />
                  <XAxis
                    dataKey="label"
                    tick={{ fill: '#6b7280', fontSize: 10 }}
                    tickLine={false}
                    axisLine={false}
                    interval="preserveStartEnd"
                    minTickGap={80}
                  />
                  <YAxis
                    dataKey="equity"
                    tick={{ fill: '#6b7280', fontSize: 10 }}
                    tickLine={false}
                    axisLine={false}
                    tickFormatter={(v) => `$${(v / 1000).toFixed(1)}k`}
                    domain={[equityMin, equityMax]}
                    width={60}
                  />
                  <Tooltip
                    content={<EquityTooltip initialCapital={result.initialCapital} />}
                    cursor={{ stroke: '#2a2a3e', strokeWidth: 1 }}
                  />
                  <ReferenceLine
                    y={result.initialCapital}
                    stroke="#2a2a3e"
                    strokeDasharray="4 4"
                    label={{
                      value: 'Start',
                      position: 'insideBottomLeft',
                      fill: '#6b7280',
                      fontSize: 10,
                    }}
                  />
                  <Area
                    type="monotone"
                    dataKey="equity"
                    stroke="#00ff88"
                    strokeWidth={1.5}
                    fill="url(#equityGradient)"
                    dot={false}
                    activeDot={{ r: 3, fill: '#00ff88' }}
                  />
                </ComposedChart>
              </ResponsiveContainer>
            </div>
          </div>

          {/* Drawdown Chart */}
          <div className="nexus-card p-5">
            <div className="flex items-center justify-between mb-4">
              <div className="text-sm font-semibold text-white">Drawdown</div>
              <div className="text-xs font-mono text-nexus-red">
                Max: {result.maxDrawdown.toFixed(2)}%
              </div>
            </div>
            <div className="h-40">
              <ResponsiveContainer width="100%" height="100%">
                <AreaChart
                  data={result.equityCurve}
                  margin={{ top: 4, right: 8, left: 0, bottom: 0 }}
                >
                  <defs>
                    <linearGradient id="drawdownGradient" x1="0" y1="0" x2="0" y2="1">
                      <stop offset="5%" stopColor="#ff4444" stopOpacity={0.3} />
                      <stop offset="95%" stopColor="#ff4444" stopOpacity={0.02} />
                    </linearGradient>
                  </defs>
                  <CartesianGrid strokeDasharray="3 3" stroke="#1e1e2e" vertical={false} />
                  <XAxis
                    dataKey="label"
                    tick={{ fill: '#6b7280', fontSize: 10 }}
                    tickLine={false}
                    axisLine={false}
                    interval="preserveStartEnd"
                    minTickGap={80}
                  />
                  <YAxis
                    dataKey="drawdown"
                    tick={{ fill: '#6b7280', fontSize: 10 }}
                    tickLine={false}
                    axisLine={false}
                    tickFormatter={(v) => `${v.toFixed(1)}%`}
                    width={55}
                  />
                  <Tooltip
                    content={<DrawdownTooltip />}
                    cursor={{ stroke: '#2a2a3e', strokeWidth: 1 }}
                  />
                  <ReferenceLine y={0} stroke="#2a2a3e" strokeDasharray="4 4" />
                  <Area
                    type="monotone"
                    dataKey="drawdown"
                    stroke="#ff4444"
                    strokeWidth={1.5}
                    fill="url(#drawdownGradient)"
                    dot={false}
                    activeDot={{ r: 3, fill: '#ff4444' }}
                  />
                </AreaChart>
              </ResponsiveContainer>
            </div>
          </div>

          {/* Trade Log Table */}
          <div className="nexus-card p-5">
            <div className="flex items-center justify-between mb-4">
              <div>
                <div className="text-sm font-semibold text-white">Trade Log</div>
                <div className="text-xs text-muted mt-0.5">{result.totalTrades} total trades</div>
              </div>
              <button
                onClick={handleExportCSV}
                className="flex items-center gap-1.5 text-xs text-muted hover:text-nexus-blue transition-colors border border-border hover:border-nexus-blue/40 rounded-lg px-3 py-1.5"
              >
                <Download size={12} />
                Export CSV
              </button>
            </div>

            {result.trades.length === 0 ? (
              <div className="text-center py-12 text-muted text-sm">
                No trades generated. Try adjusting strategy parameters.
              </div>
            ) : (
              <>
                <div className="overflow-x-auto">
                  <table className="nexus-table w-full">
                    <thead>
                      <tr>
                        <th className="text-left">#</th>
                        <th className="text-left">Entry Time</th>
                        <th className="text-left">Exit Time</th>
                        <th className="text-left">Dir</th>
                        <th className="text-right">Entry $</th>
                        <th className="text-right">Exit $</th>
                        <th className="text-right">P&L $</th>
                        <th className="text-right">P&L %</th>
                        <th className="text-right">Duration</th>
                        <th className="text-center">Exit</th>
                      </tr>
                    </thead>
                    <tbody>
                      {paginatedTrades.map((trade) => (
                        <tr key={trade.id}>
                          <td className="text-muted text-xs font-mono">{trade.id}</td>
                          <td className="text-xs font-mono text-muted">
                            {formatCandleTime(trade.entryTime, config.interval)}
                          </td>
                          <td className="text-xs font-mono text-muted">
                            {formatCandleTime(trade.exitTime, config.interval)}
                          </td>
                          <td>
                            <span className="badge bg-nexus-green/10 text-nexus-green border border-nexus-green/20 text-xs">
                              LONG
                            </span>
                          </td>
                          <td className="text-right font-mono text-xs">
                            {trade.entryPrice < 1
                              ? trade.entryPrice.toFixed(5)
                              : trade.entryPrice < 10
                              ? trade.entryPrice.toFixed(4)
                              : trade.entryPrice.toFixed(2)}
                          </td>
                          <td className="text-right font-mono text-xs">
                            {trade.exitPrice < 1
                              ? trade.exitPrice.toFixed(5)
                              : trade.exitPrice < 10
                              ? trade.exitPrice.toFixed(4)
                              : trade.exitPrice.toFixed(2)}
                          </td>
                          <td
                            className={cn(
                              'text-right font-mono text-xs font-semibold',
                              getPnlColor(trade.pnl)
                            )}
                          >
                            {trade.pnl >= 0 ? '+' : ''}{formatCurrency(trade.pnl)}
                          </td>
                          <td
                            className={cn(
                              'text-right font-mono text-xs',
                              getPnlColor(trade.pnlPct)
                            )}
                          >
                            {trade.pnlPct >= 0 ? '+' : ''}{trade.pnlPct.toFixed(2)}%
                          </td>
                          <td className="text-right font-mono text-xs text-muted">
                            {trade.duration}
                          </td>
                          <td className="text-center">
                            <span
                              className={cn(
                                'badge text-xs',
                                trade.exitReason === 'TP'
                                  ? 'bg-nexus-green/10 text-nexus-green border border-nexus-green/20'
                                  : trade.exitReason === 'SL'
                                  ? 'bg-nexus-red/10 text-nexus-red border border-nexus-red/20'
                                  : 'bg-nexus-blue/10 text-nexus-blue border border-nexus-blue/20'
                              )}
                            >
                              {trade.exitReason}
                            </span>
                          </td>
                        </tr>
                      ))}
                    </tbody>
                  </table>
                </div>

                {/* Pagination */}
                {totalPages > 1 && (
                  <div className="flex items-center justify-between mt-4 pt-4 border-t border-border">
                    <div className="text-xs text-muted">
                      Page {tradePage + 1} of {totalPages} &middot;{' '}
                      {result.trades.length} trades
                    </div>
                    <div className="flex items-center gap-2">
                      <button
                        onClick={() => setTradePage((p) => Math.max(0, p - 1))}
                        disabled={tradePage === 0}
                        className="p-1.5 rounded-lg border border-border text-muted hover:text-white hover:border-border-bright disabled:opacity-30 disabled:cursor-not-allowed transition-colors"
                      >
                        <ChevronLeft size={14} />
                      </button>
                      {Array.from({ length: Math.min(totalPages, 7) }, (_, i) => {
                        let page = i;
                        if (totalPages > 7) {
                          const start = Math.max(0, Math.min(tradePage - 3, totalPages - 7));
                          page = start + i;
                        }
                        return (
                          <button
                            key={page}
                            onClick={() => setTradePage(page)}
                            className={cn(
                              'w-7 h-7 rounded-lg text-xs font-mono transition-colors',
                              page === tradePage
                                ? 'bg-nexus-blue/20 text-nexus-blue border border-nexus-blue/40'
                                : 'border border-border text-muted hover:text-white hover:border-border-bright'
                            )}
                          >
                            {page + 1}
                          </button>
                        );
                      })}
                      <button
                        onClick={() => setTradePage((p) => Math.min(totalPages - 1, p + 1))}
                        disabled={tradePage === totalPages - 1}
                        className="p-1.5 rounded-lg border border-border text-muted hover:text-white hover:border-border-bright disabled:opacity-30 disabled:cursor-not-allowed transition-colors"
                      >
                        <ChevronRight size={14} />
                      </button>
                    </div>
                  </div>
                )}
              </>
            )}
          </div>
        </div>
      )}
    </div>
  );
}
