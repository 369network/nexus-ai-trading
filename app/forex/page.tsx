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

type ForexPair = {
  symbol: string;
  price: number;
  change: number;
  spread: string;
  pip: number;
};

function getSpreadLabel(symbol: string): string {
  if (['EURUSD', 'GBPUSD', 'USDCHF'].includes(symbol)) return '0.1–0.3 pips (est.)';
  if (['USDJPY', 'EURJPY'].includes(symbol)) return '0.5–1.5 pips (est.)';
  if (['GBPJPY', 'EURGBP'].includes(symbol)) return '1.0–2.0 pips (est.)';
  return '1.0–3.0 pips (est.)';
}

function useForexPrices() {
  const [pairs, setPairs] = useState<ForexPair[]>([]);
  const [loading, setLoading] = useState(true);

  useEffect(() => {
    const DEFAULT_PAIRS = [
      { symbol: 'EURUSD', base: 'USD', quote: 'EUR', invert: true },
      { symbol: 'GBPUSD', base: 'USD', quote: 'GBP', invert: true },
      { symbol: 'USDJPY', base: 'USD', quote: 'JPY', invert: false },
      { symbol: 'USDCHF', base: 'USD', quote: 'CHF', invert: false },
      { symbol: 'AUDUSD', base: 'USD', quote: 'AUD', invert: true },
      { symbol: 'USDCAD', base: 'USD', quote: 'CAD', invert: false },
      { symbol: 'NZDUSD', base: 'USD', quote: 'NZD', invert: true },
    ];

    const fetchRates = async () => {
      try {
        // Current rates
        const res = await fetch('https://api.frankfurter.app/latest?base=USD&symbols=EUR,GBP,JPY,CHF,AUD,CAD,NZD');
        const data = await res.json();

        // Yesterday rates for 24h change
        const yesterday = new Date(Date.now() - 86400000).toISOString().slice(0, 10);
        const resY = await fetch(`https://api.frankfurter.app/${yesterday}?base=USD&symbols=EUR,GBP,JPY,CHF,AUD,CAD,NZD`);
        const dataY = await resY.json();

        const mapped = DEFAULT_PAIRS.map((p) => {
          const rate = data.rates[p.quote];
          const rateY = dataY.rates?.[p.quote] ?? rate;
          const price = p.invert ? 1 / rate : rate;
          const priceY = p.invert ? 1 / rateY : rateY;
          const change = ((price - priceY) / priceY) * 100;
          return {
            symbol: p.symbol,
            price: parseFloat(price.toFixed(p.symbol === 'USDJPY' ? 3 : 5)),
            change: parseFloat(change.toFixed(3)),
            spread: getSpreadLabel(p.symbol),
            pip: parseFloat((price - 0.0001).toFixed(5)),
          };
        });
        setPairs(mapped);
        setLoading(false);
      } catch {
        setLoading(false);
      }
    };

    fetchRates();
    const interval = setInterval(fetchRates, 60_000);
    return () => clearInterval(interval);
  }, []);

  return { pairs, loading };
}

interface CalendarEvent {
  title: string;
  currency: string;
  country: string;
  datetime: string;
  impact: 'High' | 'Medium';
  forecast: string | null;
  previous: string | null;
  actual: string | null;
  is_released: boolean;
}

function useEconomicCalendar() {
  const [events, setEvents] = useState<CalendarEvent[]>([]);
  const [loading, setLoading] = useState(true);
  useEffect(() => {
    const load = async () => {
      try {
        const res = await fetch('/api/forex/calendar');
        const json = await res.json();
        setEvents(json.events ?? []);
      } catch {} finally { setLoading(false); }
    };
    load();
    const t = setInterval(load, 1_800_000); // 30 min
    return () => clearInterval(t);
  }, []);
  return { events, loading };
}

function currencyFlag(currency: string): string {
  const flags: Record<string, string> = {
    USD: '🇺🇸', EUR: '🇪🇺', GBP: '🇬🇧', JPY: '🇯🇵',
    AUD: '🇦🇺', CAD: '🇨🇦', CHF: '🇨🇭', NZD: '🇳🇿',
    CNY: '🇨🇳', INR: '🇮🇳',
  };
  return flags[currency] ?? '🌐';
}

function SessionClock({ sessions }: { sessions: ForexSession[] }) {
  const [currentTime, setCurrentTime] = useState(new Date());

  useEffect(() => {
    const t = setInterval(() => setCurrentTime(new Date()), 1000);
    return () => clearInterval(t);
  }, []);

  const utcHour = currentTime.getUTCHours();

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

function MajorPairsTable({ pairs }: { pairs: ForexPair[] }) {
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

function EconomicCalendarPanel({
  events,
  loading,
}: {
  events: CalendarEvent[];
  loading: boolean;
}) {
  return (
    <div className="nexus-card p-4">
      <div className="flex items-center justify-between mb-4">
        <h3 className="font-medium text-sm text-white">Economic Calendar</h3>
        <Calendar size={14} className="text-muted" />
      </div>

      {loading && (
        <div className="space-y-3">
          {Array.from({ length: 3 }).map((_, i) => (
            <div key={i} className="h-14 rounded-lg bg-white/5 animate-pulse" />
          ))}
        </div>
      )}

      {!loading && events.length === 0 && (
        <p className="text-xs text-muted text-center py-6">No high-impact events this week</p>
      )}

      {!loading && events.length > 0 && (
        <div className="space-y-3">
          {events.map((e, idx) => {
            const formattedTime =
              new Date(e.datetime).toLocaleString('en-US', {
                month: 'short',
                day: 'numeric',
                hour: '2-digit',
                minute: '2-digit',
                timeZone: 'UTC',
              }) + ' UTC';

            return (
              <div
                key={idx}
                className="flex items-start gap-3 p-2 rounded-lg bg-white/2 border border-border"
              >
                {/* Impact dot */}
                <div
                  className={cn(
                    'w-2 h-2 rounded-full mt-1 flex-shrink-0',
                    e.impact === 'High' ? 'bg-nexus-red' : 'bg-nexus-yellow'
                  )}
                />

                <div className="flex-1 min-w-0">
                  <div className="flex items-center justify-between gap-2">
                    <span className="text-xs font-medium text-white truncate">
                      {currencyFlag(e.currency)} {e.currency} — {e.title}
                    </span>
                    <span
                      className={cn(
                        'text-xs font-bold flex-shrink-0',
                        e.impact === 'High' ? 'text-nexus-red' : 'text-nexus-yellow'
                      )}
                    >
                      {e.impact}
                    </span>
                  </div>

                  <div className="flex items-center gap-3 mt-1">
                    {e.is_released ? (
                      <span className="text-xs">
                        Actual:{' '}
                        <span
                          className={cn(
                            'font-mono font-medium',
                            e.actual && e.previous && parseFloat(e.actual) >= parseFloat(e.previous)
                              ? 'text-nexus-green'
                              : 'text-nexus-red'
                          )}
                        >
                          {e.actual ?? '—'}
                        </span>
                      </span>
                    ) : (
                      <span className="text-xs text-muted">
                        F: <span className="text-gray-400">{e.forecast ?? '—'}</span>
                        {' '}P: <span className="text-gray-500">{e.previous ?? '—'}</span>
                      </span>
                    )}
                    <span className="text-xs text-muted ml-auto flex-shrink-0">{formattedTime}</span>
                  </div>
                </div>
              </div>
            );
          })}
        </div>
      )}
    </div>
  );
}

export default function ForexPage() {
  const { signalFeed, marketPrices } = useNexusStore();
  const [activePair, setActivePair] = useState('EURUSD');
  const [signals, setSignals] = useState<Signal[]>([]);
  const { pairs: MAJOR_PAIRS, loading } = useForexPrices();
  const { events: calendarEvents, loading: calendarLoading } = useEconomicCalendar();

  useEffect(() => {
    getRecentSignals('forex', 20).then(setSignals);
  }, []);

  const forexSignals = [...signals, ...signalFeed.filter((s) => s.market === 'forex')];

  return (
    <div className="space-y-4">
      {/* Header with attribution */}
      <div className="flex items-center justify-between px-1">
        <span />
        <span className="text-xs text-muted">Rates via Frankfurter.app</span>
      </div>

      {/* Session clocks */}
      <SessionClock sessions={FOREX_SESSIONS} />

      {/* Chart + pair selector */}
      <div className="grid grid-cols-3 gap-4">
        {/* Pair selector */}
        <div className="nexus-card p-4">
          <h3 className="font-medium text-sm text-white mb-3">Select Pair</h3>
          {loading && (
            <div className="space-y-1">
              {Array.from({ length: 7 }).map((_, i) => (
                <div key={i} className="h-9 rounded-lg bg-white/5 animate-pulse" />
              ))}
            </div>
          )}
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
        {loading ? (
          <div className="nexus-card p-4">
            <div className="h-4 w-24 bg-white/10 rounded animate-pulse mb-4" />
            {Array.from({ length: 7 }).map((_, i) => (
              <div key={i} className="h-8 rounded bg-white/5 animate-pulse mb-2" />
            ))}
          </div>
        ) : (
          <MajorPairsTable pairs={MAJOR_PAIRS} />
        )}
        <EconomicCalendarPanel events={calendarEvents} loading={calendarLoading} />
        <SignalFeed market="forex" limit={8} />
      </div>
    </div>
  );
}
