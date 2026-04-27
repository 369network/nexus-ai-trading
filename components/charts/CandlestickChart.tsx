'use client';

import { useEffect, useRef, useState } from 'react';
import {
  createChart,
  IChartApi,
  ISeriesApi,
  ColorType,
  CrosshairMode,
  SeriesMarker,
  Time,
  CandlestickData,
  HistogramData,
  LineData,
} from 'lightweight-charts';
import { getMarketData, subscribeToMarketData } from '@/lib/supabase';
import { cn } from '@/lib/utils';
import type { Signal, Market, Timeframe, MarketData } from '@/lib/types';

interface CandlestickChartProps {
  symbol: string;
  market: Market;
  signals?: Signal[];
  defaultTimeframe?: Timeframe;
  height?: number;
}

interface OverlayState {
  sma20: boolean;
  sma50: boolean;
  sma200: boolean;
  ema9: boolean;
  ema21: boolean;
  bb: boolean;
  signals: boolean;
  volume: boolean;
}

const TIMEFRAMES: Timeframe[] = ['1m', '5m', '15m', '1h', '4h', '1d'];

function generateCandles(symbol: string, timeframe: Timeframe, count: number = 200): CandlestickData[] {
  const basePrice: Record<string, number> = {
    BTCUSDT: 67000, ETHUSDT: 3500, SOLUSDT: 175,
    EURUSD: 1.085, GBPUSD: 1.272, USDJPY: 149.8, AUDUSD: 0.648,
    USDCHF: 0.899, USDCAD: 1.367, NZDUSD: 0.596, EURGBP: 0.852,
    XAUUSD: 2374, XAGUSD: 29.8, WTIUSD: 78.4, NATGAS: 2.18,
    NIFTY50: 24500, BANKNIFTY: 52800, SPX: 5842, NDX: 20240,
  };

  const tfMinutes: Record<Timeframe, number> = {
    '1m': 1, '5m': 5, '15m': 15, '1h': 60, '4h': 240, '1d': 1440,
  };

  const base = basePrice[symbol] ?? 100;
  const volatility = base * 0.001;
  const minuteMs = tfMinutes[timeframe] * 60 * 1000;

  const candles: CandlestickData[] = [];
  let price = base;
  const startTime = Date.now() - count * minuteMs;

  for (let i = 0; i < count; i++) {
    const open = price;
    const change = (Math.random() - 0.49) * volatility * 2;
    const high = Math.max(open, open + Math.abs(change) + Math.random() * volatility);
    const low = Math.min(open, open - Math.abs(change) - Math.random() * volatility);
    const close = open + change;

    candles.push({
      time: Math.floor((startTime + i * minuteMs) / 1000) as Time,
      open,
      high,
      low,
      close,
    });

    price = close;
  }

  return candles;
}

function generateVolume(candles: CandlestickData[]): HistogramData[] {
  const avgVol = 1000000;
  return candles.map((c) => ({
    time: c.time,
    value: avgVol * (0.5 + Math.random()),
    color: (c.close as number) >= (c.open as number)
      ? 'rgba(0, 255, 136, 0.4)'
      : 'rgba(255, 68, 68, 0.4)',
  }));
}

function calculateSMA(candles: CandlestickData[], period: number): LineData[] {
  const result: LineData[] = [];
  for (let i = period - 1; i < candles.length; i++) {
    const sum = candles.slice(i - period + 1, i + 1).reduce((s, c) => s + (c.close as number), 0);
    result.push({ time: candles[i].time, value: sum / period });
  }
  return result;
}

function calculateEMA(candles: CandlestickData[], period: number): LineData[] {
  const k = 2 / (period + 1);
  let ema = candles[0].close as number;
  const result: LineData[] = [];
  for (let i = 0; i < candles.length; i++) {
    ema = i === 0 ? (candles[0].close as number) : (candles[i].close as number) * k + ema * (1 - k);
    if (i >= period - 1) result.push({ time: candles[i].time, value: ema });
  }
  return result;
}

function calculateBB(candles: CandlestickData[], period = 20, mult = 2) {
  const sma = calculateSMA(candles, period);
  return sma.map((point, i) => {
    const slice = candles.slice(i, i + period);
    const variance = slice.reduce((v, c) => v + Math.pow((c.close as number) - point.value, 2), 0) / period;
    const std = Math.sqrt(variance);
    return { time: point.time, upper: point.value + std * mult, lower: point.value - std * mult };
  });
}

export function CandlestickChart({
  symbol,
  market,
  signals = [],
  defaultTimeframe = '1h',
  height = 380,
}: CandlestickChartProps) {
  const chartContainerRef = useRef<HTMLDivElement>(null);
  const chartRef = useRef<IChartApi | null>(null);
  const candleSeriesRef = useRef<ISeriesApi<'Candlestick'> | null>(null);
  const volumeSeriesRef = useRef<ISeriesApi<'Histogram'> | null>(null);
  const sma20Ref = useRef<ISeriesApi<'Line'> | null>(null);
  const sma50Ref = useRef<ISeriesApi<'Line'> | null>(null);
  const sma200Ref = useRef<ISeriesApi<'Line'> | null>(null);
  const ema9Ref = useRef<ISeriesApi<'Line'> | null>(null);
  const ema21Ref = useRef<ISeriesApi<'Line'> | null>(null);
  const bbUpperRef = useRef<ISeriesApi<'Line'> | null>(null);
  const bbLowerRef = useRef<ISeriesApi<'Line'> | null>(null);

  const [timeframe, setTimeframe] = useState<Timeframe>(defaultTimeframe);
  const [overlays, setOverlays] = useState<OverlayState>({
    sma20: false, sma50: true, sma200: false,
    ema9: false, ema21: false, bb: false,
    signals: true, volume: true,
  });
  const [ohlcv, setOhlcv] = useState({ o: 0, h: 0, l: 0, c: 0 });
  const [candles, setCandles] = useState<CandlestickData[]>([]);

  const toggleOverlay = (key: keyof OverlayState) => {
    setOverlays((prev) => ({ ...prev, [key]: !prev[key] }));
  };

  // Initialize chart once
  useEffect(() => {
    if (!chartContainerRef.current) return;

    const chart = createChart(chartContainerRef.current, {
      layout: {
        background: { type: ColorType.Solid, color: '#12121a' },
        textColor: '#9ca3af',
        fontSize: 11,
        fontFamily: 'Inter, sans-serif',
      },
      grid: {
        vertLines: { color: '#1a1a28', style: 1 },
        horzLines: { color: '#1a1a28', style: 1 },
      },
      crosshair: {
        mode: CrosshairMode.Normal,
        vertLine: { color: '#2a2a3e', labelBackgroundColor: '#1a1a28' },
        horzLine: { color: '#2a2a3e', labelBackgroundColor: '#1a1a28' },
      },
      rightPriceScale: { borderColor: '#1e1e2e', textColor: '#9ca3af' },
      timeScale: { borderColor: '#1e1e2e', timeVisible: true, secondsVisible: false },
      width: chartContainerRef.current.clientWidth,
      height,
    });

    chartRef.current = chart;

    const candleSeries = chart.addCandlestickSeries({
      upColor: '#00ff88',
      downColor: '#ff4444',
      borderUpColor: '#00ff88',
      borderDownColor: '#ff4444',
      wickUpColor: '#00ff88',
      wickDownColor: '#ff4444',
    });
    candleSeriesRef.current = candleSeries;

    const volumeSeries = chart.addHistogramSeries({
      priceFormat: { type: 'volume' },
      priceScaleId: 'volume',
      lastValueVisible: false,
      priceLineVisible: false,
    });
    chart.priceScale('volume').applyOptions({
      scaleMargins: { top: 0.8, bottom: 0 },
      visible: false,
    });
    volumeSeriesRef.current = volumeSeries;

    sma50Ref.current = chart.addLineSeries({ color: '#ffaa00', lineWidth: 1, priceLineVisible: false, lastValueVisible: false, visible: true });
    sma20Ref.current = chart.addLineSeries({ color: '#00ccff', lineWidth: 1, priceLineVisible: false, lastValueVisible: false, visible: false });
    sma200Ref.current = chart.addLineSeries({ color: '#ff8844', lineWidth: 1, priceLineVisible: false, lastValueVisible: false, visible: false });
    ema9Ref.current = chart.addLineSeries({ color: '#8844ff', lineWidth: 1, priceLineVisible: false, lastValueVisible: false, visible: false });
    ema21Ref.current = chart.addLineSeries({ color: '#ff44aa', lineWidth: 1, priceLineVisible: false, lastValueVisible: false, visible: false });
    bbUpperRef.current = chart.addLineSeries({ color: 'rgba(0,136,255,0.5)', lineWidth: 1, lineStyle: 2, priceLineVisible: false, lastValueVisible: false, visible: false });
    bbLowerRef.current = chart.addLineSeries({ color: 'rgba(0,136,255,0.5)', lineWidth: 1, lineStyle: 2, priceLineVisible: false, lastValueVisible: false, visible: false });

    chart.subscribeCrosshairMove((param) => {
      if (param.seriesData) {
        const data = param.seriesData.get(candleSeries) as CandlestickData | undefined;
        if (data) {
          setOhlcv({ o: data.open as number, h: data.high as number, l: data.low as number, c: data.close as number });
        }
      }
    });

    const resizeObserver = new ResizeObserver(() => {
      if (chartContainerRef.current) {
        chart.applyOptions({ width: chartContainerRef.current.clientWidth });
      }
    });
    resizeObserver.observe(chartContainerRef.current);

    return () => {
      resizeObserver.disconnect();
      chart.remove();
    };
  }, []);

  // Load data when symbol/timeframe changes
  useEffect(() => {
    const load = async () => {
      let data = await getMarketData(symbol, timeframe, 200);
      if (!data || data.length === 0) {
        setCandles(generateCandles(symbol, timeframe, 200));
        return;
      }
      setCandles(
        data.map((d) => ({
          time: Math.floor(new Date(d.timestamp).getTime() / 1000) as Time,
          open: d.open, high: d.high, low: d.low, close: d.close,
        }))
      );
    };
    load();
  }, [symbol, timeframe]);

  // Populate series when candles change
  useEffect(() => {
    if (!candleSeriesRef.current || !candles.length) return;

    candleSeriesRef.current.setData(candles);
    volumeSeriesRef.current?.setData(generateVolume(candles));

    if (candles.length >= 20) sma20Ref.current?.setData(calculateSMA(candles, 20));
    if (candles.length >= 50) sma50Ref.current?.setData(calculateSMA(candles, 50));
    if (candles.length >= 200) sma200Ref.current?.setData(calculateSMA(candles, 200));
    if (candles.length >= 9) ema9Ref.current?.setData(calculateEMA(candles, 9));
    if (candles.length >= 21) ema21Ref.current?.setData(calculateEMA(candles, 21));

    if (candles.length >= 20) {
      const bb = calculateBB(candles);
      bbUpperRef.current?.setData(bb.map((b) => ({ time: b.time, value: b.upper })));
      bbLowerRef.current?.setData(bb.map((b) => ({ time: b.time, value: b.lower })));
    }

    // Signal markers
    if (signals.length && candleSeriesRef.current) {
      const markers: SeriesMarker<Time>[] = signals
        .filter((s) => s.symbol === symbol)
        .map((sig) => ({
          time: Math.floor(new Date(sig.created_at).getTime() / 1000) as Time,
          position: sig.direction === 'LONG' ? 'belowBar' : 'aboveBar',
          color: sig.direction === 'LONG' ? '#00ff88' : sig.direction === 'SHORT' ? '#ff4444' : '#ffaa00',
          shape: sig.direction === 'LONG' ? 'arrowUp' : sig.direction === 'SHORT' ? 'arrowDown' : 'circle',
          text: `${sig.direction} ${(sig.confidence * 100).toFixed(0)}%`,
          size: 1.5,
        }));
      candleSeriesRef.current.setMarkers(markers);
    }

    chartRef.current?.timeScale().fitContent();
  }, [candles, signals, symbol]);

  // Toggle visibility
  useEffect(() => {
    sma20Ref.current?.applyOptions({ visible: overlays.sma20 });
    sma50Ref.current?.applyOptions({ visible: overlays.sma50 });
    sma200Ref.current?.applyOptions({ visible: overlays.sma200 });
    ema9Ref.current?.applyOptions({ visible: overlays.ema9 });
    ema21Ref.current?.applyOptions({ visible: overlays.ema21 });
    bbUpperRef.current?.applyOptions({ visible: overlays.bb });
    bbLowerRef.current?.applyOptions({ visible: overlays.bb });
    volumeSeriesRef.current?.applyOptions({ visible: overlays.volume });
  }, [overlays]);

  // Real-time subscription
  useEffect(() => {
    const channel = subscribeToMarketData(symbol, (data: MarketData) => {
      if (!candleSeriesRef.current) return;
      candleSeriesRef.current.update({
        time: Math.floor(new Date(data.timestamp).getTime() / 1000) as Time,
        open: data.open, high: data.high, low: data.low, close: data.close,
      });
    });
    return () => { channel.unsubscribe(); };
  }, [symbol]);

  const lastCandle = candles[candles.length - 1];
  const isPositive = lastCandle ? (lastCandle.close as number) >= (lastCandle.open as number) : true;

  const OVERLAY_TOGGLES = [
    { key: 'sma20', label: 'SMA20', color: '#00ccff' },
    { key: 'sma50', label: 'SMA50', color: '#ffaa00' },
    { key: 'sma200', label: 'SMA200', color: '#ff8844' },
    { key: 'ema9', label: 'EMA9', color: '#8844ff' },
    { key: 'ema21', label: 'EMA21', color: '#ff44aa' },
    { key: 'bb', label: 'BB', color: '#0088ff' },
    { key: 'volume', label: 'VOL', color: '#6b7280' },
  ] as const;

  return (
    <div className="flex flex-col h-full">
      {/* Toolbar */}
      <div className="flex items-center justify-between px-3 py-2 border-b border-border flex-shrink-0 flex-wrap gap-2">
        {/* OHLC */}
        <div className="flex items-center gap-4">
          <span className="text-sm font-bold text-white">{symbol}</span>
          {lastCandle && (
            <div className="flex items-center gap-2 text-xs font-mono">
              <span className="text-muted">O</span>
              <span className="text-white">{(ohlcv.o || lastCandle.open as number).toFixed(2)}</span>
              <span className="text-muted">H</span>
              <span className="text-nexus-green">{(ohlcv.h || lastCandle.high as number).toFixed(2)}</span>
              <span className="text-muted">L</span>
              <span className="text-nexus-red">{(ohlcv.l || lastCandle.low as number).toFixed(2)}</span>
              <span className="text-muted">C</span>
              <span className={isPositive ? 'text-nexus-green' : 'text-nexus-red'}>
                {(ohlcv.c || lastCandle.close as number).toFixed(2)}
              </span>
            </div>
          )}
        </div>

        {/* Timeframes */}
        <div className="flex items-center gap-1">
          {TIMEFRAMES.map((tf) => (
            <button
              key={tf}
              onClick={() => setTimeframe(tf)}
              className={cn(
                'px-2 py-0.5 rounded text-xs transition-colors',
                timeframe === tf
                  ? 'bg-nexus-blue/10 text-nexus-blue border border-nexus-blue/20'
                  : 'text-muted hover:text-white'
              )}
            >
              {tf}
            </button>
          ))}
        </div>

        {/* Overlay toggles */}
        <div className="flex items-center gap-1">
          {OVERLAY_TOGGLES.map((o) => (
            <button
              key={o.key}
              onClick={() => toggleOverlay(o.key)}
              className={cn(
                'px-1.5 py-0.5 rounded text-xs border transition-colors',
                overlays[o.key]
                  ? 'border-opacity-60 text-white'
                  : 'border-transparent text-muted hover:text-white'
              )}
              style={overlays[o.key] ? { borderColor: `${o.color}60`, color: o.color } : {}}
            >
              {o.label}
            </button>
          ))}
        </div>
      </div>

      {/* Chart */}
      <div ref={chartContainerRef} className="flex-1" />
    </div>
  );
}
