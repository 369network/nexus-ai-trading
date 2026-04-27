'use client';

import { useEffect, useState } from 'react';
import { Timer, TrendingUp, TrendingDown, BarChart2 } from 'lucide-react';
import {
  BarChart,
  Bar,
  XAxis,
  YAxis,
  ResponsiveContainer,
  Tooltip,
  Cell,
} from 'recharts';
import { CandlestickChart } from '@/components/charts/CandlestickChart';
import { SignalFeed } from '@/components/SignalFeed';
import { useNexusStore } from '@/lib/store';
import { getRecentSignals } from '@/lib/supabase';
import { cn, formatCurrency, formatPercent } from '@/lib/utils';
import type { Signal } from '@/lib/types';

const COMMODITIES = [
  { symbol: 'XAUUSD', label: 'Gold', unit: 'troy oz', price: 2374.50, change: 0.87, icon: '🥇' },
  { symbol: 'XAGUSD', label: 'Silver', unit: 'troy oz', price: 29.84, change: 1.24, icon: '🥈' },
  { symbol: 'WTIUSD', label: 'WTI Oil', unit: 'barrel', price: 78.42, change: -0.53, icon: '🛢️' },
  { symbol: 'NATGAS', label: 'Nat. Gas', unit: 'MMBtu', price: 2.18, change: -1.82, icon: '🔥' },
];

// Gold/Silver ratio
const GOLD_SILVER_RATIO = COMMODITIES[0].price / COMMODITIES[1].price; // ~79.5

// Monthly seasonal data (historical avg returns)
const SEASONAL_MONTHLY = [
  { month: 'Jan', gold: 1.2, silver: 1.8, oil: -2.1 },
  { month: 'Feb', gold: 0.8, silver: 1.1, oil: 1.4 },
  { month: 'Mar', gold: -0.3, silver: -0.7, oil: 2.8 },
  { month: 'Apr', gold: 1.5, silver: 2.2, oil: 3.1 },
  { month: 'May', gold: 0.4, silver: -0.2, oil: -1.2 },
  { month: 'Jun', gold: -0.8, silver: -1.4, oil: -0.8 },
  { month: 'Jul', gold: 0.6, silver: 0.9, oil: 1.7 },
  { month: 'Aug', gold: 1.8, silver: 2.4, oil: -0.4 },
  { month: 'Sep', gold: -1.2, silver: -2.1, oil: -2.8 },
  { month: 'Oct', gold: 2.1, silver: 3.2, oil: -1.1 },
  { month: 'Nov', gold: 0.9, silver: 1.2, oil: -3.2 },
  { month: 'Dec', gold: 1.4, silver: 1.8, oil: 0.6 },
];

function CommodityCard({
  commodity,
  isSelected,
  onClick,
}: {
  commodity: typeof COMMODITIES[0];
  isSelected: boolean;
  onClick: () => void;
}) {
  return (
    <button
      onClick={onClick}
      className={cn(
        'nexus-card nexus-card-hover p-4 text-left w-full transition-all',
        isSelected && 'border-nexus-blue/40 glow-blue'
      )}
    >
      <div className="flex items-center justify-between mb-3">
        <div className="flex items-center gap-2">
          <span className="text-xl">{commodity.icon}</span>
          <div>
            <div className="font-medium text-sm text-white">{commodity.label}</div>
            <div className="text-xs text-muted">Per {commodity.unit}</div>
          </div>
        </div>
        {commodity.change >= 0 ? (
          <TrendingUp size={16} className="text-nexus-green" />
        ) : (
          <TrendingDown size={16} className="text-nexus-red" />
        )}
      </div>
      <div className="font-mono text-xl font-bold text-white">
        {formatCurrency(commodity.price)}
      </div>
      <div
        className={cn(
          'text-sm font-medium mt-1',
          commodity.change >= 0 ? 'text-nexus-green' : 'text-nexus-red'
        )}
      >
        {formatPercent(commodity.change)} today
      </div>
    </button>
  );
}

function GoldSilverRatioGauge({ ratio }: { ratio: number }) {
  // Historical range: 40 (cheap gold) to 120 (expensive gold)
  const min = 40;
  const max = 120;
  const pct = ((ratio - min) / (max - min)) * 100;
  const signal =
    ratio > 85 ? 'BUY SILVER' : ratio < 55 ? 'BUY GOLD' : 'NEUTRAL';
  const signalColor =
    ratio > 85 ? 'text-nexus-green' : ratio < 55 ? 'text-nexus-yellow' : 'text-muted';

  return (
    <div className="nexus-card p-4">
      <h3 className="font-medium text-sm text-white mb-4">Gold/Silver Ratio</h3>
      <div className="text-3xl font-mono font-bold text-nexus-yellow text-center mb-4">
        {ratio.toFixed(1)}
      </div>
      <div className="relative mb-2">
        <div className="h-3 rounded-full bg-gradient-to-r from-nexus-green via-nexus-yellow to-nexus-red" />
        <div
          className="absolute top-1/2 -translate-y-1/2 w-3 h-5 bg-white rounded-sm shadow-lg"
          style={{ left: `calc(${pct}% - 6px)` }}
        />
      </div>
      <div className="flex justify-between text-xs text-muted mb-3">
        <span>40 (Buy Gold)</span>
        <span>80 (Neutral)</span>
        <span>120 (Buy Silver)</span>
      </div>
      <div className={cn('text-center text-sm font-bold mt-2', signalColor)}>
        Signal: {signal}
      </div>
      <div className="mt-3 pt-3 border-t border-border text-xs text-muted space-y-1">
        <div className="flex justify-between">
          <span>10Y Avg</span>
          <span className="text-white">77.4</span>
        </div>
        <div className="flex justify-between">
          <span>COVID Peak</span>
          <span className="text-white">124.1</span>
        </div>
        <div className="flex justify-between">
          <span>2011 Low</span>
          <span className="text-white">31.7</span>
        </div>
      </div>
    </div>
  );
}

function EIACountdown() {
  const [timeLeft, setTimeLeft] = useState({ h: 0, m: 0, s: 0 });

  useEffect(() => {
    // EIA petroleum report: every Wednesday at 10:30 AM ET
    const updateCountdown = () => {
      const now = new Date();
      const nextWed = new Date();
      const day = now.getDay();
      const daysUntilWed = day <= 3 ? 3 - day : 10 - day;
      nextWed.setDate(now.getDate() + daysUntilWed);
      nextWed.setHours(15, 30, 0, 0); // 10:30 ET = 15:30 UTC

      if (nextWed <= now) nextWed.setDate(nextWed.getDate() + 7);

      const diff = nextWed.getTime() - now.getTime();
      const h = Math.floor(diff / 3600000);
      const m = Math.floor((diff % 3600000) / 60000);
      const s = Math.floor((diff % 60000) / 1000);
      setTimeLeft({ h, m, s });
    };

    updateCountdown();
    const t = setInterval(updateCountdown, 1000);
    return () => clearInterval(t);
  }, []);

  return (
    <div className="nexus-card p-4">
      <div className="flex items-center gap-2 mb-3">
        <Timer size={14} className="text-nexus-yellow" />
        <h3 className="font-medium text-sm text-white">EIA Petroleum Report</h3>
      </div>
      <p className="text-xs text-muted mb-4">Next release: Wednesday 10:30 AM ET</p>
      <div className="flex items-center justify-center gap-3">
        {[
          { label: 'Hours', value: timeLeft.h },
          { label: 'Min', value: timeLeft.m },
          { label: 'Sec', value: timeLeft.s },
        ].map((unit) => (
          <div key={unit.label} className="text-center">
            <div className="bg-border rounded-lg px-3 py-2 font-mono text-2xl font-bold text-nexus-yellow w-16">
              {String(unit.value).padStart(2, '0')}
            </div>
            <div className="text-xs text-muted mt-1">{unit.label}</div>
          </div>
        ))}
      </div>
      <div className="mt-4 pt-3 border-t border-border space-y-2">
        <div className="flex justify-between text-xs">
          <span className="text-muted">Prev Crude Build</span>
          <span className="text-nexus-red font-mono">+3.44M bbl</span>
        </div>
        <div className="flex justify-between text-xs">
          <span className="text-muted">Forecast</span>
          <span className="text-nexus-green font-mono">-2.10M bbl</span>
        </div>
      </div>
    </div>
  );
}

function SeasonalHeatmap() {
  const currentMonth = new Date().getMonth();
  const months = SEASONAL_MONTHLY;
  const maxAbs = 4;

  const getColor = (val: number) => {
    const intensity = Math.abs(val) / maxAbs;
    if (val > 0) return `rgba(0, 255, 136, ${0.15 + intensity * 0.65})`;
    return `rgba(255, 68, 68, ${0.15 + intensity * 0.65})`;
  };

  return (
    <div className="nexus-card p-4 col-span-2">
      <div className="flex items-center justify-between mb-4">
        <h3 className="font-medium text-sm text-white">Seasonal Patterns (Avg Monthly %)</h3>
        <BarChart2 size={14} className="text-nexus-purple" />
      </div>
      <div className="overflow-x-auto">
        <table className="w-full text-xs">
          <thead>
            <tr>
              <th className="text-left text-muted pb-2 font-normal">Asset</th>
              {months.map((m, i) => (
                <th
                  key={m.month}
                  className={cn(
                    'text-center pb-2 font-normal w-10',
                    i === currentMonth ? 'text-nexus-blue font-bold' : 'text-muted'
                  )}
                >
                  {m.month}
                </th>
              ))}
            </tr>
          </thead>
          <tbody>
            {(['gold', 'silver', 'oil'] as const).map((asset) => (
              <tr key={asset}>
                <td className="py-1 pr-3 text-white capitalize font-medium">{asset}</td>
                {months.map((m, i) => {
                  const val = m[asset];
                  return (
                    <td
                      key={m.month}
                      className="text-center py-1 px-0.5"
                    >
                      <div
                        className={cn(
                          'rounded text-xs font-mono py-1 mx-0.5',
                          i === currentMonth && 'ring-1 ring-nexus-blue'
                        )}
                        style={{ backgroundColor: getColor(val), color: val > 0 ? '#00ff88' : '#ff4444' }}
                      >
                        {val > 0 ? '+' : ''}{val.toFixed(1)}
                      </div>
                    </td>
                  );
                })}
              </tr>
            ))}
          </tbody>
        </table>
      </div>
    </div>
  );
}

export default function CommoditiesPage() {
  const { signalFeed } = useNexusStore();
  const [selectedCommodity, setSelectedCommodity] = useState('XAUUSD');
  const [signals, setSignals] = useState<Signal[]>([]);

  useEffect(() => {
    getRecentSignals('commodities', 20).then(setSignals);
  }, []);

  const commoditySignals = [...signals, ...signalFeed.filter((s) => s.market === 'commodities')];

  return (
    <div className="space-y-4">
      {/* Price cards */}
      <div className="grid grid-cols-4 gap-4">
        {COMMODITIES.map((c) => (
          <CommodityCard
            key={c.symbol}
            commodity={c}
            isSelected={selectedCommodity === c.symbol}
            onClick={() => setSelectedCommodity(c.symbol)}
          />
        ))}
      </div>

      {/* Chart + ratio gauge + countdown */}
      <div className="grid grid-cols-4 gap-4">
        <div className="col-span-2 nexus-card overflow-hidden" style={{ height: 360 }}>
          <CandlestickChart
            symbol={selectedCommodity}
            market="commodities"
            signals={commoditySignals.filter((s) => s.symbol === selectedCommodity)}
          />
        </div>
        <GoldSilverRatioGauge ratio={GOLD_SILVER_RATIO} />
        <EIACountdown />
      </div>

      {/* Seasonal heatmap + signal feed */}
      <div className="grid grid-cols-3 gap-4">
        <SeasonalHeatmap />
        <SignalFeed market="commodities" limit={8} />
      </div>
    </div>
  );
}
