'use client';

import { useEffect, useState } from 'react';
import { TrendingUp, TrendingDown, Calendar } from 'lucide-react';
import { BarChart, Bar, XAxis, YAxis, ResponsiveContainer, Tooltip, Cell } from 'recharts';
import { SignalFeed } from '@/components/SignalFeed';
import { TradeTable } from '@/components/TradeTable';
import { useNexusStore } from '@/lib/store';
import { getRecentSignals, getActiveTrades } from '@/lib/supabase';
import { cn, formatCurrency, formatPercent, formatNumber } from '@/lib/utils';
import type { Signal, Trade } from '@/lib/types';

const US_INDICES = [
  { symbol: 'SPX', label: 'S&P 500', price: 5842.47, change: 18.32, changePct: 0.31 },
  { symbol: 'NDX', label: 'NASDAQ 100', price: 20241.55, change: -42.18, changePct: -0.21 },
  { symbol: 'DJI', label: 'Dow Jones', price: 43478.22, change: 125.75, changePct: 0.29 },
  { symbol: 'RUT', label: 'Russell 2000', price: 2248.88, change: -8.42, changePct: -0.37 },
];

// Sector data
const SECTORS = [
  { ticker: 'XLK', name: 'Technology', change: 1.24, weight: 31.2 },
  { ticker: 'XLF', name: 'Financials', change: 0.48, weight: 12.8 },
  { ticker: 'XLV', name: 'Health Care', change: -0.32, weight: 12.1 },
  { ticker: 'XLC', name: 'Comm. Svcs', change: 0.87, weight: 9.8 },
  { ticker: 'XLY', name: 'Consumer Disc.', change: -0.55, weight: 10.4 },
  { ticker: 'XLI', name: 'Industrials', change: 0.21, weight: 8.6 },
  { ticker: 'XLP', name: 'Consumer Stpl.', change: -0.18, weight: 5.7 },
  { ticker: 'XLE', name: 'Energy', change: -1.12, weight: 3.8 },
  { ticker: 'XLB', name: 'Materials', change: 0.43, weight: 2.4 },
  { ticker: 'XLU', name: 'Utilities', change: 0.76, weight: 2.3 },
  { ticker: 'XLRE', name: 'Real Estate', change: 1.14, weight: 2.2 },
];

const EARNINGS_CALENDAR = [
  { ticker: 'NVDA', name: 'NVIDIA', date: new Date(Date.now() + 86400000).toISOString(), estimate: '0.74', prev: '0.49', mcap: '3.2T', importance: 'CRITICAL' },
  { ticker: 'AAPL', name: 'Apple', date: new Date(Date.now() + 2 * 86400000).toISOString(), estimate: '1.61', prev: '1.53', mcap: '3.5T', importance: 'HIGH' },
  { ticker: 'MSFT', name: 'Microsoft', date: new Date(Date.now() + 3 * 86400000).toISOString(), estimate: '3.11', prev: '2.99', mcap: '3.1T', importance: 'HIGH' },
  { ticker: 'GOOGL', name: 'Alphabet', date: new Date(Date.now() + 4 * 86400000).toISOString(), estimate: '1.85', prev: '1.55', mcap: '2.1T', importance: 'HIGH' },
  { ticker: 'META', name: 'Meta', date: new Date(Date.now() + 5 * 86400000).toISOString(), estimate: '5.25', prev: '4.71', mcap: '1.4T', importance: 'HIGH' },
];

const ORB_SIGNALS = [
  { symbol: 'TSLA', direction: 'LONG', breakout_price: 248.40, current: 252.18, pct: 1.52, time: '09:30 ET' },
  { symbol: 'AMD', direction: 'LONG', breakout_price: 148.20, current: 150.75, pct: 1.72, time: '09:45 ET' },
  { symbol: 'AMZN', direction: 'SHORT', breakout_price: 188.50, current: 186.20, pct: -1.22, time: '09:30 ET' },
  { symbol: 'NVDA', direction: 'LONG', breakout_price: 495.00, current: 507.40, pct: 2.51, time: '10:00 ET' },
];

function IndexCard({ index }: { index: typeof US_INDICES[0] }) {
  const isPositive = index.changePct >= 0;
  return (
    <div className="nexus-card nexus-card-hover p-4">
      <div className="flex items-center justify-between mb-2">
        <span className="text-xs text-muted uppercase tracking-wider">{index.label}</span>
        {isPositive ? <TrendingUp size={14} className="text-nexus-green" /> : <TrendingDown size={14} className="text-nexus-red" />}
      </div>
      <div className="font-mono text-2xl font-bold text-white">
        {formatNumber(index.price, 2)}
      </div>
      <div className={cn('text-sm font-medium mt-1', isPositive ? 'text-nexus-green' : 'text-nexus-red')}>
        {isPositive ? '+' : ''}{formatNumber(index.change, 2)} ({formatPercent(index.changePct)})
      </div>
    </div>
  );
}

function SectorHeatmap({ sectors }: { sectors: typeof SECTORS }) {
  const maxAbs = Math.max(...sectors.map((s) => Math.abs(s.change)));

  const getColor = (change: number) => {
    const intensity = Math.abs(change) / maxAbs;
    if (change > 0) return `rgba(0, 255, 136, ${0.15 + intensity * 0.65})`;
    return `rgba(255, 68, 68, ${0.15 + intensity * 0.65})`;
  };

  const sortedSectors = [...sectors].sort((a, b) => b.change - a.change);

  return (
    <div className="nexus-card p-4">
      <h3 className="font-medium text-sm text-white mb-4">Sector Performance Heatmap</h3>
      <div className="grid grid-cols-3 gap-2">
        {sortedSectors.map((sector) => (
          <div
            key={sector.ticker}
            className="rounded-lg p-3 transition-all hover:opacity-80 cursor-pointer"
            style={{ backgroundColor: getColor(sector.change) }}
          >
            <div className="font-bold text-sm text-white">{sector.ticker}</div>
            <div className="text-xs text-gray-300 truncate">{sector.name}</div>
            <div
              className={cn(
                'text-sm font-mono font-bold mt-1',
                sector.change >= 0 ? 'text-nexus-green' : 'text-nexus-red'
              )}
            >
              {sector.change >= 0 ? '+' : ''}{sector.change.toFixed(2)}%
            </div>
          </div>
        ))}
      </div>

      {/* Bar chart */}
      <div className="mt-4">
        <ResponsiveContainer width="100%" height={120}>
          <BarChart
            data={sortedSectors}
            layout="vertical"
            margin={{ top: 0, right: 4, left: 55, bottom: 0 }}
          >
            <XAxis type="number" tick={{ fontSize: 10, fill: '#6b7280' }} tickLine={false} tickFormatter={(v) => `${v}%`} />
            <YAxis type="category" dataKey="ticker" tick={{ fontSize: 10, fill: '#9ca3af' }} tickLine={false} width={40} />
            <Tooltip
              contentStyle={{ background: '#1a1a28', border: '1px solid #2a2a3e', borderRadius: '6px', fontSize: '11px' }}
              formatter={(v: number) => [`${v.toFixed(2)}%`, 'Change']}
            />
            <Bar dataKey="change" radius={[0, 2, 2, 0]}>
              {sortedSectors.map((entry, i) => (
                <Cell key={i} fill={entry.change >= 0 ? '#00ff88' : '#ff4444'} opacity={0.7} />
              ))}
            </Bar>
          </BarChart>
        </ResponsiveContainer>
      </div>
    </div>
  );
}

function EarningsCalendar({ events }: { events: typeof EARNINGS_CALENDAR }) {
  const getCountdown = (datetime: string) => {
    const diff = new Date(datetime).getTime() - Date.now();
    if (diff < 0) return 'Released';
    const d = Math.floor(diff / 86400000);
    const h = Math.floor((diff % 86400000) / 3600000);
    if (d > 0) return `${d}d ${h}h`;
    return `${h}h`;
  };

  const importanceColor: Record<string, string> = {
    CRITICAL: 'bg-nexus-red/10 text-nexus-red border-nexus-red/20',
    HIGH: 'bg-nexus-yellow/10 text-nexus-yellow border-nexus-yellow/20',
    MEDIUM: 'bg-nexus-blue/10 text-nexus-blue border-nexus-blue/20',
  };

  return (
    <div className="nexus-card p-4">
      <div className="flex items-center justify-between mb-4">
        <h3 className="font-medium text-sm text-white">Earnings Calendar</h3>
        <Calendar size={14} className="text-muted" />
      </div>
      <div className="space-y-3">
        {events.map((e) => (
          <div key={e.ticker} className="flex items-center justify-between p-2 rounded-lg bg-white/2 border border-border">
            <div className="flex items-center gap-3">
              <div className="w-10 h-10 rounded-lg bg-nexus-blue/10 flex items-center justify-center">
                <span className="text-xs font-bold text-nexus-blue">{e.ticker.slice(0, 3)}</span>
              </div>
              <div>
                <div className="text-sm font-medium text-white">{e.ticker}</div>
                <div className="text-xs text-muted">{e.name} · {e.mcap}</div>
              </div>
            </div>
            <div className="text-right">
              <div className="text-xs text-muted">EPS Est: <span className="text-white">${e.estimate}</span></div>
              <div className="text-xs text-muted">Prev: <span className="text-muted">${e.prev}</span></div>
              <span className={cn('badge text-xs mt-1 border', importanceColor[e.importance])}>
                {getCountdown(e.date)}
              </span>
            </div>
          </div>
        ))}
      </div>
    </div>
  );
}

function ORBSignals({ signals }: { signals: typeof ORB_SIGNALS }) {
  return (
    <div className="nexus-card p-4">
      <div className="flex items-center justify-between mb-4">
        <h3 className="font-medium text-sm text-white">Opening Range Breakouts</h3>
        <span className="text-xs text-muted">Today</span>
      </div>
      <div className="space-y-3">
        {signals.map((sig) => (
          <div
            key={sig.symbol}
            className={cn(
              'p-3 rounded-lg border',
              sig.direction === 'LONG'
                ? 'border-nexus-green/20 bg-nexus-green/5'
                : 'border-nexus-red/20 bg-nexus-red/5'
            )}
          >
            <div className="flex items-center justify-between">
              <div className="flex items-center gap-2">
                <span
                  className={cn(
                    'text-xs font-bold px-2 py-0.5 rounded',
                    sig.direction === 'LONG'
                      ? 'bg-nexus-green/20 text-nexus-green'
                      : 'bg-nexus-red/20 text-nexus-red'
                  )}
                >
                  {sig.direction}
                </span>
                <span className="font-bold text-white">{sig.symbol}</span>
              </div>
              <span
                className={cn(
                  'font-mono text-sm font-bold',
                  sig.pct >= 0 ? 'text-nexus-green' : 'text-nexus-red'
                )}
              >
                {sig.pct >= 0 ? '+' : ''}{sig.pct.toFixed(2)}%
              </span>
            </div>
            <div className="flex justify-between mt-2 text-xs text-muted">
              <span>
                Breakout: <span className="font-mono text-white">${formatNumber(sig.breakout_price, 2)}</span>
              </span>
              <span>
                Current: <span className="font-mono text-white">${formatNumber(sig.current, 2)}</span>
              </span>
              <span>{sig.time}</span>
            </div>
          </div>
        ))}
      </div>
    </div>
  );
}

export default function USStocksPage() {
  const { signalFeed, activeTrades } = useNexusStore();
  const [signals, setSignals] = useState<Signal[]>([]);
  const [trades, setTrades] = useState<Trade[]>([]);

  useEffect(() => {
    const load = async () => {
      const [sigs, trds] = await Promise.all([
        getRecentSignals('us_stocks', 20),
        getActiveTrades(),
      ]);
      setSignals(sigs);
      setTrades(trds.filter((t) => t.market === 'us_stocks'));
    };
    load();
  }, []);

  const usTrades = [...trades, ...activeTrades.filter((t) => t.market === 'us_stocks')];

  return (
    <div className="space-y-4">
      {/* Index cards */}
      <div className="grid grid-cols-4 gap-4">
        {US_INDICES.map((index) => (
          <IndexCard key={index.symbol} index={index} />
        ))}
      </div>

      {/* Sector heatmap + earnings */}
      <div className="grid grid-cols-3 gap-4">
        <div className="col-span-2">
          <SectorHeatmap sectors={SECTORS} />
        </div>
        <EarningsCalendar events={EARNINGS_CALENDAR} />
      </div>

      {/* ORB signals + trades + signal feed */}
      <div className="grid grid-cols-3 gap-4">
        <ORBSignals signals={ORB_SIGNALS} />
        <TradeTable trades={usTrades} title="Active US Stock Trades" />
        <SignalFeed market="us_stocks" limit={6} />
      </div>
    </div>
  );
}
