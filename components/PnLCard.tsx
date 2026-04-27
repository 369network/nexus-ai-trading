'use client';

import { useEffect, useState, useRef } from 'react';
import { TrendingUp, TrendingDown, Activity } from 'lucide-react';
import { useNexusStore } from '@/lib/store';
import { cn, formatCurrency, formatPercent } from '@/lib/utils';

// Tiny sparkline using SVG
function MiniSparkline({
  data,
  color,
  height = 36,
  width = 140,
}: {
  data: number[];
  color: string;
  height?: number;
  width?: number;
}) {
  if (data.length < 2) return null;

  const min = Math.min(...data);
  const max = Math.max(...data);
  const range = max - min || 1;

  const points = data.map((v, i) => {
    const x = (i / (data.length - 1)) * width;
    const y = height - ((v - min) / range) * height;
    return `${x},${y}`;
  });

  const pathD = `M ${points.join(' L ')}`;
  const areaD = `M 0,${height} L ${points.join(' L ')} L ${width},${height} Z`;

  return (
    <svg width={width} height={height} className="overflow-visible">
      <defs>
        <linearGradient id="sparkGrad" x1="0" y1="0" x2="0" y2="1">
          <stop offset="0%" stopColor={color} stopOpacity="0.3" />
          <stop offset="100%" stopColor={color} stopOpacity="0" />
        </linearGradient>
      </defs>
      <path d={areaD} fill="url(#sparkGrad)" />
      <path d={pathD} fill="none" stroke={color} strokeWidth="1.5" strokeLinecap="round" />
    </svg>
  );
}

// Animated counter hook
function useAnimatedValue(targetValue: number, duration: number = 300): number {
  const [displayValue, setDisplayValue] = useState(targetValue);
  const prevValue = useRef(targetValue);
  const frameRef = useRef<number>();

  useEffect(() => {
    const start = prevValue.current;
    const end = targetValue;
    const startTime = performance.now();

    const animate = (currentTime: number) => {
      const elapsed = currentTime - startTime;
      const progress = Math.min(elapsed / duration, 1);
      const eased = 1 - Math.pow(1 - progress, 3); // Cubic ease-out
      const current = start + (end - start) * eased;
      setDisplayValue(current);

      if (progress < 1) {
        frameRef.current = requestAnimationFrame(animate);
      } else {
        prevValue.current = end;
      }
    };

    frameRef.current = requestAnimationFrame(animate);
    return () => {
      if (frameRef.current) cancelAnimationFrame(frameRef.current);
    };
  }, [targetValue, duration]);

  return displayValue;
}

export function PnLCard() {
  const { portfolioState } = useNexusStore();

  const equity = portfolioState.equity || 127840;
  const dailyPnl = portfolioState.dailyPnl || 1240;
  const dailyPnlPct = portfolioState.dailyPnlPct || 0.97;
  const drawdown = portfolioState.drawdown || -3.2;
  const openPositions = portfolioState.openPositions || 4;
  const sparkData = portfolioState.equityCurve.length
    ? portfolioState.equityCurve.map((p) => p.value)
    : Array.from({ length: 24 }, (_, i) => 120000 + Math.sin(i * 0.3) * 3000 + i * 200);

  const animatedEquity = useAnimatedValue(equity);
  const animatedDailyPnl = useAnimatedValue(dailyPnl);

  const isPositive = dailyPnl >= 0;
  const isDrawdownDanger = drawdown < -10;

  return (
    <div
      className={cn(
        'nexus-card p-5 h-full flex flex-col',
        isPositive ? 'border-nexus-green/20' : 'border-nexus-red/20'
      )}
    >
      <div className="flex items-center justify-between mb-4">
        <div className="flex items-center gap-2">
          <Activity size={14} className="text-nexus-blue" />
          <span className="text-xs text-muted uppercase tracking-wider">Portfolio</span>
        </div>
        <span
          className={cn(
            'badge text-xs',
            openPositions > 0
              ? 'bg-nexus-blue/10 text-nexus-blue border border-nexus-blue/20'
              : 'bg-muted/10 text-muted'
          )}
        >
          {openPositions} Open
        </span>
      </div>

      {/* Equity */}
      <div className="mb-4">
        <div className="text-xs text-muted mb-1">Total Equity</div>
        <div className="font-mono text-3xl font-bold text-white tracking-tight">
          {formatCurrency(animatedEquity, 'USD', false)}
        </div>
      </div>

      {/* Daily P&L */}
      <div
        className={cn(
          'flex items-center justify-between p-3 rounded-lg mb-4',
          isPositive ? 'bg-nexus-green/5 border border-nexus-green/15' : 'bg-nexus-red/5 border border-nexus-red/15'
        )}
      >
        <div>
          <div className="text-xs text-muted mb-0.5">Today's P&L</div>
          <div
            className={cn(
              'font-mono text-xl font-bold',
              isPositive ? 'text-nexus-green' : 'text-nexus-red'
            )}
          >
            {isPositive ? '+' : ''}{formatCurrency(animatedDailyPnl)}
          </div>
        </div>
        <div className="text-right">
          <div className="flex items-center gap-1">
            {isPositive ? (
              <TrendingUp size={20} className="text-nexus-green" />
            ) : (
              <TrendingDown size={20} className="text-nexus-red" />
            )}
            <span
              className={cn(
                'font-mono text-lg font-bold',
                isPositive ? 'text-nexus-green' : 'text-nexus-red'
              )}
            >
              {isPositive ? '+' : ''}{formatPercent(dailyPnlPct)}
            </span>
          </div>
        </div>
      </div>

      {/* Drawdown indicator */}
      <div
        className={cn(
          'flex items-center justify-between p-2 rounded-lg border mb-4',
          isDrawdownDanger
            ? 'bg-nexus-red/5 border-nexus-red/20'
            : 'bg-border border-border'
        )}
      >
        <div>
          <div className="text-xs text-muted">Drawdown</div>
          <div
            className={cn(
              'font-mono text-sm font-bold mt-0.5',
              isDrawdownDanger ? 'text-nexus-red' : 'text-nexus-yellow'
            )}
          >
            {formatPercent(drawdown)}
          </div>
        </div>
        <div className="w-24 h-2 bg-border-bright rounded-full overflow-hidden">
          <div
            className={cn('h-full rounded-full', isDrawdownDanger ? 'bg-nexus-red' : 'bg-nexus-yellow')}
            style={{ width: `${Math.min(100, Math.abs(drawdown) / 15 * 100)}%` }}
          />
        </div>
      </div>

      {/* Sparkline */}
      <div className="mt-auto">
        <div className="text-xs text-muted mb-2">Today's equity curve</div>
        <div className="flex justify-center">
          <MiniSparkline
            data={sparkData.slice(-24)}
            color={isPositive ? '#00ff88' : '#ff4444'}
            height={40}
            width={180}
          />
        </div>
      </div>
    </div>
  );
}
