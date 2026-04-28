'use client';

import { useEffect, useRef, useState } from 'react';
import { TrendingUp, TrendingDown } from 'lucide-react';
import { useLivePrices, type LivePrice } from '@/hooks/useLivePrices';

// Display order
const DISPLAY_ORDER = [
  'BTCUSDT',
  'ETHUSDT',
  'SOLUSDT',
  'XAU',
  'XAG',
  'EURUSD',
  'GBPUSD',
  'USDJPY',
];

function formatPrice(item: LivePrice): string {
  const { symbol, price } = item;

  if (symbol === 'BTCUSDT') {
    return '$' + price.toLocaleString('en-US', { minimumFractionDigits: 2, maximumFractionDigits: 2 });
  }
  if (symbol === 'ETHUSDT' || symbol === 'SOLUSDT') {
    return '$' + price.toLocaleString('en-US', { minimumFractionDigits: 2, maximumFractionDigits: 2 });
  }
  if (symbol === 'XAU') {
    return '$' + price.toLocaleString('en-US', { minimumFractionDigits: 1, maximumFractionDigits: 1 });
  }
  if (symbol === 'XAG') {
    return '$' + price.toLocaleString('en-US', { minimumFractionDigits: 2, maximumFractionDigits: 2 });
  }
  if (symbol === 'USDJPY') {
    return price.toLocaleString('en-US', { minimumFractionDigits: 2, maximumFractionDigits: 2 });
  }
  // EUR/USD, GBP/USD — 5 decimal places
  return price.toFixed(5);
}

function formatChange(change: number): string {
  const sign = change >= 0 ? '+' : '';
  return `${sign}${change.toFixed(2)}%`;
}

// Individual price item with flash-on-change animation
function PriceItem({ item }: { item: LivePrice }) {
  const [flashClass, setFlashClass] = useState('');
  const prevPriceRef = useRef<number | null>(null);

  useEffect(() => {
    if (prevPriceRef.current === null) {
      prevPriceRef.current = item.price;
      return;
    }

    if (item.price === prevPriceRef.current) return;

    const direction = item.price > prevPriceRef.current ? 'flash-up' : 'flash-down';
    prevPriceRef.current = item.price;

    setFlashClass(direction);
    const timer = setTimeout(() => setFlashClass(''), 600);
    return () => clearTimeout(timer);
  }, [item.price]);

  const changePositive = item.change24h >= 0;
  const changeColor = changePositive ? 'text-emerald-400' : 'text-red-400';
  const changeColorMuted = changePositive ? 'text-emerald-400/70' : 'text-red-400/70';

  return (
    <div className="flex items-center gap-1.5 flex-shrink-0">
      {/* Symbol label */}
      <span className="text-[10px] text-muted-foreground/60 uppercase tracking-wider leading-none">
        {item.label}
      </span>

      {/* Price */}
      <span
        className={[
          'font-mono text-xs font-medium text-foreground leading-none transition-colors duration-150',
          flashClass === 'flash-up' ? 'text-emerald-300' : '',
          flashClass === 'flash-down' ? 'text-red-300' : '',
        ]
          .filter(Boolean)
          .join(' ')}
      >
        {formatPrice(item)}
      </span>

      {/* 24h change */}
      <span className={`flex items-center gap-0.5 text-[10px] font-medium leading-none ${changeColor}`}>
        {item.change24h !== 0 && (
          changePositive
            ? <TrendingUp size={9} className={changeColorMuted} />
            : <TrendingDown size={9} className={changeColorMuted} />
        )}
        {formatChange(item.change24h)}
      </span>
    </div>
  );
}

function Separator() {
  return (
    <span className="text-muted-foreground/30 text-xs flex-shrink-0 select-none">·</span>
  );
}

function LiveDot() {
  return (
    <div className="flex items-center gap-1.5 flex-shrink-0 pr-1">
      <span className="relative flex h-1.5 w-1.5">
        <span className="animate-ping absolute inline-flex h-full w-full rounded-full bg-emerald-400 opacity-75" />
        <span className="relative inline-flex rounded-full h-1.5 w-1.5 bg-emerald-500" />
      </span>
      <span className="text-[10px] font-bold tracking-widest text-emerald-500 uppercase leading-none">
        LIVE
      </span>
    </div>
  );
}

export function LivePriceTicker() {
  const prices = useLivePrices();

  const orderedItems = DISPLAY_ORDER
    .map((key) => prices[key])
    .filter((item): item is LivePrice => Boolean(item));

  return (
    <div className="h-8 bg-card border-b border-border flex items-center overflow-hidden">
      <div className="flex items-center h-full px-4 gap-3">
        {/* LIVE indicator */}
        <LiveDot />

        {/* Divider between indicator and prices */}
        <div className="w-px h-4 bg-border/60 flex-shrink-0" />

        {/* Price items */}
        {orderedItems.length === 0 ? (
          <span className="text-[10px] text-muted-foreground/40 font-mono animate-pulse">
            Connecting to markets...
          </span>
        ) : (
          <div className="flex items-center gap-3 overflow-x-auto scrollbar-none">
            {orderedItems.map((item, idx) => (
              <div key={item.symbol} className="flex items-center gap-3">
                <PriceItem item={item} />
                {idx < orderedItems.length - 1 && <Separator />}
              </div>
            ))}
          </div>
        )}
      </div>
    </div>
  );
}
