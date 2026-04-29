'use client';

import { useEffect, useState } from 'react';
import * as Tabs from '@radix-ui/react-tabs';
import {
  TrendingUp,
  AlertTriangle,
  Droplets,
  Activity,
  ExternalLink,
} from 'lucide-react';
import {
  AreaChart,
  Area,
  XAxis,
  YAxis,
  ResponsiveContainer,
  Tooltip,
} from 'recharts';
import { CandlestickChart } from '@/components/charts/CandlestickChart';
import { SignalFeed } from '@/components/SignalFeed';
import { useNexusStore } from '@/lib/store';
import { getRecentSignals } from '@/lib/supabase';
import {
  cn,
  formatCurrency,
  getDirectionBg,
} from '@/lib/utils';
import type { Signal, FearGreedIndex } from '@/lib/types';

// ── Constants ─────────────────────────────────────────────────────────────────

const CRYPTO_PAIRS = [
  { symbol: 'BTCUSDT', label: 'BTC/USDT', basePrice: 0 },
  { symbol: 'ETHUSDT', label: 'ETH/USDT', basePrice: 0 },
  { symbol: 'SOLUSDT', label: 'SOL/USDT', basePrice: 0 },
];

// ── Helpers ───────────────────────────────────────────────────────────────────

function formatAge(seconds: number): string {
  if (seconds < 60) return 'just now';
  if (seconds < 3600) return `${Math.floor(seconds / 60)} min ago`;
  if (seconds < 86400) return `${Math.floor(seconds / 3600)} hr ago`;
  return `${Math.floor(seconds / 86400)}d ago`;
}

function formatUSDShort(usd: number): string {
  if (usd >= 1e9) return `$${(usd / 1e9).toFixed(1)}B`;
  if (usd >= 1e6) return `$${(usd / 1e6).toFixed(1)}M`;
  if (usd >= 1e3) return `$${(usd / 1e3).toFixed(0)}K`;
  return `$${usd.toFixed(0)}`;
}

// ── Sentiment API types ───────────────────────────────────────────────────────

interface FundingEntry {
  symbol: string;
  rate: number;
  annualized: number;
  signal: string;
  nextFundingTime?: number;
}

interface OpenInterestData {
  symbol: string;
  openInterest: string;
  time: number;
}

interface FearGreedHistory {
  value: number;
  classification: string;
  timestamp: string;
}

interface SentimentFearGreed extends FearGreedIndex {
  history?: FearGreedHistory[];
}

interface SentimentData {
  fearGreed: SentimentFearGreed | null;
  funding: FundingEntry | null;
  fundingRates: {
    BTC: FundingEntry | null;
    ETH: FundingEntry | null;
    SOL: FundingEntry | null;
  };
  openInterest: OpenInterestData | null;
  timestamp: string;
}

// ── Hooks ─────────────────────────────────────────────────────────────────────

function useCryptoSentiment() {
  const [data, setData] = useState<SentimentData | null>(null);
  const [loading, setLoading] = useState(true);

  useEffect(() => {
    const load = async () => {
      try {
        const res = await fetch('/api/crypto/sentiment');
        const json: SentimentData = await res.json();
        setData(json);
      } catch {
        // leave previous data in place on transient errors
      } finally {
        setLoading(false);
      }
    };

    load();
    const t = setInterval(load, 60_000);
    return () => clearInterval(t);
  }, []);

  return { data, loading };
}

interface TickerData {
  symbol: string;
  price: number;
  change: number;
  change_pct: number;
  high: number;
  low: number;
  volume: number;
}

function useCryptoTicker(symbols: string[]) {
  const [tickers, setTickers] = useState<Record<string, TickerData>>({});
  useEffect(() => {
    const load = async () => {
      try {
        const res = await fetch(`/api/crypto/ticker?symbols=${symbols.join(',')}`);
        const json = await res.json();
        const map: Record<string, TickerData> = {};
        for (const t of json.tickers ?? []) map[t.symbol] = t;
        setTickers(map);
      } catch {}
    };
    load();
    const t = setInterval(load, 30_000);
    return () => clearInterval(t);
  }, [symbols.join(',')]);
  return tickers;
}

interface WhaleTransaction {
  id: number;
  symbol: string;
  amount_display: string;
  amount_usd: number;
  from: string;
  to: string;
  age_seconds: number;
  blockchain: string;
}

function useWhaleAlerts() {
  const [transactions, setTransactions] = useState<WhaleTransaction[]>([]);
  const [loading, setLoading] = useState(true);
  useEffect(() => {
    const load = async () => {
      try {
        const res = await fetch('/api/crypto/whales');
        const json = await res.json();
        setTransactions(json.transactions ?? []);
      } catch {} finally {
        setLoading(false);
      }
    };
    load();
    const t = setInterval(load, 60_000);
    return () => clearInterval(t);
  }, []);
  return { transactions, loading };
}

interface LiqZone { price: number; side: string; size: string; pct: number; }

function useLiquidationZones(symbol: string) {
  const [zones, setZones] = useState<LiqZone[]>([]);
  const [source, setSource] = useState<string>('');
  useEffect(() => {
    const load = async () => {
      try {
        const res = await fetch(`/api/crypto/liquidations?symbol=${symbol}`);
        const json = await res.json();
        setZones(json.zones ?? []);
        setSource(json.source ?? '');
      } catch {}
    };
    load();
    const t = setInterval(load, 120_000);
    return () => clearInterval(t);
  }, [symbol]);
  return { zones, source };
}

// ── Skeleton helpers ──────────────────────────────────────────────────────────

function PulseSkeleton({ className }: { className?: string }) {
  return (
    <div
      className={cn(
        'animate-pulse rounded bg-white/5',
        className,
      )}
    />
  );
}

// ── Sub-components ────────────────────────────────────────────────────────────

function FearGreedGauge({
  data,
  loading,
}: {
  data: SentimentFearGreed | null;
  loading: boolean;
}) {
  const colors: Record<string, string> = {
    'Extreme Fear': '#ff4444',
    Fear: '#ff8844',
    Neutral: '#ffaa00',
    Greed: '#88cc00',
    'Extreme Greed': '#00ff88',
  };

  if (loading || !data) {
    return (
      <div className="nexus-card p-4">
        <h3 className="font-medium text-sm text-white mb-4">
          Fear &amp; Greed Index
        </h3>
        <div className="flex flex-col items-center gap-3">
          <PulseSkeleton className="w-36 h-20" />
          <PulseSkeleton className="w-16 h-8" />
          <PulseSkeleton className="w-24 h-4" />
          <PulseSkeleton className="w-32 h-3" />
        </div>
      </div>
    );
  }

  const color = colors[data.classification] ?? '#ffaa00';
  const rotation = (data.value / 100) * 180 - 90;

  return (
    <div className="nexus-card p-4">
      <h3 className="font-medium text-sm text-white mb-4">
        Fear &amp; Greed Index
      </h3>
      <div className="flex flex-col items-center">
        <div className="relative w-36 h-20 overflow-hidden">
          <svg viewBox="0 0 140 80" className="w-full h-full">
            {/* Gauge arc background segments */}
            <path
              d="M 10 70 A 60 60 0 0 1 130 70"
              fill="none"
              stroke="#ff4444"
              strokeWidth="8"
              opacity="0.3"
              strokeDasharray="37 188"
            />
            <path
              d="M 10 70 A 60 60 0 0 1 130 70"
              fill="none"
              stroke="#ff8844"
              strokeWidth="8"
              opacity="0.3"
              strokeDasharray="38 188"
              strokeDashoffset="-37"
            />
            <path
              d="M 10 70 A 60 60 0 0 1 130 70"
              fill="none"
              stroke="#ffaa00"
              strokeWidth="8"
              opacity="0.3"
              strokeDasharray="38 188"
              strokeDashoffset="-75"
            />
            <path
              d="M 10 70 A 60 60 0 0 1 130 70"
              fill="none"
              stroke="#88cc00"
              strokeWidth="8"
              opacity="0.3"
              strokeDasharray="37 188"
              strokeDashoffset="-113"
            />
            <path
              d="M 10 70 A 60 60 0 0 1 130 70"
              fill="none"
              stroke="#00ff88"
              strokeWidth="8"
              opacity="0.3"
              strokeDasharray="38 188"
              strokeDashoffset="-150"
            />
            {/* Needle */}
            <line
              x1="70"
              y1="70"
              x2="70"
              y2="18"
              stroke={color}
              strokeWidth="2"
              strokeLinecap="round"
              transform={`rotate(${rotation} 70 70)`}
            />
            <circle cx="70" cy="70" r="4" fill={color} />
          </svg>
        </div>
        <div
          className="text-3xl font-mono font-bold mt-1"
          style={{ color }}
        >
          {data.value}
        </div>
        <div className="text-sm font-medium mt-0.5" style={{ color }}>
          {data.classification}
        </div>
        <div className="flex gap-4 mt-3 text-xs text-muted">
          <span>
            Weekly avg:{' '}
            <span className="text-white">{data.weekly_average}</span>
          </span>
          <span>
            Monthly:{' '}
            <span className="text-white">{data.monthly_average}</span>
          </span>
        </div>
      </div>
    </div>
  );
}

function FundingRatePanel({
  rates,
  loading,
}: {
  rates: SentimentData['fundingRates'] | null;
  loading: boolean;
}) {
  const symbols: Array<{ key: 'BTC' | 'ETH' | 'SOL'; label: string }> = [
    { key: 'BTC', label: 'BTC' },
    { key: 'ETH', label: 'ETH' },
    { key: 'SOL', label: 'SOL' },
  ];

  return (
    <div className="nexus-card p-4">
      <div className="flex items-center justify-between mb-4">
        <h3 className="font-medium text-sm text-white">Funding Rates</h3>
        <Droplets size={14} className="text-nexus-blue" />
      </div>

      {loading || !rates ? (
        <div className="space-y-3">
          {[0, 1, 2].map((i) => (
            <div key={i} className="flex justify-between items-center">
              <PulseSkeleton className="w-8 h-3" />
              <PulseSkeleton className="w-20 h-3" />
            </div>
          ))}
          <div className="pt-2 border-t border-border">
            <PulseSkeleton className="w-full h-3" />
          </div>
        </div>
      ) : (
        <div className="space-y-0">
          {/* Table header */}
          <div className="grid grid-cols-3 text-xs text-muted mb-2">
            <span>Symbol</span>
            <span className="text-right">8h Rate</span>
            <span className="text-right">Annualized</span>
          </div>

          {symbols.map(({ key, label }) => {
            const entry = rates[key];
            if (!entry) {
              return (
                <div
                  key={key}
                  className="grid grid-cols-3 py-1.5 text-xs border-t border-border/50"
                >
                  <span className="font-medium text-white">{label}</span>
                  <span className="text-right text-muted">—</span>
                  <span className="text-right text-muted">—</span>
                </div>
              );
            }
            const ratePct = entry.rate * 100;
            const isPositive = entry.rate >= 0;
            const rateColor = isPositive ? 'text-nexus-red' : 'text-nexus-green';
            return (
              <div
                key={key}
                className="grid grid-cols-3 py-1.5 text-xs border-t border-border/50"
              >
                <span className="font-medium text-white">{label}</span>
                <span className={cn('text-right font-mono', rateColor)}>
                  {isPositive ? '+' : ''}
                  {ratePct.toFixed(4)}%
                </span>
                <span className={cn('text-right font-mono', rateColor)}>
                  {isPositive ? '+' : ''}
                  {entry.annualized.toFixed(1)}%
                </span>
              </div>
            );
          })}

          {/* BTC signal */}
          {rates.BTC && (
            <div className="pt-2 border-t border-border mt-1">
              <div className="flex justify-between items-center">
                <span className="text-xs text-muted">BTC Signal</span>
                <span
                  className={cn(
                    'text-xs font-bold',
                    rates.BTC.signal === 'HIGH_LONG_BIAS'
                      ? 'text-nexus-red'
                      : rates.BTC.signal === 'HIGH_SHORT_BIAS'
                      ? 'text-nexus-green'
                      : 'text-nexus-yellow',
                  )}
                >
                  {rates.BTC.signal.replace(/_/g, ' ')}
                </span>
              </div>
              <p className="text-xs text-muted mt-1 leading-tight">
                {rates.BTC.rate >= 0
                  ? 'Positive: longs pay shorts (crowded long)'
                  : 'Negative: shorts pay longs (squeeze risk)'}
              </p>
            </div>
          )}
        </div>
      )}
    </div>
  );
}

function OpenInterestPanel({
  oi,
  loading,
}: {
  oi: OpenInterestData | null;
  loading: boolean;
}) {
  // Build a minimal sparkline from the single data point we have.
  // We keep the same AreaChart layout but show only the live value with
  // a placeholder historical series so the chart renders consistently.
  const oiValue = oi ? parseFloat(oi.openInterest) / 1e9 : null;

  // Placeholder 4-point series so recharts doesn't render empty
  const chartData =
    oiValue !== null
      ? [
          { time: '', oi: oiValue * 0.97 },
          { time: '', oi: oiValue * 0.99 },
          { time: '', oi: oiValue * 0.995 },
          { time: 'Now', oi: oiValue },
        ]
      : [];

  return (
    <div className="nexus-card p-4">
      <div className="flex items-center justify-between mb-4">
        <h3 className="font-medium text-sm text-white">Open Interest</h3>
        <Activity size={14} className="text-nexus-purple" />
      </div>

      {loading ? (
        <div className="space-y-2">
          <PulseSkeleton className="w-full h-28" />
          <div className="flex justify-between">
            <PulseSkeleton className="w-24 h-3" />
            <PulseSkeleton className="w-12 h-3" />
          </div>
        </div>
      ) : oiValue !== null ? (
        <>
          <ResponsiveContainer width="100%" height={120}>
            <AreaChart
              data={chartData}
              margin={{ top: 4, right: 0, left: -20, bottom: 0 }}
            >
              <XAxis
                dataKey="time"
                tick={{ fontSize: 10, fill: '#6b7280' }}
                tickLine={false}
              />
              <YAxis
                tick={{ fontSize: 10, fill: '#6b7280' }}
                tickLine={false}
                domain={['auto', 'auto']}
              />
              <Tooltip
                contentStyle={{
                  background: '#1a1a28',
                  border: '1px solid #2a2a3e',
                  borderRadius: '6px',
                  fontSize: '11px',
                }}
                formatter={(v: number) => [`${v.toFixed(2)}B`, 'OI']}
              />
              <Area
                type="monotone"
                dataKey="oi"
                stroke="#8844ff"
                fill="rgba(136, 68, 255, 0.15)"
                strokeWidth={1.5}
              />
            </AreaChart>
          </ResponsiveContainer>
          <div className="mt-2 flex justify-between text-xs text-muted">
            <span>
              Live:{' '}
              <span className="text-nexus-purple font-mono">
                {oiValue.toFixed(2)}B
              </span>
            </span>
            <span className="text-xs text-muted">BTCUSDT</span>
          </div>
        </>
      ) : (
        <p className="text-xs text-muted text-center py-8">
          Open interest unavailable
        </p>
      )}
    </div>
  );
}

function WhaleAlertPanel({ transactions, loading }: { transactions: WhaleTransaction[]; loading: boolean }) {
  return (
    <div className="nexus-card p-4 flex flex-col h-full">
      <div className="flex items-center gap-2 mb-3">
        <TrendingUp size={14} className="text-nexus-yellow" />
        <h3 className="font-medium text-sm text-white">Whale Alerts</h3>
        <span className="ml-auto text-xs text-muted">≥$500K</span>
      </div>
      {loading ? (
        <div className="space-y-2">
          {[...Array(4)].map((_, i) => <PulseSkeleton key={i} className="h-10 w-full" />)}
        </div>
      ) : transactions.length === 0 ? (
        <p className="text-xs text-muted mt-2">No large transactions in the last hour.</p>
      ) : (
        <div className="space-y-2 overflow-y-auto max-h-64">
          {transactions.slice(0, 8).map((tx) => (
            <div key={tx.id} className="flex items-start justify-between gap-2 py-1.5 border-b border-white/5 last:border-0">
              <div className="flex items-center gap-1.5 min-w-0">
                <span className="text-xs font-bold text-white shrink-0">{tx.amount_display}</span>
                <span className="text-xs text-muted truncate">{tx.from} → {tx.to}</span>
              </div>
              <div className="flex flex-col items-end shrink-0">
                <span className="text-xs text-nexus-yellow font-mono">{formatUSDShort(tx.amount_usd)}</span>
                <span className="text-[10px] text-muted">{formatAge(tx.age_seconds)}</span>
              </div>
            </div>
          ))}
        </div>
      )}
    </div>
  );
}

// ── Page ──────────────────────────────────────────────────────────────────────

export default function CryptoPage() {
  const { signalFeed, marketPrices } = useNexusStore();
  const [activePair, setActivePair] = useState('BTCUSDT');
  const [signals, setSignals] = useState<Signal[]>([]);
  const { data: sentimentData, loading: sentimentLoading } =
    useCryptoSentiment();

  const tickers = useCryptoTicker(['BTCUSDT', 'ETHUSDT', 'SOLUSDT', 'BNBUSDT', 'XRPUSDT', 'DOGEUSDT']);
  const { transactions: whaleTransactions, loading: whaleLoading } = useWhaleAlerts();
  const { zones: liqZones, source: liqSource } = useLiquidationZones(activePair.replace('USDT', ''));
  const activeTicker = tickers[activePair];

  useEffect(() => {
    const load = async () => {
      const sigs = await getRecentSignals('crypto', 20);
      setSignals(sigs);
    };
    load();
  }, []);

  const cryptoSignals = signalFeed.filter((s) => s.market === 'crypto');
  const activePairData = CRYPTO_PAIRS.find((p) => p.symbol === activePair)!;

  const signalForPair = signals.find(
    (s) =>
      s.symbol === activePair ||
      s.symbol === activePair.replace('USDT', '/USDT'),
  );
  const currentPrice =
    marketPrices[activePair] ||
    marketPrices[activePair.replace('USDT', '/USDT')] ||
    (signalForPair?.entry ?? 0) ||
    activePairData.basePrice;

  const pairSignal = cryptoSignals.find((s) => s.symbol === activePair);

  return (
    <div className="space-y-4">
      {/* Header tabs */}
      <Tabs.Root value={activePair} onValueChange={setActivePair}>
        <div className="flex items-center justify-between">
          <Tabs.List className="flex gap-1 bg-card border border-border rounded-lg p-1">
            {CRYPTO_PAIRS.map((pair) => (
              <Tabs.Trigger
                key={pair.symbol}
                value={pair.symbol}
                className={cn(
                  'px-4 py-2 rounded-md text-sm font-medium transition-all',
                  'text-muted hover:text-white',
                  'data-[state=active]:bg-nexus-blue/10 data-[state=active]:text-nexus-blue data-[state=active]:border data-[state=active]:border-nexus-blue/20',
                )}
              >
                {pair.label}
              </Tabs.Trigger>
            ))}
          </Tabs.List>

          {/* Current price + signal badge */}
          <div className="flex items-center gap-3">
            <div>
              <span className="text-2xl font-mono font-bold text-white">
                {formatCurrency(currentPrice)}
              </span>
              {activeTicker ? (
                <span className={`text-sm ml-2 ${activeTicker.change_pct >= 0 ? 'text-nexus-green' : 'text-nexus-red'}`}>
                  {activeTicker.change_pct >= 0 ? '+' : ''}{activeTicker.change_pct.toFixed(2)}%
                </span>
              ) : null}
            </div>
            {pairSignal && (
              <span
                className={cn('badge', getDirectionBg(pairSignal.direction))}
              >
                {pairSignal.direction}{' '}
                {(pairSignal.confidence * 100).toFixed(0)}%
              </span>
            )}
          </div>
        </div>

        {/* Chart area for each pair */}
        {CRYPTO_PAIRS.map((pair) => (
          <Tabs.Content key={pair.symbol} value={pair.symbol} className="mt-4">
            <div className="nexus-card overflow-hidden" style={{ height: 420 }}>
              <CandlestickChart
                symbol={pair.symbol}
                market="crypto"
                signals={cryptoSignals.filter((s) => s.symbol === pair.symbol)}
              />
            </div>
          </Tabs.Content>
        ))}
      </Tabs.Root>

      {/* Bottom panels */}
      <div className="grid grid-cols-4 gap-4">
        {/* Funding Rate Panel — real data */}
        <FundingRatePanel
          rates={sentimentData?.fundingRates ?? null}
          loading={sentimentLoading}
        />

        {/* Fear & Greed — real data */}
        <FearGreedGauge
          data={sentimentData?.fearGreed ?? null}
          loading={sentimentLoading}
        />

        {/* Open Interest — real data */}
        <OpenInterestPanel
          oi={sentimentData?.openInterest ?? null}
          loading={sentimentLoading}
        />

        {/* Liquidation Zones */}
        <div className="nexus-card p-4">
          <div className="flex items-center justify-between mb-4">
            <h3 className="font-medium text-sm text-white">
              Liquidation Zones
              {liqSource === 'estimated' && (
                <span className="ml-1.5 text-xs text-muted font-normal">(est.)</span>
              )}
            </h3>
            <AlertTriangle size={14} className="text-nexus-yellow" />
          </div>
          <div className="space-y-2">
            {liqZones.length === 0 ? (
              <p className="text-xs text-muted text-center py-4">
                Awaiting price data…
              </p>
            ) : (
              liqZones.map((zone) => (
                <div
                  key={`${zone.side}-${zone.price}`}
                  className="flex items-center justify-between"
                >
                  <span className="text-xs font-mono text-muted">
                    {formatCurrency(zone.price)}
                  </span>
                  {zone.side === 'CURRENT' ? (
                    <span className="text-xs text-nexus-blue">
                      ◆ Current
                    </span>
                  ) : (
                    <div className="flex items-center gap-2">
                      <div className="w-16 h-1.5 bg-border rounded-full overflow-hidden">
                        <div
                          className={cn(
                            'h-full rounded-full',
                            zone.side === 'LONG'
                              ? 'bg-nexus-red/60'
                              : 'bg-nexus-green/60',
                          )}
                          style={{ width: `${zone.pct}%` }}
                        />
                      </div>
                      <span
                        className={cn(
                          'text-xs font-mono',
                          zone.side === 'LONG'
                            ? 'text-nexus-red'
                            : 'text-nexus-green',
                        )}
                      >
                        {zone.size}
                      </span>
                    </div>
                  )}
                </div>
              ))
            )}
          </div>
        </div>
      </div>

      {/* Whale alerts + Signals */}
      <div className="grid grid-cols-2 gap-4">
        <WhaleAlertPanel transactions={whaleTransactions} loading={whaleLoading} />
        <SignalFeed market="crypto" limit={8} />
      </div>

      {/* Attribution footer */}
      <p className="text-xs text-muted text-center pb-2">
        Data via{' '}
        <a
          href="https://www.binance.com/en/futures"
          target="_blank"
          rel="noopener noreferrer"
          className="hover:text-white transition-colors"
        >
          Binance Futures API
        </a>
        {' · '}
        <a
          href="https://whale-alert.io"
          target="_blank"
          rel="noopener noreferrer"
          className="hover:text-white transition-colors"
        >
          Whale Alert
        </a>
        {' · '}
        <a
          href="https://alternative.me/crypto/fear-and-greed-index/"
          target="_blank"
          rel="noopener noreferrer"
          className="hover:text-white transition-colors"
        >
          alternative.me
        </a>
      </p>
    </div>
  );
}
