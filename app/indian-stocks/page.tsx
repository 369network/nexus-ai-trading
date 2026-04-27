'use client';

import { useEffect, useState } from 'react';
import { BarChart, Bar, XAxis, YAxis, ResponsiveContainer, Tooltip, Cell, ReferenceLine } from 'recharts';
import { TrendingUp, TrendingDown, Activity, Clock } from 'lucide-react';
import { SignalFeed } from '@/components/SignalFeed';
import { TradeTable } from '@/components/TradeTable';
import { useNexusStore } from '@/lib/store';
import { getRecentSignals, getActiveTrades } from '@/lib/supabase';
import { cn, formatCurrency, formatPercent, formatNumber } from '@/lib/utils';
import type { Signal, Trade, FIIDIIFlow, OptionChainSummary } from '@/lib/types';

const INDICES = [
  { symbol: 'NIFTY50', label: 'NIFTY 50', price: 24500.15, change: 147.30, changePct: 0.60, high: 24620, low: 24380 },
  { symbol: 'BANKNIFTY', label: 'BANK NIFTY', price: 52840.70, change: -215.80, changePct: -0.41, high: 53100, low: 52600 },
  { symbol: 'MIDCAP', label: 'MIDCAP 50', price: 14280.45, change: 82.10, changePct: 0.58, high: 14350, low: 14190 },
  { symbol: 'IT', label: 'NIFTY IT', price: 37420.80, change: 312.55, changePct: 0.84, high: 37650, low: 37200 },
];

const FII_DII_DATA: FIIDIIFlow[] = Array.from({ length: 10 }, (_, i) => ({
  date: new Date(Date.now() - i * 86400000).toLocaleDateString('en-IN', { month: 'short', day: 'numeric' }),
  fii_buy: 8000 + Math.random() * 4000,
  fii_sell: 7000 + Math.random() * 5000,
  fii_net: (Math.random() - 0.4) * 3000,
  dii_buy: 5000 + Math.random() * 3000,
  dii_sell: 4500 + Math.random() * 3000,
  dii_net: (Math.random() - 0.45) * 2000,
})).reverse();

const OPTION_CHAIN: OptionChainSummary = {
  symbol: 'NIFTY',
  expiry: 'Nov 28, 2024',
  pcr: 0.87,
  max_pain: 24400,
  call_oi_buildup: [24600, 24700, 24800, 24900, 25000],
  put_oi_buildup: [24400, 24300, 24200, 24100, 24000],
  strikes: [24000, 24100, 24200, 24300, 24400, 24500, 24600, 24700, 24800],
  atm_iv: 12.4,
};

function getMarketStatus(): { status: string; color: string; label: string } {
  const now = new Date();
  const h = now.getUTCHours();
  const m = now.getUTCMinutes();
  const totalMin = h * 60 + m;

  // IST = UTC + 5:30
  const istMin = (totalMin + 330) % 1440;
  const istH = Math.floor(istMin / 60);
  const istM = istMin % 60;
  const istTotal = istH * 60 + istM;

  if (istTotal >= 9 * 60 && istTotal < 9 * 60 + 15) {
    return { status: 'pre-open', color: 'text-nexus-yellow', label: 'PRE-OPEN' };
  } else if (istTotal >= 9 * 60 + 15 && istTotal < 15 * 60 + 30) {
    return { status: 'open', color: 'text-nexus-green', label: 'OPEN' };
  } else if (istTotal >= 15 * 60 + 30 && istTotal < 16 * 60) {
    return { status: 'closing', color: 'text-nexus-yellow', label: 'CLOSING SESSION' };
  }
  return { status: 'closed', color: 'text-muted', label: 'CLOSED' };
}

function IndexCard({ index }: { index: typeof INDICES[0] }) {
  const isPositive = index.changePct >= 0;
  return (
    <div className="nexus-card nexus-card-hover p-4">
      <div className="flex items-center justify-between mb-2">
        <span className="text-xs font-medium text-muted uppercase tracking-wider">{index.label}</span>
        {isPositive ? (
          <TrendingUp size={14} className="text-nexus-green" />
        ) : (
          <TrendingDown size={14} className="text-nexus-red" />
        )}
      </div>
      <div className="font-mono text-2xl font-bold text-white">
        {formatNumber(index.price, 2)}
      </div>
      <div className={cn('text-sm font-medium mt-1', isPositive ? 'text-nexus-green' : 'text-nexus-red')}>
        {isPositive ? '+' : ''}{formatNumber(index.change, 2)} ({formatPercent(index.changePct)})
      </div>
      <div className="flex justify-between text-xs text-muted mt-3">
        <span>H: {formatNumber(index.high, 2)}</span>
        <span>L: {formatNumber(index.low, 2)}</span>
      </div>
    </div>
  );
}

function FIIDIIChart({ data }: { data: FIIDIIFlow[] }) {
  return (
    <div className="nexus-card p-4 col-span-2">
      <div className="flex items-center justify-between mb-4">
        <h3 className="font-medium text-sm text-white">FII / DII Flow (₹ Cr)</h3>
        <div className="flex items-center gap-4 text-xs">
          <div className="flex items-center gap-1.5">
            <div className="w-2.5 h-2.5 rounded-sm bg-nexus-blue opacity-80" />
            <span className="text-muted">FII Net</span>
          </div>
          <div className="flex items-center gap-1.5">
            <div className="w-2.5 h-2.5 rounded-sm bg-nexus-yellow opacity-80" />
            <span className="text-muted">DII Net</span>
          </div>
        </div>
      </div>
      <ResponsiveContainer width="100%" height={180}>
        <BarChart data={data} barGap={2} margin={{ top: 4, right: 4, left: -10, bottom: 0 }}>
          <XAxis dataKey="date" tick={{ fontSize: 10, fill: '#6b7280' }} tickLine={false} />
          <YAxis tick={{ fontSize: 10, fill: '#6b7280' }} tickLine={false} tickFormatter={(v) => `${(v / 1000).toFixed(0)}K`} />
          <Tooltip
            contentStyle={{ background: '#1a1a28', border: '1px solid #2a2a3e', borderRadius: '6px', fontSize: '11px' }}
            formatter={(v: number, name: string) => [`₹${formatNumber(v, 0)} Cr`, name === 'fii_net' ? 'FII' : 'DII']}
          />
          <ReferenceLine y={0} stroke="#2a2a3e" strokeDasharray="3 3" />
          <Bar dataKey="fii_net" radius={[2, 2, 0, 0]}>
            {data.map((entry, index) => (
              <Cell key={index} fill={entry.fii_net >= 0 ? '#0088ff' : '#ff4444'} opacity={0.8} />
            ))}
          </Bar>
          <Bar dataKey="dii_net" radius={[2, 2, 0, 0]}>
            {data.map((entry, index) => (
              <Cell key={index} fill={entry.dii_net >= 0 ? '#ffaa00' : '#ff6644'} opacity={0.8} />
            ))}
          </Bar>
        </BarChart>
      </ResponsiveContainer>
      <div className="grid grid-cols-2 gap-4 mt-3 pt-3 border-t border-border text-sm">
        <div>
          <div className="text-xs text-muted">Today FII Net</div>
          <div className={cn('font-mono font-bold', data[data.length-1]?.fii_net >= 0 ? 'text-nexus-blue' : 'text-nexus-red')}>
            ₹{formatNumber(data[data.length-1]?.fii_net ?? 0, 0)} Cr
          </div>
        </div>
        <div>
          <div className="text-xs text-muted">Today DII Net</div>
          <div className={cn('font-mono font-bold', data[data.length-1]?.dii_net >= 0 ? 'text-nexus-yellow' : 'text-nexus-red')}>
            ₹{formatNumber(data[data.length-1]?.dii_net ?? 0, 0)} Cr
          </div>
        </div>
      </div>
    </div>
  );
}

function OptionChainPanel({ data }: { data: OptionChainSummary }) {
  const pcrColor = data.pcr > 1.2 ? 'text-nexus-green' : data.pcr < 0.8 ? 'text-nexus-red' : 'text-nexus-yellow';

  return (
    <div className="nexus-card p-4">
      <div className="flex items-center justify-between mb-4">
        <h3 className="font-medium text-sm text-white">Option Chain — {data.symbol}</h3>
        <span className="text-xs text-muted">{data.expiry}</span>
      </div>

      <div className="grid grid-cols-3 gap-3 mb-4">
        <div className="text-center">
          <div className="text-xs text-muted mb-1">PCR</div>
          <div className={cn('font-mono font-bold text-lg', pcrColor)}>{data.pcr}</div>
          <div className="text-xs text-muted">{data.pcr > 1.0 ? 'Bullish' : 'Bearish'}</div>
        </div>
        <div className="text-center">
          <div className="text-xs text-muted mb-1">Max Pain</div>
          <div className="font-mono font-bold text-lg text-nexus-yellow">{data.max_pain.toLocaleString()}</div>
          <div className="text-xs text-muted">Strike</div>
        </div>
        <div className="text-center">
          <div className="text-xs text-muted mb-1">ATM IV</div>
          <div className="font-mono font-bold text-lg text-nexus-blue">{data.atm_iv}%</div>
          <div className="text-xs text-muted">Implied Vol</div>
        </div>
      </div>

      <div className="space-y-1">
        <div className="text-xs text-muted mb-2">Key OI Strikes</div>
        <div className="flex items-center justify-between">
          <div className="flex-1">
            <div className="text-xs text-nexus-red mb-1">Call OI Buildup</div>
            <div className="flex gap-1.5 flex-wrap">
              {data.call_oi_buildup.map((strike) => (
                <span key={strike} className="text-xs bg-nexus-red/10 text-nexus-red border border-nexus-red/20 rounded px-1.5 py-0.5">
                  {strike.toLocaleString()}
                </span>
              ))}
            </div>
          </div>
          <div className="w-px h-12 bg-border mx-3" />
          <div className="flex-1">
            <div className="text-xs text-nexus-green mb-1">Put OI Buildup</div>
            <div className="flex gap-1.5 flex-wrap">
              {data.put_oi_buildup.map((strike) => (
                <span key={strike} className="text-xs bg-nexus-green/10 text-nexus-green border border-nexus-green/20 rounded px-1.5 py-0.5">
                  {strike.toLocaleString()}
                </span>
              ))}
            </div>
          </div>
        </div>
      </div>
    </div>
  );
}

function MarketBreadth() {
  const advances = 1247;
  const declines = 802;
  const unchanged = 113;
  const total = advances + declines + unchanged;
  const advPct = (advances / total) * 100;
  const decPct = (declines / total) * 100;

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
            <div className="h-full bg-muted rounded-full" style={{ width: `${(unchanged / total) * 100}%` }} />
          </div>
        </div>

        <div className="pt-2 border-t border-border">
          <div className="text-center">
            <div className="text-xs text-muted">A/D Ratio</div>
            <div
              className={cn(
                'text-lg font-mono font-bold mt-1',
                advances > declines ? 'text-nexus-green' : 'text-nexus-red'
              )}
            >
              {(advances / declines).toFixed(2)}
            </div>
            <div className="text-xs text-muted">
              {advances > declines ? 'Broad Buying' : 'Broad Selling'}
            </div>
          </div>
        </div>

        <div className="pt-2 border-t border-border grid grid-cols-2 gap-2 text-xs">
          <div>
            <div className="text-muted">52W High</div>
            <div className="text-nexus-green font-mono">287</div>
          </div>
          <div>
            <div className="text-muted">52W Low</div>
            <div className="text-nexus-red font-mono">43</div>
          </div>
        </div>
      </div>
    </div>
  );
}

export default function IndianStocksPage() {
  const { signalFeed, activeTrades } = useNexusStore();
  const [signals, setSignals] = useState<Signal[]>([]);
  const [trades, setTrades] = useState<Trade[]>([]);
  const marketStatus = getMarketStatus();

  useEffect(() => {
    const load = async () => {
      const [sigs, trds] = await Promise.all([
        getRecentSignals('indian_stocks', 20),
        getActiveTrades(),
      ]);
      setSignals(sigs);
      setTrades(trds.filter((t) => t.market === 'indian_stocks'));
    };
    load();
  }, []);

  const indianSignals = [...signals, ...signalFeed.filter((s) => s.market === 'indian_stocks')];
  const indianTrades = [...trades, ...activeTrades.filter((t) => t.market === 'indian_stocks')];

  return (
    <div className="space-y-4">
      {/* Header: Market status + IST time */}
      <div className="flex items-center gap-4">
        <div className={cn('flex items-center gap-2 px-3 py-1.5 rounded-lg border', marketStatus.status === 'open' ? 'bg-nexus-green/5 border-nexus-green/20' : 'bg-border border-border')}>
          <span className={cn('status-dot', marketStatus.status === 'open' ? 'active' : marketStatus.status === 'pre-open' ? 'warning' : 'inactive')} />
          <span className={cn('text-sm font-bold', marketStatus.color)}>
            NSE/BSE {marketStatus.label}
          </span>
        </div>
        <div className="flex items-center gap-2 text-sm text-muted">
          <Clock size={14} />
          <ISTClock />
        </div>
      </div>

      {/* Index cards */}
      <div className="grid grid-cols-4 gap-4">
        {INDICES.map((index) => (
          <IndexCard key={index.symbol} index={index} />
        ))}
      </div>

      {/* FII/DII + option chain + breadth */}
      <div className="grid grid-cols-4 gap-4">
        <FIIDIIChart data={FII_DII_DATA} />
        <OptionChainPanel data={OPTION_CHAIN} />
        <MarketBreadth />
      </div>

      {/* Trades + signals */}
      <div className="grid grid-cols-2 gap-4">
        <TradeTable
          trades={indianTrades}
          title="Active Indian Stock Trades"
        />
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
        hour: '2-digit',
        minute: '2-digit',
        second: '2-digit',
        hour12: false,
      }).format(new Date());
      setTime(`${ist} IST`);
    };
    update();
    const t = setInterval(update, 1000);
    return () => clearInterval(t);
  }, []);

  return <span className="font-mono">{time}</span>;
}
