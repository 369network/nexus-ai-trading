// ============================================================
// NEXUS ALPHA - Utility Functions
// ============================================================

import { clsx, type ClassValue } from 'clsx';
import { twMerge } from 'tailwind-merge';
import { formatDistanceToNow, format } from 'date-fns';

export function cn(...inputs: ClassValue[]): string {
  return twMerge(clsx(inputs));
}

export function formatCurrency(
  value: number,
  currency: string = 'USD',
  compact: boolean = false
): string {
  if (compact && Math.abs(value) >= 1000000) {
    return `$${(value / 1000000).toFixed(2)}M`;
  }
  if (compact && Math.abs(value) >= 1000) {
    return `$${(value / 1000).toFixed(1)}K`;
  }
  return new Intl.NumberFormat('en-US', {
    style: 'currency',
    currency,
    minimumFractionDigits: 2,
    maximumFractionDigits: 2,
  }).format(value);
}

export function formatNumber(
  value: number,
  decimals: number = 2,
  compact: boolean = false
): string {
  if (compact && Math.abs(value) >= 1000000) {
    return `${(value / 1000000).toFixed(1)}M`;
  }
  if (compact && Math.abs(value) >= 1000) {
    return `${(value / 1000).toFixed(1)}K`;
  }
  return value.toFixed(decimals);
}

export function formatPercent(value: number, decimals: number = 2): string {
  const sign = value >= 0 ? '+' : '';
  return `${sign}${value.toFixed(decimals)}%`;
}

export function formatTimeAgo(dateString: string): string {
  try {
    return formatDistanceToNow(new Date(dateString), { addSuffix: true });
  } catch {
    return 'unknown';
  }
}

export function formatDateTime(dateString: string, fmt: string = 'HH:mm:ss'): string {
  try {
    return format(new Date(dateString), fmt);
  } catch {
    return '--:--:--';
  }
}

export function formatDate(dateString: string): string {
  try {
    return format(new Date(dateString), 'MMM dd, yyyy');
  } catch {
    return '---';
  }
}

export function getPnlColor(value: number): string {
  if (value > 0) return 'text-nexus-green';
  if (value < 0) return 'text-nexus-red';
  return 'text-muted-foreground';
}

export function getPnlBgColor(value: number): string {
  if (value > 0) return 'bg-nexus-green/10 text-nexus-green';
  if (value < 0) return 'bg-nexus-red/10 text-nexus-red';
  return 'bg-muted/10 text-muted-foreground';
}

export function getDirectionColor(direction: string): string {
  switch (direction) {
    case 'LONG':
      return 'text-nexus-green';
    case 'SHORT':
      return 'text-nexus-red';
    default:
      return 'text-nexus-yellow';
  }
}

export function getDirectionBg(direction: string): string {
  switch (direction) {
    case 'LONG':
      return 'bg-nexus-green/10 text-nexus-green border border-nexus-green/20';
    case 'SHORT':
      return 'bg-nexus-red/10 text-nexus-red border border-nexus-red/20';
    default:
      return 'bg-nexus-yellow/10 text-nexus-yellow border border-nexus-yellow/20';
  }
}

export function getSeverityColor(severity: string): string {
  switch (severity) {
    case 'CRITICAL':
      return 'text-nexus-red';
    case 'HIGH':
      return 'text-orange-400';
    case 'MEDIUM':
      return 'text-nexus-yellow';
    case 'LOW':
      return 'text-nexus-green';
    default:
      return 'text-muted-foreground';
  }
}

export function getStrengthLabel(strength: number): string {
  if (strength >= 85) return 'STRONG';
  if (strength >= 70) return 'MODERATE';
  if (strength >= 55) return 'WEAK';
  return 'VERY WEAK';
}

export function getStrengthColor(strength: number): string {
  if (strength >= 85) return 'text-nexus-green';
  if (strength >= 70) return 'text-nexus-blue';
  if (strength >= 55) return 'text-nexus-yellow';
  return 'text-muted-foreground';
}

export function calculateWinRate(wins: number, total: number): number {
  if (total === 0) return 0;
  return (wins / total) * 100;
}

export function getPriceDecimals(symbol: string): number {
  if (symbol.includes('JPY')) return 3;
  if (symbol.includes('BTC')) return 2;
  if (symbol.includes('XAU') || symbol.includes('GOLD')) return 2;
  if (symbol.includes('OIL') || symbol.includes('WTI')) return 2;
  if (symbol.includes('NIFTY') || symbol.includes('BANK')) return 2;
  if (symbol.includes('USD') || symbol.includes('EUR')) return 5;
  return 2;
}

export function debounce<T extends (...args: unknown[]) => unknown>(
  fn: T,
  delay: number
): (...args: Parameters<T>) => void {
  let timeout: ReturnType<typeof setTimeout>;
  return (...args: Parameters<T>) => {
    clearTimeout(timeout);
    timeout = setTimeout(() => fn(...args), delay);
  };
}

export function clampValue(value: number, min: number, max: number): number {
  return Math.max(min, Math.min(max, value));
}

export function interpolateColor(
  value: number,
  min: number,
  max: number,
  colorMin: string = '#ff4444',
  colorMax: string = '#00ff88'
): string {
  const t = clampValue((value - min) / (max - min), 0, 1);
  // Simple interpolation between red and green via yellow
  if (t < 0.5) {
    return `rgba(255, ${Math.round(t * 2 * 170)}, 0, 0.8)`;
  } else {
    return `rgba(${Math.round((1 - t) * 2 * 255)}, ${Math.round(136 + t * 119)}, ${Math.round(t * 136)}, 0.8)`;
  }
}

export function generateSparklineData(
  length: number = 24,
  startValue: number = 100,
  volatility: number = 0.02
): number[] {
  const data = [startValue];
  for (let i = 1; i < length; i++) {
    const change = (Math.random() - 0.48) * volatility;
    data.push(data[i - 1] * (1 + change));
  }
  return data;
}

export function toCSV(data: Record<string, unknown>[]): string {
  if (!data.length) return '';
  const headers = Object.keys(data[0]);
  const rows = data.map((row) =>
    headers.map((h) => JSON.stringify(row[h] ?? '')).join(',')
  );
  return [headers.join(','), ...rows].join('\n');
}

export function downloadCSV(data: Record<string, unknown>[], filename: string): void {
  const csv = toCSV(data);
  const blob = new Blob([csv], { type: 'text/csv;charset=utf-8;' });
  const url = URL.createObjectURL(blob);
  const link = document.createElement('a');
  link.href = url;
  link.download = filename;
  link.click();
  URL.revokeObjectURL(url);
}
