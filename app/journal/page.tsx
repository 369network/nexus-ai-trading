'use client';

import { useEffect, useState, useMemo, useCallback } from 'react';
import { supabase } from '@/lib/supabase';
import { cn, formatCurrency, formatPercent } from '@/lib/utils';
import type { Trade, Market } from '@/lib/types';
import {
  Download,
  ArrowUp,
  ArrowDown,
  ChevronLeft,
  ChevronRight,
  Filter,
} from 'lucide-react';

// ─── Constants ────────────────────────────────────────────────

const PAGE_SIZE = 20;

const MARKET_OPTIONS: { value: 'all' | Market; label: string }[] = [
  { value: 'all', label: 'All Markets' },
  { value: 'crypto', label: 'Crypto' },
  { value: 'forex', label: 'Forex' },
  { value: 'commodities', label: 'Commodities' },
  { value: 'indian_stocks', label: 'Indian Stocks' },
  { value: 'us_stocks', label: 'US Stocks' },
];

const MARKET_COLORS: Record<Market, string> = {
  crypto: '#f7931a',
  forex: '#00a8ff',
  commodities: '#ffd700',
  indian_stocks: '#00b09b',
  us_stocks: '#6c5ce7',
};

type SortKey = 'date' | 'pnl' | 'pnlPct' | 'duration';
type Direction = 'asc' | 'desc';

// ─── Helpers ──────────────────────────────────────────────────

function formatDuration(minutes: number | undefined): string {
  if (!minutes) return '—';
  if (minutes < 60) return `${Math.round(minutes)}m`;
  const h = Math.floor(minutes / 60);
  const m = Math.round(minutes % 60);
  if (h < 24) return m > 0 ? `${h}h ${m}m` : `${h}h`;
  const d = Math.floor(h / 24);
  const rh = h % 24;
  return rh > 0 ? `${d}d ${rh}h` : `${d}d`;
}

function getDurationMinutes(trade: Trade): number {
  if (trade.duration_minutes) return trade.duration_minutes;
  if (trade.opened_at && trade.closed_at) {
    return (new Date(trade.closed_at).getTime() - new Date(trade.opened_at).getTime()) / 60000;
  }
  return 0;
}

function exportToCsv(trades: Trade[]): void {
  const headers = [
    'Date', 'Symbol', 'Market', 'Direction', 'Status',
    'Entry Price', 'Exit Price', 'Qty', 'PnL ($)', 'PnL (%)',
    'Duration', 'Strategy', 'Commission',
  ];

  const rows = trades.map((t) => [
    t.closed_at ? new Date(t.closed_at).toLocaleDateString() : '',
    t.symbol,
    t.market,
    t.direction,
    t.status,
    t.entry_price,
    t.exit_price ?? '',
    t.quantity,
    t.pnl ?? '',
    t.pnl_pct ?? '',
    formatDuration(getDurationMinutes(t)),
    t.strategy ?? '',
    t.commission ?? t.fees_paid ?? '',
  ]);

  const csvContent = [headers, ...rows]
    .map((row) => row.map((v) => `"${v}"`).join(','))
    .join('\n');

  const blob = new Blob([csvContent], { type: 'text/csv;charset=utf-8;' });
  const url = URL.createObjectURL(blob);
  const link = document.createElement('a');
  link.href = url;
  link.download = `nexus-trades-${new Date().toISOString().slice(0, 10)}.csv`;
  link.click();
  URL.revokeObjectURL(url);
}

// ─── Summary Bar ──────────────────────────────────────────────

function SummaryBar({ trades }: { trades: Trade[] }) {
  const stats = useMemo(() => {
    const closed = trades.filter((t) => t.pnl != null);
    if (closed.length === 0) return null;

    const totalPnl = closed.reduce((s, t) => s + (t.pnl ?? 0), 0);
    const wins = closed.filter((t) => (t.pnl ?? 0) > 0);
    const losses = closed.filter((t) => (t.pnl ?? 0) < 0);
    const winRate = closed.length > 0 ? (wins.length / closed.length) * 100 : 0;
    const avgWin = wins.length > 0 ? wins.reduce((s, t) => s + (t.pnl ?? 0), 0) / wins.length : 0;
    const avgLoss = losses.length > 0 ? losses.reduce((s, t) => s + (t.pnl ?? 0), 0) / losses.length : 0;
    const grossWin = wins.reduce((s, t) => s + (t.pnl ?? 0), 0);
    const grossLoss = Math.abs(losses.reduce((s, t) => s + (t.pnl ?? 0), 0));
    const profitFactor = grossLoss > 0 ? grossWin / grossLoss : grossWin > 0 ? Infinity : 0;

    return { totalPnl, winRate, avgWin, avgLoss, profitFactor, count: closed.length };
  }, [trades]);

  if (!stats) return null;

  return (
    <div className="nexus-card p-4 flex flex-wrap gap-6 text-xs">
      <div>
        <div className="text-muted mb-1">Total P&L</div>
        <div className={cn('font-mono font-bold text-base', stats.totalPnl >= 0 ? 'text-nexus-green' : 'text-nexus-red')}>
          {stats.totalPnl >= 0 ? '+' : ''}{formatCurrency(stats.totalPnl)}
        </div>
      </div>
      <div>
        <div className="text-muted mb-1">Win Rate</div>
        <div className={cn('font-mono font-bold text-base', stats.winRate >= 50 ? 'text-nexus-green' : 'text-nexus-yellow')}>
          {stats.winRate.toFixed(1)}%
        </div>
      </div>
      <div>
        <div className="text-muted mb-1">Avg Win</div>
        <div className="font-mono font-bold text-base text-nexus-green">
          +{formatCurrency(stats.avgWin)}
        </div>
      </div>
      <div>
        <div className="text-muted mb-1">Avg Loss</div>
        <div className="font-mono font-bold text-base text-nexus-red">
          {formatCurrency(stats.avgLoss)}
        </div>
      </div>
      <div>
        <div className="text-muted mb-1">Profit Factor</div>
        <div className={cn('font-mono font-bold text-base', stats.profitFactor >= 1.5 ? 'text-nexus-green' : 'text-nexus-yellow')}>
          {isFinite(stats.profitFactor) ? stats.profitFactor.toFixed(2) : '∞'}
        </div>
      </div>
      <div className="ml-auto self-center">
        <span className="text-muted">{stats.count} closed trades</span>
      </div>
    </div>
  );
}

// ─── Trade Card ───────────────────────────────────────────────

function TradeCard({ trade }: { trade: Trade }) {
  const isWin = (trade.pnl ?? 0) > 0;
  const marketColor = MARKET_COLORS[trade.market] ?? '#6b7280';
  const duration = getDurationMinutes(trade);
  const commission = trade.commission ?? trade.fees_paid ?? 0;
  const closedDate = trade.closed_at
    ? new Date(trade.closed_at).toLocaleDateString('en-US', { month: 'short', day: 'numeric', year: 'numeric' })
    : '—';

  return (
    <div className="nexus-card p-4 flex items-center gap-4 text-xs hover:border-border/80 transition-colors">
      {/* Symbol + Market */}
      <div className="flex flex-col gap-1 min-w-[100px]">
        <div className="flex items-center gap-2">
          <span
            className="text-xs font-bold px-1.5 py-0.5 rounded"
            style={{ background: `${marketColor}22`, color: marketColor, border: `1px solid ${marketColor}44` }}
          >
            {trade.market.replace('_', ' ').toUpperCase()}
          </span>
        </div>
        <div className="font-bold text-white text-sm">{trade.symbol}</div>
        <div className="text-muted">{closedDate}</div>
      </div>

      {/* Direction */}
      <div className="flex flex-col items-center gap-1 min-w-[60px]">
        <div
          className={cn(
            'flex items-center gap-1 font-bold text-xs px-2 py-0.5 rounded',
            trade.direction === 'LONG'
              ? 'bg-nexus-green/10 text-nexus-green border border-nexus-green/20'
              : 'bg-nexus-red/10 text-nexus-red border border-nexus-red/20'
          )}
        >
          {trade.direction === 'LONG' ? <ArrowUp size={10} /> : <ArrowDown size={10} />}
          {trade.direction}
        </div>
      </div>

      {/* Entry → Exit */}
      <div className="flex flex-col gap-1 min-w-[140px]">
        <div className="text-muted">Entry → Exit</div>
        <div className="font-mono text-white">
          ${trade.entry_price.toFixed(4)}
          <span className="text-muted mx-1">→</span>
          {trade.exit_price ? `$${trade.exit_price.toFixed(4)}` : <span className="text-muted">open</span>}
        </div>
        <div className="text-muted">Qty: {trade.quantity}</div>
      </div>

      {/* PnL */}
      <div className="flex flex-col gap-1 min-w-[100px]">
        <div className="text-muted">P&L</div>
        <div className={cn('font-mono font-bold text-sm', isWin ? 'text-nexus-green' : 'text-nexus-red')}>
          {(trade.pnl ?? 0) >= 0 ? '+' : ''}{formatCurrency(trade.pnl ?? 0)}
        </div>
        {trade.pnl_pct != null && (
          <div className={cn('font-mono', isWin ? 'text-nexus-green/80' : 'text-nexus-red/80')}>
            {trade.pnl_pct >= 0 ? '+' : ''}{trade.pnl_pct.toFixed(2)}%
          </div>
        )}
      </div>

      {/* Duration */}
      <div className="flex flex-col gap-1 min-w-[70px]">
        <div className="text-muted">Duration</div>
        <div className="text-white font-mono">{formatDuration(duration)}</div>
      </div>

      {/* Strategy */}
      <div className="flex flex-col gap-1 flex-1">
        <div className="text-muted">Strategy</div>
        <div className="text-white">{trade.strategy ?? <span className="text-muted">—</span>}</div>
      </div>

      {/* Commission */}
      <div className="flex flex-col gap-1 min-w-[80px]">
        <div className="text-muted">Commission</div>
        <div className="font-mono text-muted">{commission > 0 ? formatCurrency(commission) : '—'}</div>
      </div>

      {/* Win/Loss badge */}
      <div>
        <span
          className={cn(
            'text-xs font-bold px-2 py-0.5 rounded',
            isWin
              ? 'bg-nexus-green/10 text-nexus-green border border-nexus-green/20'
              : 'bg-nexus-red/10 text-nexus-red border border-nexus-red/20'
          )}
        >
          {isWin ? 'WIN' : 'LOSS'}
        </span>
      </div>
    </div>
  );
}

// ─── Fetch helper ──────────────────────────────────────────────

async function getClosedTrades(): Promise<Trade[]> {
  const { data } = await supabase
    .from('trades')
    .select('*')
    .eq('status', 'CLOSED')
    .order('closed_at', { ascending: false })
    .limit(200);

  return (data ?? []) as Trade[];
}

// ─── Main Page ────────────────────────────────────────────────

export default function JournalPage() {
  const [trades, setTrades] = useState<Trade[]>([]);
  const [loading, setLoading] = useState(true);

  // Filters
  const [market, setMarket] = useState<'all' | Market>('all');
  const [direction, setDirection] = useState<'all' | 'LONG' | 'SHORT'>('all');
  const [minPnl, setMinPnl] = useState('');
  const [dateFrom, setDateFrom] = useState('');
  const [dateTo, setDateTo] = useState('');

  // Sorting
  const [sortKey, setSortKey] = useState<SortKey>('date');
  const [sortDir, setSortDir] = useState<Direction>('desc');

  // Pagination
  const [page, setPage] = useState(1);

  useEffect(() => {
    getClosedTrades().then((t) => {
      setTrades(t);
      setLoading(false);
    });
  }, []);

  const handleSort = useCallback((key: SortKey) => {
    if (sortKey === key) {
      setSortDir((d) => (d === 'asc' ? 'desc' : 'asc'));
    } else {
      setSortKey(key);
      setSortDir('desc');
    }
    setPage(1);
  }, [sortKey]);

  const filteredAndSorted = useMemo(() => {
    let result = [...trades];

    if (market !== 'all') result = result.filter((t) => t.market === market);
    if (direction !== 'all') result = result.filter((t) => t.direction === direction);
    if (minPnl !== '') {
      const threshold = parseFloat(minPnl);
      if (!isNaN(threshold)) result = result.filter((t) => (t.pnl ?? 0) >= threshold);
    }
    if (dateFrom) result = result.filter((t) => t.closed_at && t.closed_at >= dateFrom);
    if (dateTo) result = result.filter((t) => t.closed_at && t.closed_at <= dateTo + 'T23:59:59');

    result.sort((a, b) => {
      let aVal = 0;
      let bVal = 0;
      switch (sortKey) {
        case 'date':
          aVal = a.closed_at ? new Date(a.closed_at).getTime() : 0;
          bVal = b.closed_at ? new Date(b.closed_at).getTime() : 0;
          break;
        case 'pnl':
          aVal = a.pnl ?? 0;
          bVal = b.pnl ?? 0;
          break;
        case 'pnlPct':
          aVal = a.pnl_pct ?? 0;
          bVal = b.pnl_pct ?? 0;
          break;
        case 'duration':
          aVal = getDurationMinutes(a);
          bVal = getDurationMinutes(b);
          break;
      }
      return sortDir === 'asc' ? aVal - bVal : bVal - aVal;
    });

    return result;
  }, [trades, market, direction, minPnl, dateFrom, dateTo, sortKey, sortDir]);

  const totalPages = Math.max(1, Math.ceil(filteredAndSorted.length / PAGE_SIZE));
  const paginated = filteredAndSorted.slice((page - 1) * PAGE_SIZE, page * PAGE_SIZE);

  const SortButton = ({ label, k }: { label: string; k: SortKey }) => (
    <button
      onClick={() => handleSort(k)}
      className={cn(
        'text-xs px-2 py-1 rounded border transition-colors',
        sortKey === k
          ? 'border-nexus-blue text-nexus-blue bg-nexus-blue/10'
          : 'border-border text-muted hover:text-white hover:border-white/20'
      )}
    >
      {label} {sortKey === k ? (sortDir === 'desc' ? '↓' : '↑') : ''}
    </button>
  );

  return (
    <div className="space-y-4">
      {/* Header */}
      <div className="flex items-center justify-between">
        <div>
          <h1 className="text-lg font-bold text-white">Trade Journal</h1>
          <p className="text-xs text-muted mt-0.5">Complete history of closed positions</p>
        </div>
        <button
          onClick={() => exportToCsv(filteredAndSorted)}
          className="flex items-center gap-2 text-xs px-3 py-1.5 rounded border border-border text-muted hover:text-white hover:border-white/20 transition-colors"
        >
          <Download size={12} />
          Export CSV
        </button>
      </div>

      {/* Summary */}
      <SummaryBar trades={filteredAndSorted} />

      {/* Filters */}
      <div className="nexus-card p-4">
        <div className="flex items-center gap-2 mb-3">
          <Filter size={13} className="text-muted" />
          <span className="text-xs font-semibold text-white">Filters</span>
        </div>
        <div className="flex flex-wrap gap-3">
          {/* Market */}
          <select
            value={market}
            onChange={(e) => { setMarket(e.target.value as typeof market); setPage(1); }}
            className="text-xs bg-card border border-border rounded px-2 py-1 text-white focus:outline-none focus:border-nexus-blue"
          >
            {MARKET_OPTIONS.map((o) => (
              <option key={o.value} value={o.value}>{o.label}</option>
            ))}
          </select>

          {/* Direction */}
          <select
            value={direction}
            onChange={(e) => { setDirection(e.target.value as typeof direction); setPage(1); }}
            className="text-xs bg-card border border-border rounded px-2 py-1 text-white focus:outline-none focus:border-nexus-blue"
          >
            <option value="all">All Directions</option>
            <option value="LONG">LONG</option>
            <option value="SHORT">SHORT</option>
          </select>

          {/* Date From */}
          <input
            type="date"
            value={dateFrom}
            onChange={(e) => { setDateFrom(e.target.value); setPage(1); }}
            className="text-xs bg-card border border-border rounded px-2 py-1 text-white focus:outline-none focus:border-nexus-blue"
            placeholder="From"
          />
          <input
            type="date"
            value={dateTo}
            onChange={(e) => { setDateTo(e.target.value); setPage(1); }}
            className="text-xs bg-card border border-border rounded px-2 py-1 text-white focus:outline-none focus:border-nexus-blue"
            placeholder="To"
          />

          {/* Min PnL */}
          <input
            type="number"
            value={minPnl}
            onChange={(e) => { setMinPnl(e.target.value); setPage(1); }}
            className="text-xs bg-card border border-border rounded px-2 py-1 text-white focus:outline-none focus:border-nexus-blue w-28"
            placeholder="Min P&L ($)"
          />

          {/* Reset */}
          {(market !== 'all' || direction !== 'all' || minPnl || dateFrom || dateTo) && (
            <button
              onClick={() => { setMarket('all'); setDirection('all'); setMinPnl(''); setDateFrom(''); setDateTo(''); setPage(1); }}
              className="text-xs text-nexus-red hover:text-nexus-red/80 transition-colors"
            >
              Clear filters
            </button>
          )}
        </div>

        {/* Sort */}
        <div className="flex items-center gap-2 mt-3">
          <span className="text-xs text-muted">Sort:</span>
          <SortButton label="Date" k="date" />
          <SortButton label="P&L $" k="pnl" />
          <SortButton label="P&L %" k="pnlPct" />
          <SortButton label="Duration" k="duration" />
          <span className="text-xs text-muted ml-auto">
            {filteredAndSorted.length} trade{filteredAndSorted.length !== 1 ? 's' : ''}
          </span>
        </div>
      </div>

      {/* Trade list */}
      {loading ? (
        <div className="nexus-card p-8 text-center text-muted text-sm">Loading trades…</div>
      ) : paginated.length === 0 ? (
        <div className="nexus-card p-8 text-center">
          <div className="text-muted text-sm">No trades match the current filters</div>
          <div className="text-muted/60 text-xs mt-1">Try adjusting your filters or check back after more trades close</div>
        </div>
      ) : (
        <div className="space-y-2">
          {paginated.map((trade) => (
            <TradeCard key={trade.id} trade={trade} />
          ))}
        </div>
      )}

      {/* Pagination */}
      {totalPages > 1 && (
        <div className="flex items-center justify-center gap-3 py-2">
          <button
            onClick={() => setPage((p) => Math.max(1, p - 1))}
            disabled={page === 1}
            className="p-1.5 rounded border border-border text-muted hover:text-white hover:border-white/20 disabled:opacity-40 disabled:cursor-not-allowed transition-colors"
          >
            <ChevronLeft size={14} />
          </button>
          <span className="text-xs text-muted">
            Page {page} of {totalPages}
          </span>
          <button
            onClick={() => setPage((p) => Math.min(totalPages, p + 1))}
            disabled={page === totalPages}
            className="p-1.5 rounded border border-border text-muted hover:text-white hover:border-white/20 disabled:opacity-40 disabled:cursor-not-allowed transition-colors"
          >
            <ChevronRight size={14} />
          </button>
        </div>
      )}
    </div>
  );
}
