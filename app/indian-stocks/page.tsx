'use client';

import { useEffect, useRef, useState, useCallback } from 'react';
import { BarChart, Bar, XAxis, YAxis, ResponsiveContainer, Tooltip, Cell, ReferenceLine } from 'recharts';
import { TrendingUp, TrendingDown, Activity, Clock, RefreshCw, AlertCircle, ExternalLink } from 'lucide-react';
import { SignalFeed } from '@/components/SignalFeed';
import { TradeTable } from '@/components/TradeTable';
import { useNexusStore } from '@/lib/store';
import { getRecentSignals, getActiveTrades } from '@/lib/supabase';
import { cn, formatCurrency, formatPercent, formatNumber } from '@/lib/utils';
import type { Signal, Trade, FIIDIIFlow, OptionChainSummary } from '@/lib/types';

// ---------------------------------------------------------------------------
// Types
// ---------------------------------------------------------------------------

interface IndexQuote {
  symbol:    string;
  label:     string;
  price:     number;
  change:    number;
  changePct: number;
  high:      number;
  low:       number;
  prevClose: number;
  volume:    number;
}

type IndicesMap = Record<string, IndexQuote>;

// ---------------------------------------------------------------------------
// Hook: live Indian index prices
// ---------------------------------------------------------------------------

function useIndianIndices(intervalMs = 60_000) {
  const [indices, setIndices] = useState<IndicesMap>({});
  const [loading, setLoading] = useState(true);
  const [error, setError]   = useState<string | null>(null);
  const [lastUpdated, setLastUpdated] = useState<Date | null>(null);
  const timerRef = useRef<ReturnType<typeof setTimeout> | null>(null);
  const mountedRef = useRef(true);

  const fetch_ = useCallback(async () => {
    try {
      const res = await fetch('/api/prices/indian', { cache: 'no-store' });
      if (!res.ok) throw new Error(`API ${res.status}`);
      const data = await res.json();
      if (mountedRef.current) {
        setIndices(data.indices ?? {});
        setLastUpdated(new Date());
        setError(null);
      }
    } catch (e: unknown) {
      if (mountedRef.current) setError(e instanceof Error ? e.message : 'Fetch failed');
    } finally {
      if (mountedRef.current) setLoading(false);
    }
  }, []);

  useEffect(() => {
    mountedRef.current = true;
    fetch_();
    timerRef.current = setInterval(fetch_, intervalMs);
    return () => {
      mountedRef.current = false;
      if (timerRef.current) clearInterval(timerRef.current);
    };
  }, [fetch_, intervalMs]);

  return { indices, loading, error, lastUpdated, refresh: fetch_ };
}

// ---------------------------------------------------------------------------
// Hook: NSE market data (breadth + option chain)
// ---------------------------------------------------------------------------

function useNseMarket() {
  const [breadth, setBreadth] = useState<{advances:number;declines:number;unchanged:number;high52:number;low52:number}|null>(null);
  const [optionChain, setOptionChain] = useState<{underlying:number;expiry:string;pcr:number;max_pain:number;atm_iv:number;call_oi_buildup:number[];put_oi_buildup:number[]}|null>(null);
  const [loading, setLoading] = useState(true);
  useEffect(()=>{
    const load=async()=>{
      try{
        const res=await fetch('/api/indian/market');
        const json=await res.json();
        setBreadth(json.breadth??null);
        setOptionChain(json.option_chain??null);
      }catch{}finally{setLoading(false);}
    };
    load();
    const t=setInterval(load,300_000);
    return()=>clearInterval(t);
  },[]);
  return{breadth,optionChain,loading};
}

// ---------------------------------------------------------------------------
// Helpers
// ---------------------------------------------------------------------------

const INDEX_ORDER = ['NIFTY50', 'BANKNIFTY', 'MIDCAP', 'IT'];

const FALLBACK_INDICES: IndexQuote[] = INDEX_ORDER.map((key) => ({
  symbol:    key,
  label:     key === 'NIFTY50' ? 'NIFTY 50' : key === 'BANKNIFTY' ? 'BANK NIFTY' : key === 'MIDCAP' ? 'MIDCAP 50' : 'NIFTY IT',
  price:     0, change: 0, changePct: 0, high: 0, low: 0, prevClose: 0, volume: 0,
}));

function getMarketStatus() {
  const now     = new Date();
  const h       = now.getUTCHours();
  const m       = now.getUTCMinutes();
  const istMin  = (h * 60 + m + 330) % 1440;
  const istH    = Math.floor(istMin / 60);
  const istMMin = istMin % 60;
  const istTotal = istH * 60 + istMMin;
  const day = now.getUTCDay(); // 0=Sun 6=Sat, adjust for IST
  const isWeekend = day === 0 || day === 6;

  if (isWeekend) return { status: 'closed', color: 'text-muted', label: 'CLOSED (Weekend)' };
  if (istTotal >= 9 * 60 && istTotal < 9 * 60 + 15)
    return { status: 'pre-open', color: 'text-nexus-yellow', label: 'PRE-OPEN' };
  if (istTotal >= 9 * 60 + 15 && istTotal < 15 * 60 + 30)
    return { status: 'open',    color: 'text-nexus-green',  label: 'OPEN' };
  if (istTotal >= 15 * 60 + 30 && istTotal < 16 * 60)
    return { status: 'closing', color: 'text-nexus-yellow', label: 'CLOSING SESSION' };
  return { status: 'closed', color: 'text-muted', label: 'CLOSED' };
}

// ---------------------------------------------------------------------------
// Components
// ---------------------------------------------------------------------------

function IndexCard({ index, loading }: { index: IndexQuote; loading: boolean }) {
  const isPositive = index.changePct >= 0;

  if (loading) {
    return (
      <div className="nexus-card p-4 animate-pulse">
        <div className="h-3 w-20 bg-border rounded mb-3" />
        <div className="h-8 w-32 bg-border rounded mb-2" />
        <div className="h-4 w-24 bg-border rounded" />
      </div>
    );
  }

  if (index.price === 0) {
    return (
      <div className="nexus-card p-4 flex flex-col items-center justify-center text-muted text-sm">
        <AlertCircle size={16} className="mb-2" />
        <span>{index.label}</span>
        <span className="text-xs mt-1">Unavailable</span>
      </div>
    );
  }

  return (
    <div className="nexus-card nexus-card-hover p-4">
      <div className="flex items-center justify-between mb-2">
        <span className="text-xs font-medium text-muted uppercase tracking-wider">{index.label}</span>
        {isPositive ? <TrendingUp size={14} className="text-nexus-green" /> : <TrendingDown size={14} className="text-nexus-red" />}
      </div>
      <div className="font-mono text-2xl font-bold text-white">
        {formatNumber(index.price, 2)}
      </div>
      <div className={cn('text-sm font-medium mt-1', isPositive ? 'text-nexus-green' : 'text-nexus-red')}>
        {isPositive ? '+' : ''}{formatNumber(index.change, 2)} ({isPositive ? '+' : ''}{index.changePct.toFixed(2)}%)
      </div>
      <div className="flex justify-between text-xs text-muted mt-3">
        <span>H: {formatNumber(index.high, 2)}</span>
        <span>L: {formatNumber(index.low, 2)}</span>
      </div>
      {index.volume > 0 && (
        <div className="text-xs text-muted mt-1">
          Vol: {(index.volume / 1_000_000).toFixed(2)}M
        </div>
      )}
    </div>
  );
}

function FIIDIINotice() {
  return (
    <div className="nexus-card p-4 col-span-2 flex flex-col items-center justify-center gap-3 min-h-[200px]">
      <Activity size={24} className="text-nexus-blue opacity-60" />
      <div className="text-center">
        <h3 className="font-medium text-sm text-white mb-1">FII / DII Flow Data</h3>
        <p className="text-xs text-muted max-w-xs">
          Real-time FII/DII flow data requires NSE India API authentication or a paid data provider.
          Configure your NSE data source in <span className="text-nexus-blue">Settings → API Connections</span>.
        </p>
      </div>
      <div className="flex gap-3">
        <a
          href="https://www.nseindia.com/market-data/fii-dii-activity"
          target="_blank"
          rel="noopener noreferrer"
          className="flex items-center gap-1.5 text-xs text-nexus-blue hover:underline"
        >
          <ExternalLink size={12} />
          NSE FII/DII Portal
        </a>
        <a
          href="https://www.sebi.gov.in/sebiweb/other/OtherAction.do?doRecognisedFpi=yes"
          target="_blank"
          rel="noopener noreferrer"
          className="flex items-center gap-1.5 text-xs text-nexus-blue hover:underline"
        >
          <ExternalLink size={12} />
          SEBI FPI Data
        </a>
      </div>
    </div>
  );
}

function OptionChainPanel({
  optionChain,
  loading,
}: {
  optionChain: {underlying:number;expiry:string;pcr:number;max_pain:number;atm_iv:number;call_oi_buildup:number[];put_oi_buildup:number[]} | null;
  loading: boolean;
}) {
  if (loading) {
    return (
      <div className="nexus-card p-4 animate-pulse">
        <div className="h-3 w-32 bg-border rounded mb-4" />
        <div className="grid grid-cols-3 gap-3 mb-4">
          {[0,1,2].map((i) => <div key={i} className="h-16 bg-border rounded" />)}
        </div>
        <div className="h-20 bg-border rounded" />
      </div>
    );
  }

  if (!optionChain) {
    return (
      <div className="nexus-card p-4 flex flex-col items-center justify-center min-h-[200px] gap-2">
        <AlertCircle size={18} className="text-muted opacity-60" />
        <p className="text-xs text-muted text-center">NSE data temporarily unavailable</p>
      </div>
    );
  }

  const pcr = optionChain.pcr;
  const pcrColor = pcr > 1.2 ? 'text-nexus-green' : pcr < 0.8 ? 'text-nexus-red' : 'text-nexus-yellow';
  const callBuildup = optionChain.call_oi_buildup ?? [];
  const putBuildup = optionChain.put_oi_buildup ?? [];

  return (
    <div className="nexus-card p-4">
      <div className="flex items-center justify-between mb-4">
        <h3 className="font-medium text-sm text-white">Option Chain — NIFTY</h3>
        <span className="text-xs text-muted">{optionChain.expiry ?? '—'}</span>
      </div>

      <div className="grid grid-cols-3 gap-3 mb-4">
        <div className="text-center">
          <div className="text-xs text-muted mb-1">PCR</div>
          <div className={cn('font-mono font-bold text-lg', pcrColor)}>
            {optionChain.pcr?.toFixed(2) ?? '—'}
          </div>
          <div className="text-xs text-muted">{pcr > 1.0 ? 'Bullish' : 'Bearish'}</div>
        </div>
        <div className="text-center">
          <div className="text-xs text-muted mb-1">Max Pain</div>
          <div className="font-mono font-bold text-lg text-nexus-yellow">
            {optionChain.max_pain?.toLocaleString() ?? '—'}
          </div>
          <div className="text-xs text-muted">Strike</div>
        </div>
        <div className="text-center">
          <div className="text-xs text-muted mb-1">ATM IV</div>
          <div className="font-mono font-bold text-lg text-nexus-blue">
            {optionChain.atm_iv ? optionChain.atm_iv + '%' : '—'}
          </div>
          <div className="text-xs text-muted">Implied Vol</div>
        </div>
      </div>

      <div className="mb-3">
        <div className="text-xs text-muted mb-2">Key OI Strikes</div>
        <div className="flex items-start justify-between gap-3">
          <div className="flex-1">
            <div className="text-xs text-nexus-red mb-1">Call Resistance</div>
            <div className="flex gap-1.5 flex-wrap">
              {callBuildup.length > 0 ? callBuildup.map((strike) => (
                <span key={strike} className="text-xs bg-nexus-red/10 text-nexus-red border border-nexus-red/20 rounded px-1.5 py-0.5">
                  {strike.toLocaleString()}
                </span>
              )) : <span className="text-xs text-muted">—</span>}
            </div>
          </div>
          <div className="w-px h-12 bg-border" />
          <div className="flex-1">
            <div className="text-xs text-nexus-green mb-1">Put Support</div>
            <div className="flex gap-1.5 flex-wrap">
              {putBuildup.length > 0 ? putBuildup.map((strike) => (
                <span key={strike} className="text-xs bg-nexus-green/10 text-nexus-green border border-nexus-green/20 rounded px-1.5 py-0.5">
                  {strike.toLocaleString()}
                </span>
              )) : <span className="text-xs text-muted">—</span>}
            </div>
          </div>
        </div>
      </div>

      <div className="pt-2 border-t border-border text-xs text-muted text-center">
        Live via /api/indian/market
      </div>
    </div>
  );
}

function MarketBreadth({
  breadth,
  loading,
}: {
  breadth: {advances:number;declines:number;unchanged:number;high52:number;low52:number} | null;
  loading: boolean;
}) {
  if (loading) {
    return (
      <div className="nexus-card p-4 animate-pulse">
        <div className="h-3 w-28 bg-border rounded mb-4" />
        <div className="space-y-3">
          {[0,1,2].map((i) => <div key={i} className="h-8 bg-border rounded" />)}
        </div>
      </div>
    );
  }

  if (!breadth) {
    return (
      <div className="nexus-card p-4 flex flex-col items-center justify-center min-h-[200px] gap-2">
        <AlertCircle size={18} className="text-muted opacity-60" />
        <p className="text-xs text-muted text-center">NSE data temporarily unavailable</p>
      </div>
    );
  }

  const { advances, declines, unchanged, high52, low52 } = breadth;
  const breadTotal = advances + declines + unchanged;
  const advPct     = breadTotal > 0 ? (advances / breadTotal) * 100 : 0;
  const decPct     = breadTotal > 0 ? (declines / breadTotal) * 100 : 0;

  return (
    <div className="nexus-card p-4">
      <h3 className="font-medium text-sm text-white mb-4">Market Breadth</h3>
      <div className="space-y-3">
        <div>
          <div className="flex justify-between text-xs mb-1">
            <span className="text-nexus-green">Advances: {advances}</span>
            <span className="text-nexus-green">{advPct.toFixed(1)}%</span>
          </div>
          <div className="h-2 bg-border rounded-full overflow-hidden">
            <div className="h-full bg-nexus-green rounded-full" style={{ width: `${advPct}%` }} />
          </div>
        </div>
        <div>
          <div className="flex justify-between text-xs mb-1">
            <span className="text-nexus-red">Declines: {declines}</span>
            <span className="text-nexus-red">{decPct.toFixed(1)}%</span>
          </div>
          <div className="h-2 bg-border rounded-full overflow-hidden">
            <div className="h-full bg-nexus-red rounded-full" style={{ width: `${decPct}%` }} />
          </div>
        </div>
        <div>
          <div className="flex justify-between text-xs mb-1">
            <span className="text-muted">Unchanged: {unchanged}</span>
          </div>
          <div className="h-2 bg-border rounded-full overflow-hidden">
            <div className="h-full bg-muted rounded-full" style={{ width: `${breadTotal > 0 ? (unchanged / breadTotal) * 100 : 0}%` }} />
          </div>
        </div>

        <div className="pt-2 border-t border-border">
          <div className="text-center">
            <div className="text-xs text-muted">A/D Ratio</div>
            <div className={cn('text-lg font-mono font-bold mt-1', advances > declines ? 'text-nexus-green' : 'text-nexus-red')}>
              {declines > 0 ? (advances / declines).toFixed(2) : '—'}
            </div>
            <div className="text-xs text-muted">
              {advances > declines ? 'Broad Buying' : 'Broad Selling'}
            </div>
          </div>
        </div>

        <div className="pt-2 border-t border-border grid grid-cols-2 gap-2 text-xs">
          <div><div className="text-muted">52W High</div><div className="text-nexus-green font-mono">{high52}</div></div>
          <div><div className="text-muted">52W Low</div><div className="text-nexus-red font-mono">{low52}</div></div>
        </div>

        <div className="text-xs text-muted text-center pt-1 border-t border-border">
          Live via /api/indian/market
        </div>
      </div>
    </div>
  );
}

// ---------------------------------------------------------------------------
// Page
// ---------------------------------------------------------------------------

export default function IndianStocksPage() {
  const { signalFeed, activeTrades } = useNexusStore();
  const [signals, setSignals] = useState<Signal[]>([]);
  const [trades,  setTrades]  = useState<Trade[]>([]);
  const marketStatus = getMarketStatus();

  const { indices, loading, error, lastUpdated, refresh } = useIndianIndices(60_000);
  const { breadth, optionChain, loading: nseLoading } = useNseMarket();

  // Ordered list for display
  const displayIndices = INDEX_ORDER.map((key) => indices[key] ?? FALLBACK_INDICES.find((f) => f.symbol === key)!);

  useEffect(() => {
    const load = async () => {
      const [sigs, trds] = await Promise.all([getRecentSignals('indian_stocks', 20), getActiveTrades()]);
      setSignals(sigs);
      setTrades(trds.filter((t) => t.market === 'indian_stocks'));
    };
    load();
  }, []);

  const indianSignals = [...signals, ...signalFeed.filter((s) => s.market === 'indian_stocks')];
  const indianTrades  = [...trades,  ...activeTrades.filter((t) => t.market === 'indian_stocks')];

  return (
    <div className="space-y-4">
      {/* Header */}
      <div className="flex items-center justify-between">
        <div className="flex items-center gap-4">
          <div className={cn(
            'flex items-center gap-2 px-3 py-1.5 rounded-lg border',
            marketStatus.status === 'open'
              ? 'bg-nexus-green/5 border-nexus-green/20'
              : 'bg-border border-border'
          )}>
            <span className={cn('status-dot', {
              active:   marketStatus.status === 'open',
              warning:  marketStatus.status === 'pre-open' || marketStatus.status === 'closing',
              inactive: marketStatus.status === 'closed',
            })} />
            <span className={cn('text-sm font-bold', marketStatus.color)}>
              NSE/BSE {marketStatus.label}
            </span>
          </div>
          <div className="flex items-center gap-2 text-sm text-muted">
            <Clock size={14} />
            <ISTClock />
          </div>
        </div>

        <div className="flex items-center gap-3">
          {lastUpdated && !loading && (
            <span className="text-xs text-muted">
              Updated {lastUpdated.toLocaleTimeString()} · Yahoo Finance
            </span>
          )}
          {error && (
            <span className="text-xs text-nexus-red flex items-center gap-1">
              <AlertCircle size={12} /> {error}
            </span>
          )}
          <button
            onClick={refresh}
            className="flex items-center gap-1.5 text-xs text-muted hover:text-white transition-colors"
          >
            <RefreshCw size={12} className={loading ? 'animate-spin' : ''} />
            Refresh
          </button>
        </div>
      </div>

      {/* Index cards */}
      <div className="grid grid-cols-4 gap-4">
        {displayIndices.map((index) => (
          <IndexCard key={index.symbol} index={index} loading={loading} />
        ))}
      </div>

      {/* FII/DII + option chain + breadth */}
      <div className="grid grid-cols-4 gap-4">
        <FIIDIINotice />
        <OptionChainPanel optionChain={optionChain} loading={nseLoading} />
        <MarketBreadth breadth={breadth} loading={nseLoading} />
      </div>

      {/* Trades + signals */}
      <div className="grid grid-cols-2 gap-4">
        <TradeTable trades={indianTrades} title="Active Indian Stock Trades" />
        <SignalFeed market="indian_stocks" limit={8} />
      </div>
    </div>
  );
}

function ISTClock() {
  const [time, setTime] = useState('');

  useEffect(() => {
    const update = () => {
      const ist = new Intl.DateTimeFormat('en-IN', {
        timeZone: 'Asia/Kolkata',
        hour:     '2-digit',
        minute:   '2-digit',
        second:   '2-digit',
        hour12:   false,
      }).format(new Date());
      setTime(`${ist} IST`);
    };
    update();
    const t = setInterval(update, 1000);
    return () => clearInterval(t);
  }, []);

  return <span className="font-mono">{time}</span>;
}
