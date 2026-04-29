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

type IndexItem = {
  symbol: string;
  label: string;
  price: number;
  change: number;
  changePct: number;
};

function useUSIndices() {
  const [indices, setIndices] = useState<IndexItem[]>([]);
  const [loading, setLoading] = useState(true);

  const LABELS: Record<string, { label: string; symbol: string }> = {
    spx: { label: 'S&P 500', symbol: 'SPX' },
    ndx: { label: 'NASDAQ 100', symbol: 'NDX' },
    dji: { label: 'Dow Jones', symbol: 'DJI' },
    rut: { label: 'Russell 2000', symbol: 'RUT' },
  };

  useEffect(() => {
    const load = async () => {
      try {
        const res = await fetch('/api/prices/indices');
        const data = await res.json();
        const mapped = Object.entries(data.indices)
          .filter(([, v]) => v !== null)
          .map(([key, v]: [string, any]) => ({
            symbol: LABELS[key].symbol,
            label: LABELS[key].label,
            price: v.price,
            change: v.change,
            changePct: v.changePct,
          }));
        setIndices(mapped);
        setLoading(false);
      } catch {
        setLoading(false);
      }
    };
    load();
    const t = setInterval(load, 60_000);
    return () => clearInterval(t);
  }, []);

  return { indices, loading };
}

// ---------------------------------------------------------------------------
// Hook: sector performance from API
// ---------------------------------------------------------------------------

function useSectors(){
  const [sectors,setSectors]=useState<Array<{symbol:string;name:string;weight:number;price:number|null;change:number|null;change_pct:number|null}>>([]);
  const [loading,setLoading]=useState(true);
  useEffect(()=>{
    const load=async()=>{try{const res=await fetch('/api/stocks/sectors');const json=await res.json();setSectors(json.sectors??[]);}catch{}finally{setLoading(false);}};
    load();const t=setInterval(load,60_000);return()=>clearInterval(t);
  },[]);
  return{sectors,loading};
}

// ---------------------------------------------------------------------------
// Hook: earnings calendar from API
// ---------------------------------------------------------------------------

function useEarnings(){
  const [events,setEvents]=useState<Array<{symbol:string;company:string;datetime:string;eps_estimate:number|null;eps_actual:number|null;eps_surprise_pct:number|null;is_released:boolean;market_cap:string}>>([]);
  const [loading,setLoading]=useState(true);
  useEffect(()=>{
    const load=async()=>{try{const res=await fetch('/api/stocks/earnings');const json=await res.json();setEvents(json.events??[]);}catch{}finally{setLoading(false);}};
    load();const t=setInterval(load,3_600_000);return()=>clearInterval(t);
  },[]);
  return{events,loading};
}

// ---------------------------------------------------------------------------
// Components
// ---------------------------------------------------------------------------

function IndexCard({ index }: { index: IndexItem }) {
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

function SectorHeatmap({
  sectors,
  loading,
}: {
  sectors: Array<{symbol:string;name:string;weight:number;price:number|null;change:number|null;change_pct:number|null}>;
  loading: boolean;
}) {
  if (loading) {
    return (
      <div className="nexus-card p-4">
        <h3 className="font-medium text-sm text-white mb-4">Sector Performance Heatmap</h3>
        <div className="grid grid-cols-3 gap-2">
          {Array.from({ length: 11 }).map((_, i) => (
            <div key={i} className="h-20 rounded-lg bg-border animate-pulse" />
          ))}
        </div>
      </div>
    );
  }

  const sorted = [...sectors].sort((a, b) => (b.change_pct ?? 0) - (a.change_pct ?? 0));
  const maxAbs = Math.max(...sorted.map((s) => Math.abs(s.change_pct ?? 0)), 1);

  const getColor = (change_pct: number | null) => {
    if (change_pct === null) return 'rgba(107,114,128,0.2)';
    const intensity = Math.abs(change_pct) / maxAbs;
    if (change_pct > 0) return `rgba(0, 255, 136, ${0.15 + intensity * 0.65})`;
    return `rgba(255, 68, 68, ${0.15 + intensity * 0.65})`;
  };

  return (
    <div className="nexus-card p-4">
      <h3 className="font-medium text-sm text-white mb-4">Sector Performance Heatmap</h3>
      <div className="grid grid-cols-3 gap-2">
        {sorted.map((sector) => (
          <div
            key={sector.symbol}
            className="rounded-lg p-3 transition-all hover:opacity-80 cursor-pointer"
            style={{ backgroundColor: getColor(sector.change_pct) }}
          >
            <div className="font-bold text-sm text-white">{sector.symbol}</div>
            <div className="text-xs text-gray-300 truncate">{sector.name}</div>
            <div
              className={cn(
                'text-sm font-mono font-bold mt-1',
                sector.change_pct === null ? 'text-muted' :
                sector.change_pct >= 0 ? 'text-nexus-green' : 'text-nexus-red'
              )}
            >
              {sector.change_pct !== null
                ? `${sector.change_pct >= 0 ? '+' : ''}${sector.change_pct.toFixed(2)}%`
                : '—'}
            </div>
          </div>
        ))}
      </div>

      {/* Bar chart */}
      {sorted.length > 0 && (
        <div className="mt-4">
          <ResponsiveContainer width="100%" height={120}>
            <BarChart
              data={sorted}
              layout="vertical"
              margin={{ top: 0, right: 4, left: 55, bottom: 0 }}
            >
              <XAxis type="number" tick={{ fontSize: 10, fill: '#6b7280' }} tickLine={false} tickFormatter={(v) => `${v}%`} />
              <YAxis type="category" dataKey="symbol" tick={{ fontSize: 10, fill: '#9ca3af' }} tickLine={false} width={40} />
              <Tooltip
                contentStyle={{ background: '#1a1a28', border: '1px solid #2a2a3e', borderRadius: '6px', fontSize: '11px' }}
                formatter={(v: number) => [`${v.toFixed(2)}%`, 'Change']}
              />
              <Bar dataKey="change_pct" radius={[0, 2, 2, 0]}>
                {sorted.map((entry, i) => (
                  <Cell key={i} fill={(entry.change_pct ?? 0) >= 0 ? '#00ff88' : '#ff4444'} opacity={0.7} />
                ))}
              </Bar>
            </BarChart>
          </ResponsiveContainer>
        </div>
      )}
    </div>
  );
}

function EarningsCalendar({
  events,
  loading,
}: {
  events: Array<{symbol:string;company:string;datetime:string;eps_estimate:number|null;eps_actual:number|null;eps_surprise_pct:number|null;is_released:boolean;market_cap:string}>;
  loading: boolean;
}) {
  if (loading) {
    return (
      <div className="nexus-card p-4">
        <div className="flex items-center justify-between mb-4">
          <h3 className="font-medium text-sm text-white">Earnings Calendar</h3>
          <Calendar size={14} className="text-muted" />
        </div>
        <div className="space-y-3">
          {Array.from({ length: 4 }).map((_, i) => (
            <div key={i} className="h-16 bg-border rounded-lg animate-pulse" />
          ))}
        </div>
      </div>
    );
  }

  if (events.length === 0) {
    return (
      <div className="nexus-card p-4">
        <div className="flex items-center justify-between mb-4">
          <h3 className="font-medium text-sm text-white">Earnings Calendar</h3>
          <Calendar size={14} className="text-muted" />
        </div>
        <div className="flex items-center justify-center h-32 text-xs text-muted">
          No upcoming earnings
        </div>
      </div>
    );
  }

  return (
    <div className="nexus-card p-4">
      <div className="flex items-center justify-between mb-4">
        <h3 className="font-medium text-sm text-white">Earnings Calendar</h3>
        <Calendar size={14} className="text-muted" />
      </div>
      <div className="space-y-3">
        {events.map((e) => {
          const dateLabel = new Date(e.datetime).toLocaleDateString('en-US', { month: 'short', day: 'numeric' });
          const hasSurprise = e.is_released && e.eps_surprise_pct !== null;
          const isBeat = (e.eps_surprise_pct ?? 0) >= 0;

          return (
            <div key={e.symbol} className="flex items-center justify-between p-2 rounded-lg bg-white/2 border border-border">
              <div className="flex items-center gap-3">
                <div className="w-10 h-10 rounded-lg bg-nexus-blue/10 flex items-center justify-center">
                  <span className="text-xs font-bold text-nexus-blue">{e.symbol.slice(0, 3)}</span>
                </div>
                <div>
                  <div className="text-sm font-bold text-white">{e.symbol}</div>
                  <div className="text-xs text-muted">{e.company} · {e.market_cap}</div>
                </div>
              </div>
              <div className="text-right">
                <div className="text-xs text-muted">
                  Est: <span className="text-white">{e.eps_estimate !== null ? `$${e.eps_estimate.toFixed(2)}` : '—'}</span>
                </div>
                {e.is_released && e.eps_actual !== null && (
                  <div className={cn('text-xs font-mono', (e.eps_actual ?? 0) >= (e.eps_estimate ?? 0) ? 'text-nexus-green' : 'text-nexus-red')}>
                    Act: ${e.eps_actual.toFixed(2)}
                  </div>
                )}
                {hasSurprise ? (
                  <div className={cn('text-xs font-medium mt-0.5', isBeat ? 'text-nexus-green' : 'text-nexus-red')}>
                    {isBeat ? '+' : ''}{e.eps_surprise_pct!.toFixed(1)}% {isBeat ? 'beat' : 'miss'}
                  </div>
                ) : (
                  <div className="text-xs text-muted mt-0.5">{dateLabel}</div>
                )}
              </div>
            </div>
          );
        })}
      </div>
    </div>
  );
}

function USStockSignals() {
  const [signals, setSignals] = useState<Signal[]>([]);
  const [loading, setLoading] = useState(true);

  useEffect(() => {
    const load = async () => {
      try {
        const sigs = await getRecentSignals('us_stocks', 10);
        setSignals(sigs);
      } catch {
        // leave empty
      } finally {
        setLoading(false);
      }
    };
    load();
  }, []);

  return (
    <div className="nexus-card p-4">
      <div className="flex items-center justify-between mb-4">
        <h3 className="font-medium text-sm text-white">US Stock Signals</h3>
        <span className="text-xs text-muted">Recent</span>
      </div>
      {loading ? (
        <div className="space-y-2">
          {Array.from({ length: 4 }).map((_, i) => (
            <div key={i} className="h-10 bg-border rounded-lg animate-pulse" />
          ))}
        </div>
      ) : signals.length === 0 ? (
        <div className="flex items-center justify-center h-32 text-xs text-muted text-center">
          No US stock signals yet. Trading starts when US markets open.
        </div>
      ) : (
        <div className="overflow-auto">
          <table className="w-full text-xs">
            <thead>
              <tr className="text-muted border-b border-border">
                <th className="text-left pb-2 font-medium">Symbol</th>
                <th className="text-left pb-2 font-medium">Direction</th>
                <th className="text-left pb-2 font-medium">Confidence</th>
                <th className="text-right pb-2 font-medium">Time</th>
              </tr>
            </thead>
            <tbody>
              {signals.map((sig) => (
                <tr key={sig.id} className="border-b border-border/40 last:border-0">
                  <td className="py-2 font-bold text-white">{sig.symbol}</td>
                  <td className="py-2">
                    <span className={cn(
                      'px-1.5 py-0.5 rounded text-xs font-bold',
                      sig.direction === 'LONG' ? 'bg-nexus-green/10 text-nexus-green' :
                      sig.direction === 'SHORT' ? 'bg-nexus-red/10 text-nexus-red' :
                      'bg-muted/10 text-muted'
                    )}>
                      {sig.direction}
                    </span>
                  </td>
                  <td className="py-2 font-mono text-nexus-blue">
                    {typeof sig.confidence === 'number' ? `${(sig.confidence * 100).toFixed(0)}%` : '—'}
                  </td>
                  <td className="py-2 text-right text-muted">
                    {new Date(sig.created_at).toLocaleTimeString('en-US', { hour: '2-digit', minute: '2-digit' })}
                  </td>
                </tr>
              ))}
            </tbody>
          </table>
        </div>
      )}
    </div>
  );
}

export default function USStocksPage() {
  const { signalFeed, activeTrades } = useNexusStore();
  const [trades, setTrades] = useState<Trade[]>([]);
  const { indices: US_INDICES, loading: indicesLoading } = useUSIndices();
  const { sectors, loading: sectorsLoading } = useSectors();
  const { events: earningsEvents, loading: earningsLoading } = useEarnings();

  useEffect(() => {
    const load = async () => {
      try {
        const trds = await getActiveTrades();
        setTrades(trds.filter((t) => t.market === 'us_stocks'));
      } catch {
        // leave empty
      }
    };
    load();
  }, []);

  const usTrades = [...trades, ...activeTrades.filter((t) => t.market === 'us_stocks')];

  return (
    <div className="space-y-4">
      {/* Attribution */}
      <div className="flex items-center justify-end px-1">
        <span className="text-xs text-muted">
          {indicesLoading ? (
            'Loading live data...'
          ) : (
            <>
              <span className="inline-block w-2 h-2 rounded-full bg-nexus-green mr-1 animate-pulse" />
              Live · Yahoo Finance
            </>
          )}
        </span>
      </div>

      {/* Index cards */}
      <div className="grid grid-cols-4 gap-4">
        {indicesLoading
          ? Array.from({ length: 4 }).map((_, i) => (
              <div key={i} className="nexus-card p-4 h-24 animate-pulse bg-white/5" />
            ))
          : US_INDICES.map((index) => (
              <IndexCard key={index.symbol} index={index} />
            ))}
      </div>

      {/* Sector heatmap + earnings */}
      <div className="grid grid-cols-3 gap-4">
        <div className="col-span-2">
          <SectorHeatmap sectors={sectors} loading={sectorsLoading} />
        </div>
        <EarningsCalendar events={earningsEvents} loading={earningsLoading} />
      </div>

      {/* US Stock Signals + trades + signal feed */}
      <div className="grid grid-cols-3 gap-4">
        <USStockSignals />
        <TradeTable trades={usTrades} title="Active US Stock Trades" />
        <SignalFeed market="us_stocks" limit={6} />
      </div>
    </div>
  );
}
