'use client';

import { useState, useEffect, useCallback, useRef } from 'react';
import { useRouter } from 'next/navigation';
import {
  RefreshCw,
  ArrowUpDown,
  ArrowUp,
  ArrowDown,
  TrendingUp,
  TrendingDown,
  Minus,
  Clock,
  ScanSearch,
} from 'lucide-react';
import { cn, formatNumber, formatPercent } from '@/lib/utils';
import type { Market } from '@/lib/types';

// ─── Types ───────────────────────────────────────────────────────────────────

type SignalType = 'LONG' | 'SHORT' | 'WATCH' | 'NEUTRAL';
type SortKey = 'strength' | 'rsi' | 'change' | 'market';
type SortDir = 'asc' | 'desc';
type MarketFilter = 'all' | Market;
type Timeframe = '1h' | '4h' | '1d';
type Volatility = 'High' | 'Med' | 'Low';

interface WatchlistItem {
  symbol: string;
  label: string;
  market: Market;
  binanceSymbol?: string;
}

interface ScannerRow {
  symbol: string;
  label: string;
  market: Market;
  price: number | null;
  change: number | null;
  rsi: number | null;
  macdBull: boolean | null;
  trendUp: boolean | null;
  volatility: Volatility | null;
  signal: SignalType;
  strength: number;
  loading: boolean;
  error: boolean;
}

// ─── Constants ───────────────────────────────────────────────────────────────

const WATCHLIST: WatchlistItem[] = [
  { symbol: 'BTCUSDT',  label: 'BTC/USDT', market: 'crypto',      binanceSymbol: 'BTCUSDT' },
  { symbol: 'ETHUSDT',  label: 'ETH/USDT', market: 'crypto',      binanceSymbol: 'ETHUSDT' },
  { symbol: 'SOLUSDT',  label: 'SOL/USDT', market: 'crypto',      binanceSymbol: 'SOLUSDT' },
  { symbol: 'BNBUSDT',  label: 'BNB/USDT', market: 'crypto',      binanceSymbol: 'BNBUSDT' },
  { symbol: 'XRPUSDT',  label: 'XRP/USDT', market: 'crypto',      binanceSymbol: 'XRPUSDT' },
  { symbol: 'ADAUSDT',  label: 'ADA/USDT', market: 'crypto',      binanceSymbol: 'ADAUSDT' },
  { symbol: 'EURUSD',   label: 'EUR/USD',  market: 'forex' },
  { symbol: 'GBPUSD',   label: 'GBP/USD',  market: 'forex' },
  { symbol: 'USDJPY',   label: 'USD/JPY',  market: 'forex' },
  { symbol: 'AUDUSD',   label: 'AUD/USD',  market: 'forex' },
  { symbol: 'XAUUSD',   label: 'Gold',     market: 'commodities' },
  { symbol: 'XAGUSD',   label: 'Silver',   market: 'commodities' },
  { symbol: 'SPX',      label: 'S&P 500',  market: 'us_stocks' },
  { symbol: 'NDX',      label: 'Nasdaq 100', market: 'us_stocks' },
];

const MARKET_LABELS: Record<MarketFilter, string> = {
  all:          'All',
  crypto:       'Crypto',
  forex:        'Forex',
  commodities:  'Commodities',
  indian_stocks:'Indian',
  us_stocks:    'US',
};

const MARKET_FILTERS: MarketFilter[] = ['all', 'crypto', 'forex', 'commodities', 'us_stocks'];

const AUTO_REFRESH_SECONDS = 60;

// ─── Technical computations ───────────────────────────────────────────────────

function computeEMA(closes: number[], period: number): number[] {
  const k = 2 / (period + 1);
  const ema: number[] = [closes[0]];
  for (let i = 1; i < closes.length; i++) {
    ema.push(closes[i] * k + ema[i - 1] * (1 - k));
  }
  return ema;
}

function computeRSI(closes: number[], period = 14): number {
  if (closes.length < period + 1) return 50;
  let gains = 0, losses = 0;
  for (let i = closes.length - period; i < closes.length; i++) {
    const diff = closes[i] - closes[i - 1];
    if (diff > 0) gains += diff;
    else losses += Math.abs(diff);
  }
  const avgGain = gains / period;
  const avgLoss = losses / period;
  if (avgLoss === 0) return 100;
  const rs = avgGain / avgLoss;
  return 100 - 100 / (1 + rs);
}

function computeMACD(closes: number[]): boolean {
  // Returns true when MACD line (EMA12 - EMA26) > 0 (bullish)
  if (closes.length < 27) return false;
  const ema12 = computeEMA(closes, 12);
  const ema26 = computeEMA(closes, 26);
  const macd = ema12[ema12.length - 1] - ema26[ema26.length - 1];
  return macd > 0;
}

function computeTrend(closes: number[], period = 20): boolean {
  if (closes.length < period) return true;
  const slice = closes.slice(-period);
  const sma = slice.reduce((a, b) => a + b, 0) / period;
  return closes[closes.length - 1] > sma;
}

function computeVolatility(closes: number[]): Volatility {
  if (closes.length < 2) return 'Med';
  const returns = [];
  for (let i = 1; i < closes.length; i++) {
    returns.push(Math.abs((closes[i] - closes[i - 1]) / closes[i - 1]));
  }
  const avgReturn = returns.reduce((a, b) => a + b, 0) / returns.length;
  if (avgReturn > 0.025) return 'High';
  if (avgReturn > 0.008) return 'Med';
  return 'Low';
}

function computeSignal(
  rsi: number,
  macdBull: boolean,
  trendUp: boolean
): { signal: SignalType; strength: number } {
  let score = 50;
  if (rsi < 30) score += 25;
  else if (rsi > 70) score -= 25;
  if (macdBull) score += 15;
  if (trendUp) score += 10;

  if (score >= 65) return { signal: 'LONG',    strength: score };
  if (score <= 35) return { signal: 'SHORT',   strength: 100 - score };
  if (score >= 55) return { signal: 'WATCH',   strength: score };
  return                  { signal: 'NEUTRAL', strength: 50 };
}

// ─── Data fetchers ────────────────────────────────────────────────────────────

interface BinanceKline {
  close: number;
}

async function fetchCryptoData(
  binanceSymbol: string,
  interval: string
): Promise<{ price: number; change: number; rsi: number; macdBull: boolean; trendUp: boolean; volatility: Volatility }> {
  const limit = 40; // enough for EMA26 + buffer
  const url = `https://api.binance.com/api/v3/klines?symbol=${binanceSymbol}&interval=${interval}&limit=${limit}`;
  const res = await fetch(url, { cache: 'no-store' });
  if (!res.ok) throw new Error(`Binance HTTP ${res.status}`);
  // eslint-disable-next-line @typescript-eslint/no-explicit-any
  const raw: any[][] = await res.json();
  const closes: number[] = raw.map((k) => parseFloat(k[4]));
  const open0 = parseFloat(raw[0][1]);
  const current = closes[closes.length - 1];
  const change = ((current - open0) / open0) * 100;

  return {
    price:      current,
    change,
    rsi:        computeRSI(closes),
    macdBull:   computeMACD(closes),
    trendUp:    computeTrend(closes),
    volatility: computeVolatility(closes),
  };
}

// Frankfurter returns base=EUR rates; we invert/compute needed pairs
async function fetchForexData(symbol: string): Promise<{ price: number; change: number }> {
  // Today + yesterday
  const today = new Date();
  const yesterday = new Date(today);
  yesterday.setDate(yesterday.getDate() - 1);
  const fmtDate = (d: Date) => d.toISOString().split('T')[0];

  const [latestRes, prevRes] = await Promise.all([
    fetch(`https://api.frankfurter.app/latest`, { cache: 'no-store' }),
    fetch(`https://api.frankfurter.app/${fmtDate(yesterday)}`, { cache: 'no-store' }),
  ]);
  if (!latestRes.ok || !prevRes.ok) throw new Error('Frankfurter error');
  const latest = await latestRes.json();
  const prev   = await prevRes.json();

  // Rates are relative to EUR (base). Derive cross rates.
  function getRate(sym: string, rates: Record<string, number>): number {
    if (sym === 'EURUSD') return rates['USD'];
    if (sym === 'GBPUSD') return rates['USD'] / rates['GBP'];
    if (sym === 'USDJPY') return rates['JPY'];
    if (sym === 'AUDUSD') return rates['USD'] / rates['AUD'];
    return 0;
  }

  const latestRate = getRate(symbol, latest.rates);
  const prevRate   = getRate(symbol, prev.rates);
  if (!latestRate) throw new Error(`No rate for ${symbol}`);
  const change = ((latestRate - prevRate) / prevRate) * 100;
  return { price: latestRate, change };
}

async function fetchCommodityData(symbol: string): Promise<{ price: number; change: number }> {
  // metals.live free endpoint
  const res = await fetch('https://api.metals.live/v1/spot', { cache: 'no-store' });
  if (!res.ok) throw new Error(`metals.live HTTP ${res.status}`);
  // eslint-disable-next-line @typescript-eslint/no-explicit-any
  const data: any[] = await res.json();
  // Array of objects: [{ gold: number, silver: number, ... }]
  const latest = data[data.length - 1] ?? data[0];
  if (symbol === 'XAUUSD') {
    const price = latest?.gold ?? latest?.XAU ?? 0;
    // No historical: use a small proxy (metals.live often returns today + yesterday)
    const prev  = data.length > 1 ? (data[data.length - 2]?.gold ?? price) : price;
    const change = prev ? ((price - prev) / prev) * 100 : 0;
    return { price, change };
  }
  if (symbol === 'XAGUSD') {
    const price = latest?.silver ?? latest?.XAG ?? 0;
    const prev  = data.length > 1 ? (data[data.length - 2]?.silver ?? price) : price;
    const change = prev ? ((price - prev) / prev) * 100 : 0;
    return { price, change };
  }
  throw new Error(`Unknown commodity ${symbol}`);
}

// US stock indices — approximate via Binance futures / Yahoo-compatible endpoint
// No free no-auth index endpoint is truly reliable; use a lightweight proxy approach.
async function fetchIndexData(symbol: string): Promise<{ price: number; change: number }> {
  // Use stooq.com free CSV (no auth required)
  const stooqMap: Record<string, string> = { SPX: '^spx', NDX: '^ndx' };
  const ticker = stooqMap[symbol];
  if (!ticker) throw new Error(`Unknown index ${symbol}`);
  const url = `https://stooq.com/q/l/?s=${ticker}&f=sd2t2ohlcv&h&e=csv`;
  const res = await fetch(url, { cache: 'no-store' });
  if (!res.ok) throw new Error(`stooq HTTP ${res.status}`);
  const text = await res.text();
  const lines = text.trim().split('\n');
  if (lines.length < 2) throw new Error('stooq empty response');
  const parts = lines[1].split(',');
  // columns: Symbol,Date,Time,Open,High,Low,Close,Volume
  const open  = parseFloat(parts[3]);
  const close = parseFloat(parts[6]);
  if (!close || isNaN(close)) throw new Error('stooq parse error');
  const change = ((close - open) / open) * 100;
  return { price: close, change };
}

// ─── Signal badge helpers ─────────────────────────────────────────────────────

function signalBadgeClass(signal: SignalType): string {
  switch (signal) {
    case 'LONG':    return 'bg-nexus-green/15 text-nexus-green border border-nexus-green/30';
    case 'SHORT':   return 'bg-nexus-red/15 text-nexus-red border border-nexus-red/30';
    case 'WATCH':   return 'bg-nexus-yellow/15 text-nexus-yellow border border-nexus-yellow/30';
    default:        return 'bg-white/5 text-muted border border-border';
  }
}

function signalBarColor(signal: SignalType): string {
  switch (signal) {
    case 'LONG':  return 'bg-nexus-green';
    case 'SHORT': return 'bg-nexus-red';
    case 'WATCH': return 'bg-nexus-yellow';
    default:      return 'bg-muted';
  }
}

function rsiColorClass(rsi: number): string {
  if (rsi < 30)  return 'text-nexus-green';
  if (rsi < 50)  return 'text-green-400';
  if (rsi < 70)  return 'text-nexus-yellow';
  return 'text-nexus-red';
}

function marketBadgeClass(market: Market): string {
  switch (market) {
    case 'crypto':       return 'bg-nexus-blue/10 text-nexus-blue border border-nexus-blue/20';
    case 'forex':        return 'bg-purple-500/10 text-purple-400 border border-purple-500/20';
    case 'commodities':  return 'bg-amber-500/10 text-amber-400 border border-amber-500/20';
    case 'us_stocks':    return 'bg-nexus-green/10 text-nexus-green border border-nexus-green/20';
    case 'indian_stocks':return 'bg-orange-500/10 text-orange-400 border border-orange-500/20';
  }
}

function formatPrice(price: number, market: Market, label: string): string {
  if (market === 'forex') {
    if (label.includes('JPY')) return price.toFixed(3);
    return price.toFixed(4);
  }
  if (market === 'crypto') {
    if (price > 1000)   return `$${price.toLocaleString('en-US', { maximumFractionDigits: 0 })}`;
    if (price > 1)      return `$${price.toFixed(3)}`;
    return `$${price.toFixed(5)}`;
  }
  if (price > 1000)    return `$${price.toLocaleString('en-US', { maximumFractionDigits: 0 })}`;
  return `$${price.toFixed(2)}`;
}

// ─── Skeleton row ─────────────────────────────────────────────────────────────

function SkeletonRow() {
  return (
    <tr className="border-b border-border/30 animate-pulse">
      {Array.from({ length: 11 }).map((_, i) => (
        <td key={i} className="px-3 py-3">
          <div className="h-4 rounded bg-white/5" style={{ width: `${40 + (i % 3) * 20}%` }} />
        </td>
      ))}
    </tr>
  );
}

// ─── Timeframe interval mapping ───────────────────────────────────────────────

const TF_TO_BINANCE: Record<Timeframe, string> = { '1h': '1h', '4h': '4h', '1d': '1d' };

// ─── Main page component ──────────────────────────────────────────────────────

export default function ScannerPage() {
  const router = useRouter();

  const [marketFilter, setMarketFilter] = useState<MarketFilter>('all');
  const [timeframe, setTimeframe]       = useState<Timeframe>('1h');
  const [sortKey, setSortKey]           = useState<SortKey>('strength');
  const [sortDir, setSortDir]           = useState<SortDir>('desc');
  const [rows, setRows]                 = useState<ScannerRow[]>([]);
  const [lastScanned, setLastScanned]   = useState<Date | null>(null);
  const [countdown, setCountdown]       = useState(AUTO_REFRESH_SECONDS);
  const [isScanning, setIsScanning]     = useState(false);

  const abortRef = useRef<AbortController | null>(null);

  // Build initial skeleton rows
  useEffect(() => {
    setRows(
      WATCHLIST.map((w) => ({
        symbol:     w.symbol,
        label:      w.label,
        market:     w.market,
        price:      null,
        change:     null,
        rsi:        null,
        macdBull:   null,
        trendUp:    null,
        volatility: null,
        signal:     'NEUTRAL',
        strength:   50,
        loading:    true,
        error:      false,
      }))
    );
  }, []);

  // ── Fetch one row ──────────────────────────────────────────────────────────
  const fetchRow = useCallback(
    async (item: WatchlistItem, tf: Timeframe): Promise<Partial<ScannerRow>> => {
      try {
        if (item.market === 'crypto' && item.binanceSymbol) {
          const d = await fetchCryptoData(item.binanceSymbol, TF_TO_BINANCE[tf]);
          const { signal, strength } = computeSignal(d.rsi, d.macdBull, d.trendUp);
          return {
            price:      d.price,
            change:     d.change,
            rsi:        d.rsi,
            macdBull:   d.macdBull,
            trendUp:    d.trendUp,
            volatility: d.volatility,
            signal,
            strength,
            loading:    false,
            error:      false,
          };
        }

        if (item.market === 'forex') {
          const d = await fetchForexData(item.symbol);
          // Forex: RSI/MACD not available from simple price — use heuristic defaults
          const rsi  = 50; // neutral default; no candle history from Frankfurter
          const macdBull = d.change > 0;
          const trendUp  = d.change > 0;
          const { signal, strength } = computeSignal(rsi, macdBull, trendUp);
          return {
            price:      d.price,
            change:     d.change,
            rsi,
            macdBull,
            trendUp,
            volatility: Math.abs(d.change) > 0.5 ? 'High' : Math.abs(d.change) > 0.1 ? 'Med' : 'Low',
            signal,
            strength,
            loading:    false,
            error:      false,
          };
        }

        if (item.market === 'commodities') {
          const d = await fetchCommodityData(item.symbol);
          const rsi  = 50;
          const macdBull = d.change > 0;
          const trendUp  = d.change > 0;
          const { signal, strength } = computeSignal(rsi, macdBull, trendUp);
          return {
            price:      d.price,
            change:     d.change,
            rsi,
            macdBull,
            trendUp,
            volatility: Math.abs(d.change) > 1 ? 'High' : Math.abs(d.change) > 0.3 ? 'Med' : 'Low',
            signal,
            strength,
            loading:    false,
            error:      false,
          };
        }

        if (item.market === 'us_stocks') {
          const d = await fetchIndexData(item.symbol);
          const rsi  = 50;
          const macdBull = d.change > 0;
          const trendUp  = d.change > 0;
          const { signal, strength } = computeSignal(rsi, macdBull, trendUp);
          return {
            price:      d.price,
            change:     d.change,
            rsi,
            macdBull,
            trendUp,
            volatility: Math.abs(d.change) > 1.5 ? 'High' : Math.abs(d.change) > 0.5 ? 'Med' : 'Low',
            signal,
            strength,
            loading:    false,
            error:      false,
          };
        }

        throw new Error('Unsupported market');
      } catch {
        return { loading: false, error: true, signal: 'NEUTRAL', strength: 50 };
      }
    },
    []
  );

  // ── Full scan ──────────────────────────────────────────────────────────────
  const runScan = useCallback(
    async (tf: Timeframe) => {
      if (abortRef.current) abortRef.current.abort();
      abortRef.current = new AbortController();

      setIsScanning(true);

      // Reset all rows to loading
      setRows(
        WATCHLIST.map((w) => ({
          symbol:     w.symbol,
          label:      w.label,
          market:     w.market,
          price:      null,
          change:     null,
          rsi:        null,
          macdBull:   null,
          trendUp:    null,
          volatility: null,
          signal:     'NEUTRAL',
          strength:   50,
          loading:    true,
          error:      false,
        }))
      );

      // Fetch all symbols concurrently, update each as it resolves
      await Promise.allSettled(
        WATCHLIST.map(async (item) => {
          const partial = await fetchRow(item, tf);
          setRows((prev) =>
            prev.map((r) =>
              r.symbol === item.symbol ? { ...r, ...partial } : r
            )
          );
        })
      );

      setLastScanned(new Date());
      setCountdown(AUTO_REFRESH_SECONDS);
      setIsScanning(false);
    },
    [fetchRow]
  );

  // ── Initial scan + timeframe change ───────────────────────────────────────
  useEffect(() => {
    runScan(timeframe);
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [timeframe]);

  // ── Countdown + auto-refresh ───────────────────────────────────────────────
  useEffect(() => {
    const interval = setInterval(() => {
      setCountdown((c) => {
        if (c <= 1) {
          runScan(timeframe);
          return AUTO_REFRESH_SECONDS;
        }
        return c - 1;
      });
    }, 1000);
    return () => clearInterval(interval);
  }, [runScan, timeframe]);

  // ── Sort handler ───────────────────────────────────────────────────────────
  function handleSort(key: SortKey) {
    if (sortKey === key) {
      setSortDir((d) => (d === 'asc' ? 'desc' : 'asc'));
    } else {
      setSortKey(key);
      setSortDir('desc');
    }
  }

  // ── Derived display rows ───────────────────────────────────────────────────
  const displayRows = [...rows]
    .filter((r) => marketFilter === 'all' || r.market === marketFilter)
    .sort((a, b) => {
      let va: number, vb: number;
      switch (sortKey) {
        case 'strength': va = a.strength; vb = b.strength; break;
        case 'rsi':      va = a.rsi ?? 50; vb = b.rsi ?? 50; break;
        case 'change':   va = a.change ?? 0; vb = b.change ?? 0; break;
        case 'market': {
          const order: Record<Market, number> = { crypto: 0, forex: 1, commodities: 2, us_stocks: 3, indian_stocks: 4 };
          va = order[a.market]; vb = order[b.market]; break;
        }
        default: va = 0; vb = 0;
      }
      return sortDir === 'desc' ? vb - va : va - vb;
    });

  // ── Sort icon helper ───────────────────────────────────────────────────────
  function SortIcon({ k }: { k: SortKey }) {
    if (sortKey !== k) return <ArrowUpDown size={12} className="text-muted opacity-40" />;
    return sortDir === 'desc'
      ? <ArrowDown size={12} className="text-nexus-blue" />
      : <ArrowUp   size={12} className="text-nexus-blue" />;
  }

  // ── Market route map ───────────────────────────────────────────────────────
  const marketRoutes: Record<Market, string> = {
    crypto:        '/crypto',
    forex:         '/forex',
    commodities:   '/commodities',
    us_stocks:     '/us-stocks',
    indian_stocks: '/indian-stocks',
  };

  // ─── Summary stats for top cards ──────────────────────────────────────────
  const completedRows = rows.filter((r) => !r.loading && !r.error);
  const longCount    = completedRows.filter((r) => r.signal === 'LONG').length;
  const shortCount   = completedRows.filter((r) => r.signal === 'SHORT').length;
  const watchCount   = completedRows.filter((r) => r.signal === 'WATCH').length;
  const avgStrength  = completedRows.length
    ? Math.round(completedRows.reduce((s, r) => s + r.strength, 0) / completedRows.length)
    : 0;

  return (
    <div className="space-y-4">

      {/* ── Page header ── */}
      <div className="flex items-center gap-3">
        <div className="p-2 rounded-lg bg-nexus-blue/10 border border-nexus-blue/20">
          <ScanSearch size={20} className="text-nexus-blue" />
        </div>
        <div>
          <h1 className="text-lg font-bold text-white tracking-wide">Market Scanner</h1>
          <p className="text-xs text-muted">
            Real-time technical signal scan across all watched markets
          </p>
        </div>
      </div>

      {/* ── Summary cards ── */}
      <div className="grid grid-cols-2 md:grid-cols-4 gap-3">
        <div className="nexus-card p-3 flex items-center gap-3">
          <div className="w-8 h-8 rounded-md bg-nexus-green/10 border border-nexus-green/20 flex items-center justify-center">
            <TrendingUp size={16} className="text-nexus-green" />
          </div>
          <div>
            <div className="text-xs text-muted">Long Signals</div>
            <div className="font-mono text-xl font-bold text-nexus-green">{longCount}</div>
          </div>
        </div>
        <div className="nexus-card p-3 flex items-center gap-3">
          <div className="w-8 h-8 rounded-md bg-nexus-red/10 border border-nexus-red/20 flex items-center justify-center">
            <TrendingDown size={16} className="text-nexus-red" />
          </div>
          <div>
            <div className="text-xs text-muted">Short Signals</div>
            <div className="font-mono text-xl font-bold text-nexus-red">{shortCount}</div>
          </div>
        </div>
        <div className="nexus-card p-3 flex items-center gap-3">
          <div className="w-8 h-8 rounded-md bg-nexus-yellow/10 border border-nexus-yellow/20 flex items-center justify-center">
            <Minus size={16} className="text-nexus-yellow" />
          </div>
          <div>
            <div className="text-xs text-muted">Watch Setups</div>
            <div className="font-mono text-xl font-bold text-nexus-yellow">{watchCount}</div>
          </div>
        </div>
        <div className="nexus-card p-3 flex items-center gap-3">
          <div className="w-8 h-8 rounded-md bg-nexus-blue/10 border border-nexus-blue/20 flex items-center justify-center">
            <RefreshCw size={16} className="text-nexus-blue" />
          </div>
          <div>
            <div className="text-xs text-muted">Avg Strength</div>
            <div className="font-mono text-xl font-bold text-nexus-blue">
              {completedRows.length ? `${avgStrength}%` : '—'}
            </div>
          </div>
        </div>
      </div>

      {/* ── Control bar ── */}
      <div className="nexus-card p-3">
        <div className="flex flex-wrap items-center gap-3 justify-between">

          {/* Market filter */}
          <div className="flex items-center gap-1 flex-wrap">
            {MARKET_FILTERS.map((f) => (
              <button
                key={f}
                onClick={() => setMarketFilter(f)}
                className={cn(
                  'px-3 py-1.5 rounded-md text-xs font-medium transition-colors',
                  marketFilter === f
                    ? 'bg-nexus-blue text-black font-bold'
                    : 'bg-white/5 text-muted hover:text-white hover:bg-white/10'
                )}
              >
                {MARKET_LABELS[f]}
              </button>
            ))}
          </div>

          {/* Timeframe + refresh */}
          <div className="flex items-center gap-2">
            {/* Timeframe */}
            <div className="flex items-center gap-1 bg-white/5 rounded-md p-1">
              {(['1h', '4h', '1d'] as Timeframe[]).map((tf) => (
                <button
                  key={tf}
                  onClick={() => setTimeframe(tf)}
                  className={cn(
                    'px-2.5 py-1 rounded text-xs font-mono font-medium transition-colors',
                    timeframe === tf
                      ? 'bg-nexus-blue/20 text-nexus-blue'
                      : 'text-muted hover:text-white'
                  )}
                >
                  {tf}
                </button>
              ))}
            </div>

            {/* Manual refresh */}
            <button
              onClick={() => runScan(timeframe)}
              disabled={isScanning}
              className="flex items-center gap-1.5 px-3 py-1.5 rounded-md bg-white/5 hover:bg-white/10 text-muted hover:text-white text-xs transition-colors disabled:opacity-50"
            >
              <RefreshCw size={12} className={cn(isScanning && 'animate-spin')} />
              {isScanning ? 'Scanning...' : 'Refresh'}
            </button>

            {/* Countdown */}
            <div className="flex items-center gap-1 text-xs text-muted font-mono">
              <Clock size={11} />
              <span>{countdown}s</span>
            </div>
          </div>
        </div>

        {/* Last scanned */}
        {lastScanned && (
          <div className="mt-2 text-xs text-muted">
            Last scanned:{' '}
            <span className="text-white/60 font-mono">
              {lastScanned.toLocaleTimeString()}
            </span>
          </div>
        )}
      </div>

      {/* ── Scanner table ── */}
      <div className="nexus-card overflow-hidden">
        <div className="overflow-x-auto">
          <table className="w-full min-w-[900px] text-sm">
            <thead>
              <tr className="border-b border-border text-xs text-muted uppercase tracking-wider">
                <th className="text-left px-3 py-3 font-medium">Symbol</th>
                <th
                  className="text-left px-3 py-3 font-medium cursor-pointer hover:text-white transition-colors"
                  onClick={() => handleSort('market')}
                >
                  <span className="flex items-center gap-1">Market <SortIcon k="market" /></span>
                </th>
                <th className="text-right px-3 py-3 font-medium">Price</th>
                <th
                  className="text-right px-3 py-3 font-medium cursor-pointer hover:text-white transition-colors"
                  onClick={() => handleSort('change')}
                >
                  <span className="flex items-center gap-1 justify-end">Change <SortIcon k="change" /></span>
                </th>
                <th
                  className="text-right px-3 py-3 font-medium cursor-pointer hover:text-white transition-colors"
                  onClick={() => handleSort('rsi')}
                >
                  <span className="flex items-center gap-1 justify-end">RSI <SortIcon k="rsi" /></span>
                </th>
                <th className="text-center px-3 py-3 font-medium">MACD</th>
                <th className="text-center px-3 py-3 font-medium">Trend</th>
                <th className="text-center px-3 py-3 font-medium">Volatility</th>
                <th className="text-center px-3 py-3 font-medium">Signal</th>
                <th
                  className="text-left px-3 py-3 font-medium cursor-pointer hover:text-white transition-colors"
                  onClick={() => handleSort('strength')}
                >
                  <span className="flex items-center gap-1">Strength <SortIcon k="strength" /></span>
                </th>
                <th className="text-center px-3 py-3 font-medium">Action</th>
              </tr>
            </thead>
            <tbody>
              {displayRows.length === 0 && (
                Array.from({ length: WATCHLIST.length }).map((_, i) => (
                  <SkeletonRow key={i} />
                ))
              )}

              {displayRows.map((row) => {
                if (row.loading) return <SkeletonRow key={row.symbol} />;

                return (
                  <tr
                    key={row.symbol}
                    className="border-b border-border/30 hover:bg-white/[0.02] transition-colors group"
                  >
                    {/* Symbol */}
                    <td className="px-3 py-3">
                      <span className="font-mono font-semibold text-white text-sm">
                        {row.label}
                      </span>
                    </td>

                    {/* Market */}
                    <td className="px-3 py-3">
                      <span className={cn(
                        'inline-flex items-center px-2 py-0.5 rounded text-xs font-medium',
                        marketBadgeClass(row.market)
                      )}>
                        {MARKET_LABELS[row.market]}
                      </span>
                    </td>

                    {/* Price */}
                    <td className="px-3 py-3 text-right">
                      {row.error || row.price === null ? (
                        <span className="text-muted font-mono">—</span>
                      ) : (
                        <span className="font-mono text-white text-sm">
                          {formatPrice(row.price, row.market, row.label)}
                        </span>
                      )}
                    </td>

                    {/* Change */}
                    <td className="px-3 py-3 text-right">
                      {row.error || row.change === null ? (
                        <span className="text-muted font-mono">—</span>
                      ) : (
                        <span className={cn(
                          'font-mono text-sm font-medium',
                          row.change > 0 ? 'text-nexus-green' : row.change < 0 ? 'text-nexus-red' : 'text-muted'
                        )}>
                          {row.change > 0 ? '+' : ''}{formatNumber(row.change, 2)}%
                        </span>
                      )}
                    </td>

                    {/* RSI */}
                    <td className="px-3 py-3 text-right">
                      {row.error || row.rsi === null ? (
                        <span className="text-muted font-mono">—</span>
                      ) : (
                        <span className={cn('font-mono text-sm font-bold', rsiColorClass(row.rsi))}>
                          {Math.round(row.rsi)}
                        </span>
                      )}
                    </td>

                    {/* MACD */}
                    <td className="px-3 py-3 text-center">
                      {row.error || row.macdBull === null ? (
                        <span className="text-muted">—</span>
                      ) : row.macdBull ? (
                        <span className="inline-flex items-center gap-1 text-nexus-green text-xs font-medium">
                          <TrendingUp size={12} /> Bull
                        </span>
                      ) : (
                        <span className="inline-flex items-center gap-1 text-nexus-red text-xs font-medium">
                          <TrendingDown size={12} /> Bear
                        </span>
                      )}
                    </td>

                    {/* Trend */}
                    <td className="px-3 py-3 text-center">
                      {row.error || row.trendUp === null ? (
                        <span className="text-muted">—</span>
                      ) : row.trendUp ? (
                        <span className="inline-flex items-center gap-1 text-nexus-green text-xs font-medium">
                          <ArrowUp size={12} /> Up
                        </span>
                      ) : (
                        <span className="inline-flex items-center gap-1 text-nexus-red text-xs font-medium">
                          <ArrowDown size={12} /> Dn
                        </span>
                      )}
                    </td>

                    {/* Volatility */}
                    <td className="px-3 py-3 text-center">
                      {row.error || row.volatility === null ? (
                        <span className="text-muted">—</span>
                      ) : (
                        <span className={cn(
                          'text-xs font-medium font-mono',
                          row.volatility === 'High' ? 'text-nexus-red'
                          : row.volatility === 'Med' ? 'text-nexus-yellow'
                          : 'text-nexus-green'
                        )}>
                          {row.volatility}
                        </span>
                      )}
                    </td>

                    {/* Signal badge */}
                    <td className="px-3 py-3 text-center">
                      <span className={cn(
                        'inline-flex items-center px-2.5 py-1 rounded-md text-xs font-bold tracking-wide',
                        signalBadgeClass(row.signal)
                      )}>
                        {row.signal}
                      </span>
                    </td>

                    {/* Strength bar */}
                    <td className="px-3 py-3">
                      <div className="flex items-center gap-2 min-w-[100px]">
                        <div className="flex-1 h-1.5 rounded-full bg-white/10 overflow-hidden">
                          <div
                            className={cn('h-full rounded-full transition-all duration-700', signalBarColor(row.signal))}
                            style={{ width: `${row.strength}%` }}
                          />
                        </div>
                        <span className={cn(
                          'font-mono text-xs font-semibold w-8 text-right shrink-0',
                          signalBarColor(row.signal).replace('bg-', 'text-')
                        )}>
                          {row.strength}%
                        </span>
                      </div>
                    </td>

                    {/* Action */}
                    <td className="px-3 py-3 text-center">
                      <button
                        onClick={() => router.push(marketRoutes[row.market])}
                        className="px-3 py-1 rounded-md bg-white/5 hover:bg-nexus-blue/20 text-muted hover:text-nexus-blue text-xs font-medium transition-colors border border-transparent hover:border-nexus-blue/30"
                      >
                        View
                      </button>
                    </td>
                  </tr>
                );
              })}
            </tbody>
          </table>
        </div>

        {/* Empty state */}
        {displayRows.length === 0 && !rows.some((r) => r.loading) && (
          <div className="py-12 text-center text-muted text-sm">
            No symbols match the current filter.
          </div>
        )}
      </div>

      {/* ── Footer note ── */}
      <p className="text-xs text-muted/60 text-center pb-2">
        Crypto: Binance API &nbsp;|&nbsp; Forex: Frankfurter &nbsp;|&nbsp; Metals: metals.live &nbsp;|&nbsp; Indices: stooq.com
        &nbsp;· Signal logic is for informational purposes only and does not constitute financial advice.
      </p>
    </div>
  );
}
