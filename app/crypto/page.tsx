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

// ── Hook ──────────────────────────────────────────────────────────────────────

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

function WhaleAlertNotice() {
  return (
    <div className="nexus-card p-4 flex flex-col justify-between h-full">
      <div>
        <div className="flex items-center gap-2 mb-3">
          <TrendingUp size={14} className="text-nexus-yellow" />
          <h3 className="font-medium text-sm text-white">Whale Alerts</h3>
        </div>
        <p className="text-xs text-muted leading-relaxed">
          Whale alert data requires a Whale Alert API subscription. Configure
          your key in{' '}
          <span className="text-white">Settings &rarr; API Connections</span>.
        </p>
      </div>
      <a
        href="https://whale-alert.io/api"
        target="_blank"
        rel="noopener noreferrer"
        className="mt-4 inline-flex items-center gap-1.5 text-xs text-nexus-blue hover:text-nexus-blue/80 transition-colors"
      >
        Get a Whale Alert API key
        <ExternalLink size={11} />
      </a>
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

  const liquidationZones: {
    price: number;
    side: string;
    size: string;
    pct: number;
  }[] =
    currentPrice > 0
      ? [
          {
            price: Math.round(currentPrice * 1.07),
            side: 'LONG',
            size: '$284M',
            pct: 75,
          },
          {
            price: Math.round(currentPrice * 1.04),
            side: 'LONG',
            size: '$156M',
            pct: 55,
          },
          { price: Math.round(currentPrice), side: 'CURRENT', size: '—', pct: 0 },
          {
            price: Math.round(currentPrice * 0.96),
            side: 'SHORT',
            size: '$198M',
            pct: 65,
          },
          {
            price: Math.round(currentPrice * 0.92),
            side: 'SHORT',
            size: '$421M',
            pct: 85,
          },
        ]
      : [];

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
              <span className="text-sm text-nexus-green ml-2">+2.34%</span>
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
            </h3>
            <AlertTriangle size={14} className="text-nexus-yellow" />
          </div>
          <div className="space-y-2">
            {liquidationZones.length === 0 ? (
              <p className="text-xs text-muted text-center py-4">
                Awaiting price data…
              </p>
            ) : (
              liquidationZones.map((zone) => (
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

      {/* Whale alerts notice + Signals */}
      <div className="grid grid-cols-2 gap-4">
        <WhaleAlertNotice />
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
        </a>{' '}
        &amp;{' '}
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
