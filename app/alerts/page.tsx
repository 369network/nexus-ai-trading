'use client';

import { useState, useEffect, useRef, useCallback } from 'react';
import {
  Bell,
  BellRing,
  Plus,
  Trash2,
  Eye,
  EyeOff,
  Copy,
  CheckCircle2,
  Clock,
  ChevronUp,
  X,
  Zap,
  AlertCircle,
  TrendingUp,
  TrendingDown,
} from 'lucide-react';
import { cn, formatNumber } from '@/lib/utils';

// ─── Types ────────────────────────────────────────────────────────────────────

interface PriceAlert {
  id: string;
  symbol: string;
  label: string;
  market: 'crypto' | 'forex' | 'commodities';
  condition: 'above' | 'below';
  targetPrice: number;
  currentPrice: number | null;
  triggered: boolean;
  triggeredAt: string | null;
  createdAt: string;
  active: boolean;
  note: string;
}

// ─── Constants ────────────────────────────────────────────────────────────────

const STORAGE_KEY = 'nexus_alerts';

const CRYPTO_SYMBOLS = [
  { symbol: 'BTCUSDT', label: 'BTC/USDT', market: 'crypto' as const },
  { symbol: 'ETHUSDT', label: 'ETH/USDT', market: 'crypto' as const },
  { symbol: 'SOLUSDT', label: 'SOL/USDT', market: 'crypto' as const },
  { symbol: 'BNBUSDT', label: 'BNB/USDT', market: 'crypto' as const },
  { symbol: 'XRPUSDT', label: 'XRP/USDT', market: 'crypto' as const },
  { symbol: 'ADAUSDT', label: 'ADA/USDT', market: 'crypto' as const },
];

const FOREX_SYMBOLS = [
  { symbol: 'EURUSD', label: 'EUR/USD', market: 'forex' as const },
  { symbol: 'GBPUSD', label: 'GBP/USD', market: 'forex' as const },
  { symbol: 'USDJPY', label: 'USD/JPY', market: 'forex' as const },
  { symbol: 'AUDUSD', label: 'AUD/USD', market: 'forex' as const },
];

const COMMODITY_SYMBOLS = [
  { symbol: 'XAUUSD', label: 'XAU/USD (Gold)', market: 'commodities' as const },
  { symbol: 'XAGUSD', label: 'XAG/USD (Silver)', market: 'commodities' as const },
];

const ALL_SYMBOLS = [...CRYPTO_SYMBOLS, ...FOREX_SYMBOLS, ...COMMODITY_SYMBOLS];

// ─── Helpers ──────────────────────────────────────────────────────────────────

function generateId(): string {
  try {
    return crypto.randomUUID();
  } catch {
    return Date.now().toString(36) + Math.random().toString(36).slice(2);
  }
}

function getPriceDecimals(symbol: string): number {
  if (symbol === 'XAGUSD') return 4;
  if (symbol.includes('JPY')) return 3;
  if (symbol.includes('BTC') || symbol.includes('XAU')) return 2;
  if (symbol.includes('USDT') || symbol.includes('USD') || symbol.includes('EUR') || symbol.includes('GBP') || symbol.includes('AUD')) return 5;
  return 2;
}

function formatPrice(price: number | null, symbol: string): string {
  if (price === null) return '—';
  const decimals = getPriceDecimals(symbol);
  // For crypto overrides: BTC 2dp, others by symbol
  if (symbol === 'BTCUSDT' || symbol === 'ETHUSDT') return formatNumber(price, 2);
  if (symbol === 'SOLUSDT' || symbol === 'BNBUSDT') return formatNumber(price, 2);
  if (symbol === 'XRPUSDT' || symbol === 'ADAUSDT') return formatNumber(price, 4);
  return formatNumber(price, decimals);
}

function loadAlerts(): PriceAlert[] {
  try {
    const raw = localStorage.getItem(STORAGE_KEY);
    if (!raw) return [];
    return JSON.parse(raw) as PriceAlert[];
  } catch {
    return [];
  }
}

function saveAlerts(alerts: PriceAlert[]): void {
  try {
    localStorage.setItem(STORAGE_KEY, JSON.stringify(alerts));
  } catch {
    // quota exceeded – silently ignore
  }
}

function isTriggeredToday(alert: PriceAlert): boolean {
  if (!alert.triggered || !alert.triggeredAt) return false;
  const today = new Date().toDateString();
  return new Date(alert.triggeredAt).toDateString() === today;
}

function sortAlerts(alerts: PriceAlert[]): PriceAlert[] {
  return [...alerts].sort((a, b) => {
    // Active first
    if (a.active && !b.active) return -1;
    if (!a.active && b.active) return 1;
    // Then triggered (non-active triggered)
    if (a.triggered && !b.triggered) return -1;
    if (!a.triggered && b.triggered) return 1;
    // Then by created desc
    return new Date(b.createdAt).getTime() - new Date(a.createdAt).getTime();
  });
}

// ─── Audio beep ───────────────────────────────────────────────────────────────

function playAlertBeep(): void {
  try {
    const ctx = new (window.AudioContext || (window as unknown as { webkitAudioContext: typeof AudioContext }).webkitAudioContext)();
    const osc = ctx.createOscillator();
    const gain = ctx.createGain();
    osc.connect(gain);
    gain.connect(ctx.destination);
    osc.type = 'sine';
    osc.frequency.setValueAtTime(880, ctx.currentTime);
    gain.gain.setValueAtTime(0.3, ctx.currentTime);
    gain.gain.exponentialRampToValueAtTime(0.001, ctx.currentTime + 0.2);
    osc.start(ctx.currentTime);
    osc.stop(ctx.currentTime + 0.2);
    osc.onended = () => ctx.close();
  } catch {
    // AudioContext not supported
  }
}

// ─── Browser notification ─────────────────────────────────────────────────────

function fireBrowserNotification(alert: PriceAlert): void {
  try {
    if (typeof Notification === 'undefined') return;
    if (Notification.permission !== 'granted') return;
    const direction = alert.condition === 'above' ? 'rose above' : 'fell below';
    new Notification(`NEXUS ALPHA — Alert Triggered`, {
      body: `${alert.label} ${direction} ${formatPrice(alert.targetPrice, alert.symbol)}`,
      icon: '/favicon.ico',
      tag: alert.id,
    });
  } catch {
    // Notification not supported
  }
}

// ─── Price feed hooks ─────────────────────────────────────────────────────────

type PriceMap = Record<string, number>;

function usePriceFeeds(
  onPriceUpdate: (prices: PriceMap) => void
): { prices: PriceMap; wsConnected: boolean } {
  const [prices, setPrices] = useState<PriceMap>({});
  const [wsConnected, setWsConnected] = useState(false);
  const onPriceUpdateRef = useRef(onPriceUpdate);
  onPriceUpdateRef.current = onPriceUpdate;
  const localPricesRef = useRef<PriceMap>({});

  // ── Binance WebSocket for crypto ──────────────────────────────────────────
  useEffect(() => {
    const CRYPTO_KEYS = new Set(CRYPTO_SYMBOLS.map((s) => s.symbol.toLowerCase()));
    let ws: WebSocket | null = null;
    let reconnectTimer: ReturnType<typeof setTimeout>;

    function connect() {
      try {
        ws = new WebSocket('wss://stream.binance.com:9443/ws/!miniTicker@arr');

        ws.onopen = () => setWsConnected(true);

        ws.onmessage = (ev) => {
          try {
            const tickers = JSON.parse(ev.data as string) as Array<{ s: string; c: string }>;
            const updates: PriceMap = {};
            for (const t of tickers) {
              if (CRYPTO_KEYS.has(t.s.toLowerCase())) {
                updates[t.s] = parseFloat(t.c);
              }
            }
            if (Object.keys(updates).length > 0) {
              localPricesRef.current = { ...localPricesRef.current, ...updates };
              setPrices((prev) => ({ ...prev, ...updates }));
              onPriceUpdateRef.current({ ...localPricesRef.current, ...updates });
            }
          } catch {
            // malformed frame — ignore
          }
        };

        ws.onerror = () => { /* suppress */ };

        ws.onclose = () => {
          setWsConnected(false);
          reconnectTimer = setTimeout(connect, 5000);
        };
      } catch {
        setWsConnected(false);
        reconnectTimer = setTimeout(connect, 10000);
      }
    }

    if (typeof WebSocket !== 'undefined') {
      connect();
    }

    return () => {
      clearTimeout(reconnectTimer);
      ws?.close();
    };
  }, []);

  // ── Frankfurter REST polling for forex ───────────────────────────────────
  useEffect(() => {
    const FOREX_MAP: Record<string, string> = {
      EURUSD: 'EUR',
      GBPUSD: 'GBP',
      USDJPY: 'JPY',
      AUDUSD: 'AUD',
    };

    async function fetchForex() {
      try {
        const res = await fetch('https://api.frankfurter.app/latest?from=USD&to=EUR,GBP,JPY,AUD');
        if (!res.ok) return;
        const data = await res.json() as { rates: Record<string, number> };
        const updates: PriceMap = {};
        for (const [sym, currency] of Object.entries(FOREX_MAP)) {
          const rate = data.rates[currency];
          if (rate === undefined) continue;
          // Frankfurter gives USD→X; for EURUSD/GBPUSD/AUDUSD we want X per USD inverted
          if (sym === 'USDJPY') {
            updates[sym] = rate;
          } else {
            updates[sym] = 1 / rate;
          }
        }
        if (Object.keys(updates).length > 0) {
          localPricesRef.current = { ...localPricesRef.current, ...updates };
          setPrices((prev) => ({ ...prev, ...updates }));
          onPriceUpdateRef.current({ ...localPricesRef.current, ...updates });
        }
      } catch {
        // network error
      }
    }

    fetchForex();
    const iv = setInterval(fetchForex, 60_000);
    return () => clearInterval(iv);
  }, []);

  // ── metals.live REST polling for commodities ──────────────────────────────
  useEffect(() => {
    async function fetchMetals() {
      try {
        const res = await fetch('https://api.metals.live/v1/spot');
        if (!res.ok) return;
        const data = await res.json() as Array<Record<string, number>>;
        // metals.live returns an array of objects like [{gold: 2300.12}, {silver: 27.34}]
        const merged: Record<string, number> = Object.assign({}, ...data);
        const updates: PriceMap = {};
        if (merged.gold) updates['XAUUSD'] = merged.gold;
        if (merged.silver) updates['XAGUSD'] = merged.silver;
        if (Object.keys(updates).length > 0) {
          localPricesRef.current = { ...localPricesRef.current, ...updates };
          setPrices((prev) => ({ ...prev, ...updates }));
          onPriceUpdateRef.current({ ...localPricesRef.current, ...updates });
        }
      } catch {
        // network error
      }
    }

    fetchMetals();
    const iv = setInterval(fetchMetals, 60_000);
    return () => clearInterval(iv);
  }, []);

  return { prices, wsConnected };
}

// ─── Sub-components ───────────────────────────────────────────────────────────

function StatusBadge({ alert }: { alert: PriceAlert }) {
  if (alert.triggered) {
    return (
      <span className="inline-flex items-center gap-1 px-2 py-0.5 rounded-full text-xs font-medium bg-nexus-green/10 text-nexus-green border border-nexus-green/20">
        <CheckCircle2 className="w-3 h-3" />
        Triggered
      </span>
    );
  }
  if (alert.active) {
    return (
      <span className="inline-flex items-center gap-1 px-2 py-0.5 rounded-full text-xs font-medium bg-nexus-blue/10 text-nexus-blue border border-nexus-blue/20">
        <Zap className="w-3 h-3" />
        Active
      </span>
    );
  }
  return (
    <span className="inline-flex items-center gap-1 px-2 py-0.5 rounded-full text-xs font-medium bg-[var(--text-muted)]/10 text-[var(--text-muted)] border border-[var(--text-muted)]/20">
      <EyeOff className="w-3 h-3" />
      Inactive
    </span>
  );
}

function MarketBadge({ market }: { market: PriceAlert['market'] }) {
  const styles: Record<string, string> = {
    crypto: 'bg-nexus-yellow/10 text-nexus-yellow border-nexus-yellow/20',
    forex: 'bg-nexus-blue/10 text-nexus-blue border-nexus-blue/20',
    commodities: 'bg-orange-400/10 text-orange-400 border-orange-400/20',
  };
  const labels: Record<string, string> = {
    crypto: 'Crypto',
    forex: 'Forex',
    commodities: 'Commodity',
  };
  return (
    <span className={cn('inline-flex px-2 py-0.5 rounded text-[10px] font-semibold tracking-wide uppercase border', styles[market])}>
      {labels[market]}
    </span>
  );
}

function SummaryCard({
  label,
  value,
  sub,
  accent,
}: {
  label: string;
  value: number | string;
  sub?: string;
  accent?: string;
}) {
  return (
    <div className="nexus-card rounded-xl p-4 flex flex-col gap-1">
      <span className="metric-label">{label}</span>
      <span className={cn('text-2xl font-bold tabular-nums', accent ?? 'text-white')}>{value}</span>
      {sub && <span className="text-xs text-[var(--text-muted)]">{sub}</span>}
    </div>
  );
}

// ─── Add Alert Form ───────────────────────────────────────────────────────────

interface AddAlertFormProps {
  prices: PriceMap;
  onAdd: (alert: PriceAlert) => void;
  onClose: () => void;
}

function AddAlertForm({ prices, onAdd, onClose }: AddAlertFormProps) {
  const [selectedSymbol, setSelectedSymbol] = useState(CRYPTO_SYMBOLS[0].symbol);
  const [condition, setCondition] = useState<'above' | 'below'>('above');
  const [targetPrice, setTargetPrice] = useState('');
  const [note, setNote] = useState('');

  const symbolInfo = ALL_SYMBOLS.find((s) => s.symbol === selectedSymbol)!;
  const currentPrice = prices[selectedSymbol] ?? null;

  // Auto-fill target when symbol or condition changes
  useEffect(() => {
    if (currentPrice === null) {
      setTargetPrice('');
      return;
    }
    const offset = condition === 'above' ? 1.05 : 0.95;
    const suggested = currentPrice * offset;
    const decimals = getPriceDecimals(selectedSymbol);
    setTargetPrice(suggested.toFixed(decimals));
  }, [selectedSymbol, condition, currentPrice]);

  function handleSubmit(e: React.FormEvent) {
    e.preventDefault();
    const parsed = parseFloat(targetPrice);
    if (isNaN(parsed) || parsed <= 0) return;

    const alert: PriceAlert = {
      id: generateId(),
      symbol: selectedSymbol,
      label: symbolInfo.label,
      market: symbolInfo.market,
      condition,
      targetPrice: parsed,
      currentPrice,
      triggered: false,
      triggeredAt: null,
      createdAt: new Date().toISOString(),
      active: true,
      note: note.trim(),
    };
    onAdd(alert);
    onClose();
  }

  const decimals = getPriceDecimals(selectedSymbol);
  const step = Math.pow(10, -decimals).toFixed(decimals);

  return (
    <div className="nexus-card rounded-xl border border-nexus-blue/30 p-5 mb-4">
      <div className="flex items-center justify-between mb-4">
        <h3 className="text-sm font-semibold text-white flex items-center gap-2">
          <Plus className="w-4 h-4 text-nexus-blue" />
          New Price Alert
        </h3>
        <button
          onClick={onClose}
          className="text-[var(--text-muted)] hover:text-white transition-colors"
          aria-label="Close form"
        >
          <X className="w-4 h-4" />
        </button>
      </div>

      <form onSubmit={handleSubmit} className="grid grid-cols-1 md:grid-cols-2 gap-4">
        {/* Symbol selector */}
        <div className="flex flex-col gap-1.5">
          <label className="metric-label">Symbol</label>
          <select
            value={selectedSymbol}
            onChange={(e) => setSelectedSymbol(e.target.value)}
            className="nexus-card rounded-lg px-3 py-2 text-sm text-white border border-white/10 bg-transparent focus:outline-none focus:border-nexus-blue/60 transition-colors"
          >
            <optgroup label="Crypto">
              {CRYPTO_SYMBOLS.map((s) => (
                <option key={s.symbol} value={s.symbol} className="bg-[#0d1117]">
                  {s.label}
                </option>
              ))}
            </optgroup>
            <optgroup label="Forex">
              {FOREX_SYMBOLS.map((s) => (
                <option key={s.symbol} value={s.symbol} className="bg-[#0d1117]">
                  {s.label}
                </option>
              ))}
            </optgroup>
            <optgroup label="Commodities">
              {COMMODITY_SYMBOLS.map((s) => (
                <option key={s.symbol} value={s.symbol} className="bg-[#0d1117]">
                  {s.label}
                </option>
              ))}
            </optgroup>
          </select>
          {currentPrice !== null && (
            <span className="text-xs text-[var(--text-muted)]">
              Current: <span className="text-white font-medium">{formatPrice(currentPrice, selectedSymbol)}</span>
            </span>
          )}
        </div>

        {/* Condition */}
        <div className="flex flex-col gap-1.5">
          <label className="metric-label">Condition</label>
          <div className="grid grid-cols-2 gap-2">
            <button
              type="button"
              onClick={() => setCondition('above')}
              className={cn(
                'flex items-center justify-center gap-1.5 px-3 py-2 rounded-lg text-sm font-medium border transition-all',
                condition === 'above'
                  ? 'bg-nexus-green/15 text-nexus-green border-nexus-green/40'
                  : 'nexus-card border-white/10 text-[var(--text-muted)] hover:text-white'
              )}
            >
              <TrendingUp className="w-3.5 h-3.5" />
              Above
            </button>
            <button
              type="button"
              onClick={() => setCondition('below')}
              className={cn(
                'flex items-center justify-center gap-1.5 px-3 py-2 rounded-lg text-sm font-medium border transition-all',
                condition === 'below'
                  ? 'bg-nexus-red/15 text-nexus-red border-nexus-red/40'
                  : 'nexus-card border-white/10 text-[var(--text-muted)] hover:text-white'
              )}
            >
              <TrendingDown className="w-3.5 h-3.5" />
              Below
            </button>
          </div>
        </div>

        {/* Target price */}
        <div className="flex flex-col gap-1.5">
          <label className="metric-label">Target Price</label>
          <input
            type="number"
            value={targetPrice}
            onChange={(e) => setTargetPrice(e.target.value)}
            step={step}
            min="0"
            required
            placeholder="0.00"
            className="nexus-card rounded-lg px-3 py-2 text-sm text-white border border-white/10 bg-transparent focus:outline-none focus:border-nexus-blue/60 transition-colors [appearance:textfield] [&::-webkit-outer-spin-button]:appearance-none [&::-webkit-inner-spin-button]:appearance-none"
          />
          {currentPrice !== null && targetPrice && !isNaN(parseFloat(targetPrice)) && (
            <span className="text-xs">
              {(() => {
                const dist = ((parseFloat(targetPrice) - currentPrice) / currentPrice) * 100;
                return (
                  <span className={dist >= 0 ? 'text-nexus-green' : 'text-nexus-red'}>
                    {dist >= 0 ? '+' : ''}{dist.toFixed(2)}% from current
                  </span>
                );
              })()}
            </span>
          )}
        </div>

        {/* Note */}
        <div className="flex flex-col gap-1.5">
          <label className="metric-label">Note (optional)</label>
          <input
            type="text"
            value={note}
            onChange={(e) => setNote(e.target.value)}
            placeholder="e.g. resistance level, macro event..."
            maxLength={120}
            className="nexus-card rounded-lg px-3 py-2 text-sm text-white border border-white/10 bg-transparent focus:outline-none focus:border-nexus-blue/60 transition-colors"
          />
        </div>

        {/* Submit */}
        <div className="md:col-span-2 flex justify-end gap-2 pt-1">
          <button
            type="button"
            onClick={onClose}
            className="px-4 py-2 text-sm text-[var(--text-muted)] hover:text-white transition-colors"
          >
            Cancel
          </button>
          <button
            type="submit"
            className="px-5 py-2 rounded-lg text-sm font-semibold bg-nexus-blue/20 text-nexus-blue border border-nexus-blue/40 hover:bg-nexus-blue/30 transition-all"
          >
            Set Alert
          </button>
        </div>
      </form>
    </div>
  );
}

// ─── Alert row ────────────────────────────────────────────────────────────────

interface AlertRowProps {
  alert: PriceAlert;
  onToggle: (id: string) => void;
  onDelete: (id: string) => void;
  onDuplicate: (id: string) => void;
}

function AlertRow({ alert, onToggle, onDelete, onDuplicate }: AlertRowProps) {
  const dist =
    alert.currentPrice !== null
      ? ((alert.currentPrice - alert.targetPrice) / alert.targetPrice) * 100
      : null;

  // Direction to target: positive dist means current > target (approaching for "above", moving away for "below")
  const distColor =
    dist === null
      ? 'text-[var(--text-muted)]'
      : alert.condition === 'above'
      ? dist >= 0
        ? 'text-nexus-green'
        : 'text-nexus-red'
      : dist <= 0
      ? 'text-nexus-green'
      : 'text-nexus-red';

  return (
    <tr className={cn('border-b border-white/5 transition-colors group hover:bg-white/[0.02]', alert.triggered && 'opacity-70')}>
      {/* Symbol */}
      <td className="py-3 px-4">
        <div className="flex flex-col gap-0.5">
          <span className="text-sm font-semibold text-white">{alert.label}</span>
          <MarketBadge market={alert.market} />
        </div>
      </td>

      {/* Condition */}
      <td className="py-3 px-4">
        <span
          className={cn(
            'inline-flex items-center gap-1 text-xs font-medium',
            alert.condition === 'above' ? 'text-nexus-green' : 'text-nexus-red'
          )}
        >
          {alert.condition === 'above' ? <TrendingUp className="w-3 h-3" /> : <TrendingDown className="w-3 h-3" />}
          {alert.condition === 'above' ? 'Above' : 'Below'}
        </span>
      </td>

      {/* Target */}
      <td className="py-3 px-4 text-sm text-white font-mono tabular-nums">
        {formatPrice(alert.targetPrice, alert.symbol)}
      </td>

      {/* Current */}
      <td className="py-3 px-4 text-sm font-mono tabular-nums text-[var(--text-muted)]">
        {formatPrice(alert.currentPrice, alert.symbol)}
      </td>

      {/* Distance */}
      <td className="py-3 px-4">
        {dist !== null ? (
          <span className={cn('text-xs font-medium tabular-nums', distColor)}>
            {dist >= 0 ? '+' : ''}{dist.toFixed(2)}%
          </span>
        ) : (
          <span className="text-xs text-[var(--text-muted)]">—</span>
        )}
      </td>

      {/* Status */}
      <td className="py-3 px-4">
        <StatusBadge alert={alert} />
      </td>

      {/* Note */}
      <td className="py-3 px-4 max-w-[180px]">
        <span className="text-xs text-[var(--text-muted)] truncate block" title={alert.note}>
          {alert.note || '—'}
        </span>
      </td>

      {/* Actions */}
      <td className="py-3 px-4">
        <div className="flex items-center gap-1 opacity-60 group-hover:opacity-100 transition-opacity">
          <button
            onClick={() => onToggle(alert.id)}
            disabled={alert.triggered}
            title={alert.active ? 'Disable alert' : 'Enable alert'}
            className="p-1.5 rounded hover:bg-white/10 transition-colors disabled:cursor-not-allowed disabled:opacity-40"
          >
            {alert.active ? (
              <Eye className="w-3.5 h-3.5 text-nexus-blue" />
            ) : (
              <EyeOff className="w-3.5 h-3.5 text-[var(--text-muted)]" />
            )}
          </button>
          <button
            onClick={() => onDuplicate(alert.id)}
            title="Duplicate alert"
            className="p-1.5 rounded hover:bg-white/10 transition-colors"
          >
            <Copy className="w-3.5 h-3.5 text-[var(--text-muted)] hover:text-white" />
          </button>
          <button
            onClick={() => onDelete(alert.id)}
            title="Delete alert"
            className="p-1.5 rounded hover:bg-nexus-red/20 transition-colors"
          >
            <Trash2 className="w-3.5 h-3.5 text-[var(--text-muted)] hover:text-nexus-red" />
          </button>
        </div>
      </td>
    </tr>
  );
}

// ─── Main Page ────────────────────────────────────────────────────────────────

export default function AlertsPage() {
  const [alerts, setAlerts] = useState<PriceAlert[]>([]);
  const [showForm, setShowForm] = useState(false);
  const [notifPermission, setNotifPermission] = useState<NotificationPermission | 'unsupported'>('default');
  const alertsRef = useRef<PriceAlert[]>([]);

  // Load from localStorage on mount
  useEffect(() => {
    const stored = loadAlerts();
    setAlerts(stored);
    alertsRef.current = stored;
  }, []);

  // Sync permission state
  useEffect(() => {
    if (typeof Notification === 'undefined') {
      setNotifPermission('unsupported');
    } else {
      setNotifPermission(Notification.permission);
    }
  }, []);

  const persistAlerts = useCallback((next: PriceAlert[]) => {
    setAlerts(next);
    alertsRef.current = next;
    saveAlerts(next);
  }, []);

  // Called on each price tick
  const handlePriceUpdate = useCallback((prices: PriceMap) => {
    const current = alertsRef.current;
    let changed = false;
    const next = current.map((alert) => {
      if (!alert.active || alert.triggered) return alert;
      const price = prices[alert.symbol];
      if (price === undefined) return alert;

      const updated = { ...alert, currentPrice: price };
      const triggered =
        alert.condition === 'above' ? price >= alert.targetPrice : price <= alert.targetPrice;

      if (triggered) {
        changed = true;
        const fired: PriceAlert = {
          ...updated,
          triggered: true,
          triggeredAt: new Date().toISOString(),
          active: false,
        };
        playAlertBeep();
        fireBrowserNotification(fired);
        return fired;
      }

      if (updated.currentPrice !== alert.currentPrice) changed = true;
      return updated;
    });

    if (changed) {
      alertsRef.current = next;
      setAlerts([...next]);
      saveAlerts(next);
    }
  }, []);

  const { prices, wsConnected } = usePriceFeeds(handlePriceUpdate);

  // Update currentPrice on all alerts when prices map changes (for display)
  useEffect(() => {
    if (Object.keys(prices).length === 0) return;
    const current = alertsRef.current;
    let changed = false;
    const next = current.map((a) => {
      const p = prices[a.symbol];
      if (p !== undefined && p !== a.currentPrice) {
        changed = true;
        return { ...a, currentPrice: p };
      }
      return a;
    });
    if (changed) {
      alertsRef.current = next;
      setAlerts([...next]);
      saveAlerts(next);
    }
  }, [prices]);

  // ── Alert mutations ─────────────────────────────────────────────────────────

  const handleAddAlert = useCallback((alert: PriceAlert) => {
    const current = alertsRef.current;
    persistAlerts([...current, alert]);
  }, [persistAlerts]);

  const handleToggle = useCallback((id: string) => {
    const next = alertsRef.current.map((a) =>
      a.id === id ? { ...a, active: !a.active } : a
    );
    persistAlerts(next);
  }, [persistAlerts]);

  const handleDelete = useCallback((id: string) => {
    persistAlerts(alertsRef.current.filter((a) => a.id !== id));
  }, [persistAlerts]);

  const handleDuplicate = useCallback((id: string) => {
    const src = alertsRef.current.find((a) => a.id === id);
    if (!src) return;
    const clone: PriceAlert = {
      ...src,
      id: generateId(),
      triggered: false,
      triggeredAt: null,
      active: true,
      createdAt: new Date().toISOString(),
    };
    persistAlerts([...alertsRef.current, clone]);
  }, [persistAlerts]);

  // ── Notification permission ─────────────────────────────────────────────────

  const requestNotifPermission = async () => {
    if (typeof Notification === 'undefined') return;
    try {
      const result = await Notification.requestPermission();
      setNotifPermission(result);
    } catch {
      // blocked or unsupported
    }
  };

  // ── Derived state ───────────────────────────────────────────────────────────

  const sorted = sortAlerts(alerts);
  const totalCount = alerts.length;
  const activeCount = alerts.filter((a) => a.active && !a.triggered).length;
  const triggeredToday = alerts.filter(isTriggeredToday);

  const hasPrices = Object.keys(prices).length > 0;

  return (
    <div className="min-h-screen p-4 md:p-6 space-y-5">

      {/* ── Header ── */}
      <div className="flex flex-wrap items-center justify-between gap-3">
        <div className="flex items-center gap-3">
          <div className="p-2 rounded-lg bg-nexus-yellow/10 border border-nexus-yellow/20">
            <Bell className="w-5 h-5 text-nexus-yellow" />
          </div>
          <div>
            <h1 className="text-xl font-bold text-white">Price Alerts</h1>
            <p className="text-xs text-[var(--text-muted)]">
              Real-time notifications for your price targets
            </p>
          </div>
          {/* WS status dot */}
          <div className="flex items-center gap-1.5 ml-2">
            <span
              className={cn(
                'w-2 h-2 rounded-full',
                wsConnected ? 'bg-nexus-green animate-pulse' : hasPrices ? 'bg-nexus-yellow' : 'bg-[var(--text-muted)]'
              )}
            />
            <span className="text-[10px] text-[var(--text-muted)]">
              {wsConnected ? 'Live' : hasPrices ? 'Polling' : 'Connecting…'}
            </span>
          </div>
        </div>

        <div className="flex items-center gap-2">
          {notifPermission !== 'granted' && notifPermission !== 'unsupported' && (
            <button
              onClick={requestNotifPermission}
              disabled={notifPermission === 'denied'}
              title={notifPermission === 'denied' ? 'Permission denied in browser settings' : undefined}
              className="flex items-center gap-1.5 px-3 py-1.5 rounded-lg text-xs font-medium bg-nexus-yellow/10 text-nexus-yellow border border-nexus-yellow/20 hover:bg-nexus-yellow/20 transition-all disabled:opacity-50 disabled:cursor-not-allowed"
            >
              <BellRing className="w-3.5 h-3.5" />
              {notifPermission === 'denied' ? 'Notifications Blocked' : 'Request Notifications'}
            </button>
          )}
          {notifPermission === 'granted' && (
            <div className="flex items-center gap-1.5 px-3 py-1.5 rounded-lg text-xs font-medium bg-nexus-green/10 text-nexus-green border border-nexus-green/20">
              <CheckCircle2 className="w-3.5 h-3.5" />
              Notifications On
            </div>
          )}

          <button
            onClick={() => setShowForm((v) => !v)}
            className="flex items-center gap-1.5 px-4 py-1.5 rounded-lg text-sm font-semibold bg-nexus-blue/20 text-nexus-blue border border-nexus-blue/40 hover:bg-nexus-blue/30 transition-all"
          >
            {showForm ? <ChevronUp className="w-4 h-4" /> : <Plus className="w-4 h-4" />}
            {showForm ? 'Close' : 'Add Alert'}
          </button>
        </div>
      </div>

      {/* ── Summary cards ── */}
      <div className="grid grid-cols-3 gap-3">
        <SummaryCard
          label="Total Alerts"
          value={totalCount}
          sub={totalCount === 1 ? '1 alert configured' : `${totalCount} alerts configured`}
        />
        <SummaryCard
          label="Active"
          value={activeCount}
          sub="Watching for triggers"
          accent={activeCount > 0 ? 'text-nexus-blue' : undefined}
        />
        <SummaryCard
          label="Triggered Today"
          value={triggeredToday.length}
          sub={triggeredToday.length === 0 ? 'None yet' : 'Tap to review below'}
          accent={triggeredToday.length > 0 ? 'text-nexus-green' : undefined}
        />
      </div>

      {/* ── Add Alert form ── */}
      {showForm && (
        <AddAlertForm prices={prices} onAdd={handleAddAlert} onClose={() => setShowForm(false)} />
      )}

      {/* ── Triggered today panel ── */}
      {triggeredToday.length > 0 && (
        <div className="rounded-xl border border-nexus-green/30 bg-nexus-green/5 p-4 space-y-2">
          <div className="flex items-center gap-2 mb-3">
            <CheckCircle2 className="w-4 h-4 text-nexus-green" />
            <h2 className="text-sm font-semibold text-nexus-green">
              Triggered Today — {triggeredToday.length} alert{triggeredToday.length > 1 ? 's' : ''}
            </h2>
          </div>
          <div className="space-y-2">
            {triggeredToday.map((a) => (
              <div
                key={a.id}
                className="flex flex-wrap items-center justify-between gap-2 px-3 py-2 rounded-lg bg-nexus-green/10 border border-nexus-green/20"
              >
                <div className="flex items-center gap-3">
                  <span className="text-sm font-bold text-white">{a.label}</span>
                  <span className="text-xs text-[var(--text-muted)]">
                    {a.condition === 'above' ? 'rose above' : 'fell below'}
                  </span>
                  <span className="text-sm font-mono text-nexus-green">
                    {formatPrice(a.targetPrice, a.symbol)}
                  </span>
                </div>
                {a.triggeredAt && (
                  <div className="flex items-center gap-1 text-xs text-[var(--text-muted)]">
                    <Clock className="w-3 h-3" />
                    {new Date(a.triggeredAt).toLocaleTimeString()}
                  </div>
                )}
              </div>
            ))}
          </div>
        </div>
      )}

      {/* ── Alerts table ── */}
      <div className="nexus-card rounded-xl overflow-hidden">
        <div className="px-4 py-3 border-b border-white/5 flex items-center justify-between">
          <h2 className="text-sm font-semibold text-white flex items-center gap-2">
            <AlertCircle className="w-4 h-4 text-[var(--text-muted)]" />
            All Alerts
          </h2>
          <span className="text-xs text-[var(--text-muted)]">{alerts.length} total</span>
        </div>

        {sorted.length === 0 ? (
          <div className="flex flex-col items-center justify-center py-16 px-4 text-center border-2 border-dashed border-white/10 mx-4 my-4 rounded-xl">
            <Bell className="w-10 h-10 text-[var(--text-muted)] mb-3 opacity-40" />
            <p className="text-sm font-medium text-[var(--text-muted)]">No alerts set</p>
            <p className="text-xs text-[var(--text-muted)] opacity-60 mt-1">
              Click <strong className="text-white">Add Alert</strong> to get started
            </p>
          </div>
        ) : (
          <div className="overflow-x-auto">
            <table className="w-full text-sm">
              <thead>
                <tr className="border-b border-white/5 text-left">
                  {['Symbol', 'Condition', 'Target', 'Current', 'Distance %', 'Status', 'Note', 'Actions'].map((h) => (
                    <th key={h} className="px-4 py-2.5 metric-label font-medium whitespace-nowrap">
                      {h}
                    </th>
                  ))}
                </tr>
              </thead>
              <tbody>
                {sorted.map((alert) => (
                  <AlertRow
                    key={alert.id}
                    alert={alert}
                    onToggle={handleToggle}
                    onDelete={handleDelete}
                    onDuplicate={handleDuplicate}
                  />
                ))}
              </tbody>
            </table>
          </div>
        )}
      </div>

      {/* ── Footer ── */}
      <p className="text-[10px] text-[var(--text-muted)] text-center leading-relaxed opacity-60 pb-2">
        Alerts are stored locally in your browser. Browser notifications require permission.
        Crypto prices via Binance WebSocket · Forex via Frankfurter API (60s poll) · Commodities via metals.live (60s poll).
      </p>
    </div>
  );
}
