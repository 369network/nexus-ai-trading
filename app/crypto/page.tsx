'use client';

import { useEffect, useState, useCallback } from 'react';
import * as Tabs from '@radix-ui/react-tabs';
import {
  TrendingUp,
  TrendingDown,
  AlertTriangle,
  Droplets,
  Activity,
  Zap,
} from 'lucide-react';
import {
  AreaChart,
  Area,
  BarChart,
  Bar,
  XAxis,
  YAxis,
  ResponsiveContainer,
  Tooltip,
} from 'recharts';
import { CandlestickChart } from '@/components/charts/CandlestickChart';
import { SignalFeed } from '@/components/SignalFeed';
import { useNexusStore } from '@/lib/store';
import { getRecentSignals } from '@/lib/supabase';
import { cn, formatCurrency, formatNumber, formatPercent, formatTimeAgo, getDirectionBg } from '@/lib/utils';
import type { Signal, WhaleAlert, FearGreedIndex } from '@/lib/types';

const CRYPTO_PAIRS = [
  { symbol: 'BTCUSDT', label: 'BTC/USDT', basePrice: 67420 },
  { symbol: 'ETHUSDT', label: 'ETH/USDT', basePrice: 3580 },
  { symbol: 'SOLUSDT', label: 'SOL/USDT', basePrice: 178 },
];

// Mock real-time data generators
function generateFundingRate() {
  return {
    current: (Math.random() - 0.45) * 0.1,
    h8: (Math.random() - 0.45) * 0.08,
    annualized: (Math.random() - 0.45) * 40,
    signal: Math.random() > 0.6 ? 'HIGH_LONG_BIAS' : Math.random() > 0.5 ? 'NEUTRAL' : 'HIGH_SHORT_BIAS',
  };
}

function generateWhaleAlerts(): WhaleAlert[] {
  const types = ['EXCHANGE_DEPOSIT', 'EXCHANGE_WITHDRAWAL', 'TRANSFER'] as const;
  const exchanges = ['Binance', 'Coinbase', 'Kraken', 'Unknown'];
  return Array.from({ length: 10 }, (_, i) => ({
    id: `whale-${i}`,
    symbol: Math.random() > 0.5 ? 'BTC' : 'ETH',
    amount_usd: (2 + Math.random() * 98) * 1e6,
    amount_tokens: Math.random() * 1000,
    from_exchange: exchanges[Math.floor(Math.random() * exchanges.length)],
    to_exchange: exchanges[Math.floor(Math.random() * exchanges.length)],
    transaction_type: types[Math.floor(Math.random() * types.length)],
    timestamp: new Date(Date.now() - i * 600000).toISOString(),
    hash: `0x${Math.random().toString(16).slice(2, 12)}...`,
  }));
}

function generateFearGreed(): FearGreedIndex {
  const value = Math.floor(30 + Math.random() * 60);
  return {
    value,
    classification:
      value < 25 ? 'Extreme Fear'
      : value < 45 ? 'Fear'
      : value < 55 ? 'Neutral'
      : value < 75 ? 'Greed'
      : 'Extreme Greed',
    timestamp: new Date().toISOString(),
    previous_value: value - Math.floor((Math.random() - 0.5) * 10),
    previous_close: value - 2,
    weekly_average: value + Math.floor((Math.random() - 0.5) * 5),
    monthly_average: value - Math.floor(Math.random() * 8),
  };
}

function generateOpenInterest() {
  return Array.from({ length: 24 }, (_, i) => ({
    time: new Date(Date.now() - (23 - i) * 3600000).toISOString().slice(11, 16),
    oi: 8 + Math.random() * 4,
    change: (Math.random() - 0.5) * 0.5,
  }));
}

function FearGreedGauge({ data }: { data: FearGreedIndex }) {
  const colors: Record<string, string> = {
    'Extreme Fear': '#ff4444',
    Fear: '#ff8844',
    Neutral: '#ffaa00',
    Greed: '#88cc00',
    'Extreme Greed': '#00ff88',
  };
  const color = colors[data.classification];
  const rotation = (data.value / 100) * 180 - 90;

  return (
    <div className="nexus-card p-4">
      <h3 className="font-medium text-sm text-white mb-4">Fear &amp; Greed Index</h3>
      <div className="flex flex-col items-center">
        <div className="relative w-36 h-20 overflow-hidden">
          {/* Gauge arc background */}
          <svg viewBox="0 0 140 80" className="w-full h-full">
            {/* Background arc segments */}
            <path d="M 10 70 A 60 60 0 0 1 130 70" fill="none" stroke="#ff4444" strokeWidth="8" opacity="0.3" strokeDasharray="37 188" />
            <path d="M 10 70 A 60 60 0 0 1 130 70" fill="none" stroke="#ff8844" strokeWidth="8" opacity="0.3" strokeDasharray="38 188" strokeDashoffset="-37" />
            <path d="M 10 70 A 60 60 0 0 1 130 70" fill="none" stroke="#ffaa00" strokeWidth="8" opacity="0.3" strokeDasharray="38 188" strokeDashoffset="-75" />
            <path d="M 10 70 A 60 60 0 0 1 130 70" fill="none" stroke="#88cc00" strokeWidth="8" opacity="0.3" strokeDasharray="37 188" strokeDashoffset="-113" />
            <path d="M 10 70 A 60 60 0 0 1 130 70" fill="none" stroke="#00ff88" strokeWidth="8" opacity="0.3" strokeDasharray="38 188" strokeDashoffset="-150" />
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
        <div className="text-3xl font-mono font-bold mt-1" style={{ color }}>
          {data.value}
        </div>
        <div className="text-sm font-medium mt-0.5" style={{ color }}>
          {data.classification}
        </div>
        <div className="flex gap-4 mt-3 text-xs text-muted">
          <span>Weekly avg: <span className="text-white">{data.weekly_average}</span></span>
          <span>Monthly: <span className="text-white">{data.monthly_average}</span></span>
        </div>
      </div>
    </div>
  );
}

function WhaleAlertFeed({ alerts }: { alerts: WhaleAlert[] }) {
  return (
    <div className="nexus-card p-4">
      <div className="flex items-center justify-between mb-4">
        <h3 className="font-medium text-sm text-white">Whale Alerts</h3>
        <Zap size={14} className="text-nexus-yellow" />
      </div>
      <div className="space-y-2 max-h-64 overflow-y-auto">
        {alerts.map((alert) => (
          <div
            key={alert.id}
            className="flex items-center justify-between p-2 rounded-lg bg-white/2 border border-border hover:border-border-bright transition-colors"
          >
            <div className="flex items-center gap-2">
              <span
                className={cn(
                  'w-1.5 h-1.5 rounded-full',
                  alert.transaction_type === 'EXCHANGE_DEPOSIT'
                    ? 'bg-nexus-red'
                    : alert.transaction_type === 'EXCHANGE_WITHDRAWAL'
                    ? 'bg-nexus-green'
                    : 'bg-nexus-yellow'
                )}
              />
              <div>
                <div className="text-xs font-medium text-white">
                  {(alert.amount_usd / 1e6).toFixed(1)}M {alert.symbol}
                </div>
                <div className="text-xs text-muted">
                  {alert.from_exchange} → {alert.to_exchange}
                </div>
              </div>
            </div>
            <span className="text-xs text-muted">{formatTimeAgo(alert.timestamp)}</span>
          </div>
        ))}
      </div>
    </div>
  );
}

export default function CryptoPage() {
  const { signalFeed, marketPrices } = useNexusStore();
  const [activePair, setActivePair] = useState('BTCUSDT');
  const [signals, setSignals] = useState<Signal[]>([]);
  const [fundingRates, setFundingRates] = useState(generateFundingRate());
  const [whaleAlerts] = useState(generateWhaleAlerts());
  const [fearGreed, setFearGreed] = useState(generateFearGreed());
  const [openInterest] = useState(generateOpenInterest());

  useEffect(() => {
    const load = async () => {
      const sigs = await getRecentSignals('crypto', 20);
      setSignals(sigs);
    };
    load();

    // Simulate real-time funding rate updates
    const interval = setInterval(() => {
      setFundingRates(generateFundingRate());
      setFearGreed(generateFearGreed());
    }, 15000);

    return () => clearInterval(interval);
  }, []);

  const cryptoSignals = signalFeed.filter((s) => s.market === 'crypto');
  const activePairData = CRYPTO_PAIRS.find((p) => p.symbol === activePair)!;
  const currentPrice = marketPrices[activePair] ?? activePairData.basePrice;
  const pairSignal = cryptoSignals.find((s) => s.symbol === activePair);

  const fundingSignalColor =
    fundingRates.signal === 'HIGH_LONG_BIAS'
      ? 'text-nexus-red'
      : fundingRates.signal === 'HIGH_SHORT_BIAS'
      ? 'text-nexus-green'
      : 'text-nexus-yellow';

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
                  'data-[state=active]:bg-nexus-blue/10 data-[state=active]:text-nexus-blue data-[state=active]:border data-[state=active]:border-nexus-blue/20'
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
              <span className={cn('badge', getDirectionBg(pairSignal.direction))}>
                {pairSignal.direction} {(pairSignal.confidence * 100).toFixed(0)}%
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
        {/* Funding Rate Panel */}
        <div className="nexus-card p-4">
          <div className="flex items-center justify-between mb-4">
            <h3 className="font-medium text-sm text-white">Funding Rate</h3>
            <Droplets size={14} className="text-nexus-blue" />
          </div>
          <div className="space-y-3">
            <div className="flex justify-between items-center">
              <span className="text-xs text-muted">Current Rate</span>
              <span
                className={cn(
                  'text-sm font-mono font-bold',
                  fundingRates.current >= 0 ? 'text-nexus-red' : 'text-nexus-green'
                )}
              >
                {fundingRates.current >= 0 ? '+' : ''}{fundingRates.current.toFixed(4)}%
              </span>
            </div>
            <div className="flex justify-between items-center">
              <span className="text-xs text-muted">8h Rate</span>
              <span className="text-sm font-mono text-white">
                {fundingRates.h8 >= 0 ? '+' : ''}{fundingRates.h8.toFixed(4)}%
              </span>
            </div>
            <div className="flex justify-between items-center">
              <span className="text-xs text-muted">Annualized</span>
              <span className={cn('text-sm font-mono', fundingRates.annualized >= 0 ? 'text-nexus-red' : 'text-nexus-green')}>
                {fundingRates.annualized >= 0 ? '+' : ''}{fundingRates.annualized.toFixed(1)}%
              </span>
            </div>
            <div className="pt-2 border-t border-border">
              <div className="flex justify-between items-center">
                <span className="text-xs text-muted">Signal</span>
                <span className={cn('text-xs font-bold', fundingSignalColor)}>
                  {fundingRates.signal.replace(/_/g, ' ')}
                </span>
              </div>
            </div>
          </div>
        </div>

        {/* Fear & Greed */}
        <FearGreedGauge data={fearGreed} />

        {/* Open Interest */}
        <div className="nexus-card p-4">
          <div className="flex items-center justify-between mb-4">
            <h3 className="font-medium text-sm text-white">Open Interest</h3>
            <Activity size={14} className="text-nexus-purple" />
          </div>
          <ResponsiveContainer width="100%" height={120}>
            <AreaChart data={openInterest} margin={{ top: 4, right: 0, left: -20, bottom: 0 }}>
              <XAxis dataKey="time" tick={{ fontSize: 10, fill: '#6b7280' }} tickLine={false} />
              <YAxis tick={{ fontSize: 10, fill: '#6b7280' }} tickLine={false} />
              <Tooltip
                contentStyle={{ background: '#1a1a28', border: '1px solid #2a2a3e', borderRadius: '6px', fontSize: '11px' }}
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
            <span>Current: <span className="text-nexus-purple font-mono">{openInterest[openInterest.length - 1]?.oi.toFixed(2)}B</span></span>
            <span className="text-nexus-green">▲ 2.3%</span>
          </div>
        </div>

        {/* Liquidations */}
        <div className="nexus-card p-4">
          <div className="flex items-center justify-between mb-4">
            <h3 className="font-medium text-sm text-white">Liquidation Zones</h3>
            <AlertTriangle size={14} className="text-nexus-yellow" />
          </div>
          <div className="space-y-2">
            {[
              { price: 72000, side: 'LONG', size: '$284M', pct: 75 },
              { price: 70000, side: 'LONG', size: '$156M', pct: 55 },
              { price: 67000, side: 'CURRENT', size: '—', pct: 0 },
              { price: 65000, side: 'SHORT', size: '$198M', pct: 65 },
              { price: 62000, side: 'SHORT', size: '$421M', pct: 85 },
            ].map((zone) => (
              <div key={zone.price} className="flex items-center justify-between">
                <span className="text-xs font-mono text-muted">{formatCurrency(zone.price)}</span>
                {zone.side === 'CURRENT' ? (
                  <span className="text-xs text-nexus-blue">◆ Current</span>
                ) : (
                  <div className="flex items-center gap-2">
                    <div className="w-16 h-1.5 bg-border rounded-full overflow-hidden">
                      <div
                        className={cn('h-full rounded-full', zone.side === 'LONG' ? 'bg-nexus-red/60' : 'bg-nexus-green/60')}
                        style={{ width: `${zone.pct}%` }}
                      />
                    </div>
                    <span className={cn('text-xs font-mono', zone.side === 'LONG' ? 'text-nexus-red' : 'text-nexus-green')}>
                      {zone.size}
                    </span>
                  </div>
                )}
              </div>
            ))}
          </div>
        </div>
      </div>

      {/* Whale alerts + Signals */}
      <div className="grid grid-cols-2 gap-4">
        <WhaleAlertFeed alerts={whaleAlerts} />
        <SignalFeed market="crypto" limit={8} />
      </div>
    </div>
  );
}
