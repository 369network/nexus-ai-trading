'use client';

import { useEffect, useState } from 'react';
import { Clock, Globe, TrendingUp, TrendingDown, Calendar } from 'lucide-react';
import { CandlestickChart } from '@/components/charts/CandlestickChart';
import { SignalFeed } from '@/components/SignalFeed';
import { useNexusStore } from '@/lib/store';
import { getRecentSignals } from '@/lib/supabase';
import { cn, formatPercent } from '@/lib/utils';
import type { Signal, ForexSession, EconomicEvent } from '@/lib/types';

const FOREX_SESSIONS: ForexSession[] = [
  { name: 'Sydney', open_utc: '22:00', close_utc: '07:00', active: false, pairs: ['AUDUSD', 'NZDUSD', 'AUDNZD'] },
  { name: 'Tokyo', open_utc: '00:00', close_utc: '09:00', active: false, pairs: ['USDJPY', 'EURJPY', 'AUDJPY'] },
  { name: 'London', open_utc: '08:00', close_utc: '17:00', active: true, pairs: ['EURUSD', 'GBPUSD', 'EURGBP'] },
  { name: 'New York', open_utc: '13:00', close_utc: '22:00', active: true, pairs: ['EURUSD', 'GBPUSD', 'USDCAD'] },
];

const MAJOR_PAIRS = [
  { symbol: 'EURUSD', price: 1.08420, change: -0.12, spread: 0.2, pip: 1.08400 },
  { symbol: 'GBPUSD', price: 1.27180, change: 0.23, spread: 0.3, pip: 1.27150 },
  { symbol: 'USDJPY', price: 149.840, change: 0.45, spread: 0.5, pip: 149.870 },
  { symbol: 'USDCHF', price: 0.89920, change: -0.08, spread: 0.4, pip: 0.89900 },
  { symbol: 'AUDUSD', price: 0.64820, change: 0.18, spread: 0.3, pip: 0.64810 },
  { symbol: 'USDCAD', price: 1.36720, change: -0.22, spread: 0.4, pip: 1.36700 },
  { symbol: 'NZDUSD', price: 0.59640, change: 0.31, spread: 0.5, pip: 0.59630 },
  { symbol: 'EURGBP', price: 0.85240, change: -0.15, spread: 0.4, pip: 0.85230 },
];

const ECONOMIC_EVENTS: EconomicEvent[] = [
  { id: '1', datetime: new Date(Date.now() + 3600000).toISOString(), currency: 'USD', title: 'Non-Farm Payrolls', impact: 'HIGH', forecast: '185K', previous: '206K' },
  { id: '2', datetime: new Date(Date.now() + 7200000).toISOString(), currency: 'EUR', title: 'ECB Interest Rate Decision', impact: 'HIGH', forecast: '4.25%', previous: '4.50%' },
  { id: '3', datetime: new Date(Date.now() + 10800000).toISOString(), currency: 'GBP', title: 'UK CPI m/m', impact: 'HIGH', forecast: '0.2%', previous: '0.3%' },
  { id: '4', datetime: new Date(Date.now() + 18000000).toISOString(), currency: 'USD', title: 'FOMC Meeting Minutes', impact: 'MEDIUM', forecast: '—', previous: '—' },
  { id: '5', datetime: new Date(Date.now() + 86400000).toISOString(), currency: 'JPY', title: 'Bank of Japan Rate Decision', impact: 'HIGH', forecast: '-0.10%', previous: '-0.10%' },
];

function SessionClock({ sessions }: { sessions: ForexSession[] }) {
  const [currentTime, setCurrentTime] = useState(new Date());

  useEffect(() => {
    const t = setInterval(() => setCurrentTime(new Date()), 1000);
    return () => clearInterval(t);
  }, []);

  const utcHour = currentTime.getUTCHours();
  const utcMinute = currentTime.getUTCMinutes();

  const isSessionActive = (session: ForexSession) => {
    const openH = parseInt(session.open_utc.split(':')[0]);
    const closeH = parseInt(session.close_utc.split(':')[0]);
    if (openH > closeH) {
      return utcHour >= openH || utcHour < closeH;
    }
    return utcHour >= openH && utcHour < closeH;
  };

  const sessionColors: Record<string, string> = {
    Sydney: '#00ccff',
    Tokyo: '#ff6b35',
    London: '#00ff88',
    'New York': '#0088ff',
  };

  return (
    <div className="nexus-card p-4">
      <div className="flex items-center justify-between mb-4">
        <h3 className="font-medium text-sm text-white">Market Sessions</h3>
        <div className="flex items-center gap-2">
          <Clock size={14} className="text-muted" />
          <span className="font-mono text-sm text-nexus-blue">
            {currentTime.toUTCString().slice(17, 25)} UTC
          </span>
        </div>
      </div>

      <div className="grid grid-cols-4 gap-3">
        {sessions.map((session) => {
          const active = isSessionActive(session);
          const color = sessionColors[session.name];
          return (
            <div
              key={session.name}
              className={cn(
                'rounded-lg border p-3 transition-all',
                active
                  ? 'border-opacity-50 shadow-lg'
                  : 'border-border opacity-50'
              )}
              style={active ? { borderColor: color, boxShadow: `0 0 12px ${color}20` } : {}}
            >
              <div className="flex items-center justify-between mb-2">
                <span className="text-xs font-bold text-white">{session.name}</span>
                {active && (
                  <span
                    className="status-dot"
                    style={{ backgroundColor: color }}
                  />
                )}
              </div>
              <div className="text-xs text-muted space-y-0.5">
                <div>{session.open_utc} – {session.close_utc} UTC</div>
                <div className="mt-2">
                  {session.pairs.slice(0, 2).map((p) => (
                    <span key={p} className="text-xs mr-1" style={{ color }}>
                      {p.slice(0, 3)}
                    </span>
                  ))}
                </div>
              </div>
              <div
                className={cn(
                  'mt-2 text-xs font-bold',
                  active ? 'text-nexus-green' : 'text-muted'
                )}
              >
                {active ? 'OPEN' : 'CLOSED'}
              </div>
            </div>
          );
        })}
      </div>
    </div>
  );
}

function MajorPairsTable({ pairs }: { pairs: typeof MAJOR_PAIRS }) {
  return (
    <div className="nexus-card p-4">
      <h3 className="font-medium text-sm text-white mb-4">Major Pairs</h3>
      <table className="nexus-table">
        <thead>
          <tr>
            <th>Pair</th>
            <th className="text-right">Price</th>
            <th className="text-right">Spread</th>
            <th className="text-right">Daily Change</th>
            <th className="text-right">Trend</th>
          </tr>
        </thead>
        <tbody>
          {pairs.map((pair) => (
            <tr key={pair.symbol} className="cursor-pointer">
              <td className="font-medium text-white">{pair.symbol}</td>
              <td className="text-right font-mono text-sm">{pair.price.toFixed(5)}</td>
              <td className="text-right font-mono text-xs text-muted">{pair.spread}</td>
              <td
                className={cn(
                  'text-right font-mono text-sm font-medium',
                  pair.change >= 0 ? 'text-nexus-green' : 'text-nexus-red'
                )}
              >
                {pair.change >= 0 ? '+' : ''}{pair.change.toFixed(2)}%
              </td>
              <td className="text-right">
                {pair.change >= 0 ? (
                  <TrendingUp size={14} className="text-nexus-green inline-block" />
                ) : (
                  <TrendingDown size={14} className="text-nexus-red inline-block" />
                )}
              </td>
            </tr>
          ))}
        </tbody>
      </table>
    </div>
  );
}

function EconomicCalendar({ events }: { events: EconomicEvent[] }) {
  const impactColors = { HIGH: 'text-nexus-red', MEDIUM: 'text-nexus-yellow', LOW: 'text-muted' };
  const impactDots = { HIGH: 'bg-nexus-red', MEDIUM: 'bg-nexus-yellow', LOW: 'bg-muted' };

  const getCountdown = (datetime: string) => {
    const diff = new Date(datetime).getTime() - Date.now();
    if (diff < 0) return 'Past';
    const h = Math.floor(diff / 3600000);
    const m = Math.floor((diff % 3600000) / 60000);
    if (h > 0) return `${h}h ${m}m`;
    return `${m}m`;
  };

  return (
    <div className="nexus-card p-4">
      <div className="flex items-center justify-between mb-4">
        <h3 className="font-medium text-sm text-white">Economic Calendar</h3>
        <Calendar size={14} className="text-muted" />
      </div>
      <div className="space-y-3">
        {events.map((event) => (
          <div key={event.id} className="flex items-start gap-3 p-2 rounded-lg bg-white/2 border border-border">
            <div
              className={cn('w-2 h-2 rounded-full mt-1 flex-shrink-0', impactDots[event.impact])}
            />
            <div className="flex-1 min-w-0">
              <div className="flex items-center justify-between">
                <span className="text-xs font-medium text-white truncate">{event.title}</span>
                <span className="text-xs font-mono text-nexus-blue ml-2 flex-shrink-0">
                  {getCountdown(event.datetime)}
                </span>
              </div>
              <div className="flex items-center gap-3 mt-1">
                <span
                  className={cn('badge text-xs', `bg-${event.currency === 'USD' ? 'blue' : 'gray'}-500/10`)}
                  style={{ color: '#9ca3af' }}
                >
                  {event.currency}
                </span>
                <span className={cn('text-xs', impactColors[event.impact])}>
                  {event.impact}
                </span>
                {event.forecast && (
                  <span className="text-xs text-muted">
                    F: <span className="text-white">{event.forecast}</span>
                    {' '}P: <span className="text-muted">{event.previous}</span>
                  </span>
                )}
              </div>
            </div>
          </div>
        ))}
      </div>
    </div>
  );
}

export default function ForexPage() {
  const { signalFeed, marketPrices } = useNexusStore();
  const [activePair, setActivePair] = useState('EURUSD');
  const [signals, setSignals] = useState<Signal[]>([]);

  useEffect(() => {
    getRecentSignals('forex', 20).then(setSignals);
  }, []);

  const forexSignals = [...signals, ...signalFeed.filter((s) => s.market === 'forex')];

  return (
    <div className="space-y-4">
      {/* Session clocks */}
      <SessionClock sessions={FOREX_SESSIONS} />

      {/* Chart + pair selector */}
      <div className="grid grid-cols-3 gap-4">
        {/* Pair selector */}
        <div className="nexus-card p-4">
          <h3 className="font-medium text-sm text-white mb-3">Select Pair</h3>
          <div className="space-y-1">
            {MAJOR_PAIRS.map((pair) => (
              <button
                key={pair.symbol}
                onClick={() => setActivePair(pair.symbol)}
                className={cn(
                  'w-full flex items-center justify-between px-3 py-2 rounded-lg text-sm transition-all',
                  activePair === pair.symbol
                    ? 'bg-nexus-blue/10 border border-nexus-blue/20 text-nexus-blue'
                    : 'text-gray-400 hover:bg-white/5 hover:text-white'
                )}
              >
                <span className="font-medium">{pair.symbol}</span>
                <div className="flex items-center gap-2">
                  <span className="font-mono text-xs">{pair.price.toFixed(4)}</span>
                  <span
                    className={cn(
                      'text-xs',
                      pair.change >= 0 ? 'text-nexus-green' : 'text-nexus-red'
                    )}
                  >
                    {pair.change >= 0 ? '+' : ''}{pair.change.toFixed(2)}%
                  </span>
                </div>
              </button>
            ))}
          </div>
        </div>

        {/* Chart */}
        <div className="col-span-2 nexus-card overflow-hidden" style={{ height: 380 }}>
          <CandlestickChart
            symbol={activePair}
            market="forex"
            signals={forexSignals.filter((s) => s.symbol === activePair)}
          />
        </div>
      </div>

      {/* Bottom panels */}
      <div className="grid grid-cols-3 gap-4">
        <MajorPairsTable pairs={MAJOR_PAIRS} />
        <EconomicCalendar events={ECONOMIC_EVENTS} />
        <SignalFeed market="forex" limit={8} />
      </div>
    </div>
  );
}
