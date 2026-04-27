'use client';

import { useEffect, useRef } from 'react';
import {
  createChart,
  IChartApi,
  ISeriesApi,
  ColorType,
  Time,
  HistogramData,
  LineData,
} from 'lightweight-charts';

interface VolumeBar {
  time: number;
  volume: number;
  isUp: boolean;
}

interface VolumeChartProps {
  data: VolumeBar[];
  height?: number;
}

export function VolumeChart({ data, height = 100 }: VolumeChartProps) {
  const containerRef = useRef<HTMLDivElement>(null);
  const chartRef = useRef<IChartApi | null>(null);
  const volSeriesRef = useRef<ISeriesApi<'Histogram'> | null>(null);
  const maSeriesRef = useRef<ISeriesApi<'Line'> | null>(null);

  useEffect(() => {
    if (!containerRef.current) return;

    const chart = createChart(containerRef.current, {
      layout: {
        background: { type: ColorType.Solid, color: 'transparent' },
        textColor: '#6b7280',
        fontSize: 10,
      },
      grid: {
        vertLines: { visible: false },
        horzLines: { color: '#1a1a28' },
      },
      rightPriceScale: { visible: false },
      leftPriceScale: { visible: false },
      timeScale: { visible: false },
      handleScroll: false,
      handleScale: false,
      width: containerRef.current.clientWidth,
      height,
    });

    chartRef.current = chart;

    const volSeries = chart.addHistogramSeries({
      priceFormat: { type: 'volume' },
      lastValueVisible: false,
      priceLineVisible: false,
    });
    volSeriesRef.current = volSeries;

    const maSeries = chart.addLineSeries({
      color: 'rgba(0, 136, 255, 0.8)',
      lineWidth: 1,
      lastValueVisible: false,
      priceLineVisible: false,
    });
    maSeriesRef.current = maSeries;

    const resizeObserver = new ResizeObserver(() => {
      if (containerRef.current) {
        chart.applyOptions({ width: containerRef.current.clientWidth });
      }
    });
    resizeObserver.observe(containerRef.current);

    return () => {
      resizeObserver.disconnect();
      chart.remove();
    };
  }, [height]);

  useEffect(() => {
    if (!volSeriesRef.current || !maSeriesRef.current || !data.length) return;

    const avgVol = data.reduce((s, d) => s + d.volume, 0) / data.length;
    const SPIKE_MULTIPLIER = 1.5;

    const volData: HistogramData[] = data.map((d) => {
      const isSpike = d.volume > avgVol * SPIKE_MULTIPLIER;
      return {
        time: d.time as Time,
        value: d.volume,
        color: isSpike
          ? d.isUp ? 'rgba(0, 255, 136, 0.8)' : 'rgba(255, 68, 68, 0.8)'
          : d.isUp ? 'rgba(0, 255, 136, 0.35)' : 'rgba(255, 68, 68, 0.35)',
      };
    });
    volSeriesRef.current.setData(volData);

    // Volume MA (20-period)
    const period = 20;
    const maData: LineData[] = [];
    for (let i = period - 1; i < data.length; i++) {
      const sum = data.slice(i - period + 1, i + 1).reduce((s, d) => s + d.volume, 0);
      maData.push({ time: data[i].time as Time, value: sum / period });
    }
    maSeriesRef.current.setData(maData);

    chartRef.current?.timeScale().fitContent();
  }, [data]);

  return <div ref={containerRef} className="w-full" style={{ height }} />;
}
