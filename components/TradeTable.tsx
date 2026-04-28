'use client';

import { useState, useMemo } from 'react';
import {
  ArrowUpDown,
  ArrowUp,
  ArrowDown,
  Download,
  ExternalLink,
} from 'lucide-react';
import { cn, formatCurrency, formatPercent, formatTimeAgo, formatDateTime, getPnlColor, downloadCSV } from '@/lib/utils';
import type { Trade, TradeStatus } from '@/lib/types';

interface TradeTableProps {
  trades: Trade[];
  title?: string;
  isLoading?: boolean;
  showExport?: boolean;
}

type SortKey = keyof Trade;
type SortDir = 'asc' | 'desc';

const STATUS_STYLES: Record<TradeStatus, string> = {
  OPEN: 'bg-nexus-blue/10 text-nexus-blue border-nexus-blue/20',
  CLOSED: 'bg-muted/10 text-muted border-muted/20',
  PARTIAL: 'bg-nexus-yellow/10 text-nexus-yellow border-nexus-yellow/20',
  STOPPED_OUT: 'bg-nexus-red/10 text-nexus-red border-nexus-red/20',
  TAKE_PROFIT: 'bg-nexus-green/10 text-nexus-green border-nexus-green/20',
  CANCELLED: 'bg-gray-500/10 text-gray-500 border-gray-500/20',
};

const STATUS_LABELS: Record<TradeStatus, string> = {
  OPEN: 'OPEN',
  CLOSED: 'CLOSED',
  PARTIAL: 'PARTIAL',
  STOPPED_OUT: 'STOP',
  TAKE_PROFIT: 'TP',
  CANCELLED: 'CANCEL',
};

function SortableHeader({
  label,
  sortKey,
  currentKey,
  direction,
  onSort,
  align = 'left',
}: {
  label: string;
  sortKey: SortKey;
  currentKey: SortKey | null;
  direction: SortDir;
  onSort: (key: SortKey) => void;
  align?: 'left' | 'right';
}) {
  const isActive = currentKey === sortKey;

  return (
    <th
      className={cn(
        'py-2 px-3 text-xs font-medium text-muted uppercase tracking-wider cursor-pointer select-none hover:text-white transition-colors',
        align === 'right' && 'text-right'
      )}
      onClick={() => onSort(sortKey)}
    >
      <div className={cn('flex items-center gap-1', align === 'right' && 'justify-end')}>
        {label}
        {isActive ? (
          direction === 'asc' ? (
            <ArrowUp size={10} className="text-nexus-blue" />
          ) : (
            <ArrowDown size={10} className="text-nexus-blue" />
          )
        ) : (
          <ArrowUpDown size={10} className="opacity-30" />
        )}
      </div>
    </th>
  );
}

function LoadingSkeleton() {
  return (
    <>
      {Array.from({ length: 5 }).map((_, i) => (
        <tr key={i}>
          {Array.from({ length: 10 }).map((_, j) => (
            <td key={j} className="py-3 px-3">
              <div className="h-3 shimmer rounded w-full" />
            </td>
          ))}
        </tr>
      ))}
    </>
  );
}

export function TradeTable({
  trades,
  title = 'Trade History',
  isLoading = false,
  showExport = false,
}: TradeTableProps) {
  const [sortKey, setSortKey] = useState<SortKey | null>('entry_time');
  const [sortDir, setSortDir] = useState<SortDir>('desc');
  const [page, setPage] = useState(0);
  const PAGE_SIZE = 10;

  const handleSort = (key: SortKey) => {
    if (sortKey === key) {
      setSortDir(sortDir === 'asc' ? 'desc' : 'asc');
    } else {
      setSortKey(key);
      setSortDir('desc');
    }
    setPage(0);
  };

  const sortedTrades = useMemo(() => {
    if (!sortKey) return trades;
    return [...trades].sort((a, b) => {
      const aVal = a[sortKey];
      const bVal = b[sortKey];
      if (aVal === undefined || aVal === null) return 1;
      if (bVal === undefined || bVal === null) return -1;
      if (typeof aVal === 'number' && typeof bVal === 'number') {
        return sortDir === 'asc' ? aVal - bVal : bVal - aVal;
      }
      const aStr = String(aVal);
      const bStr = String(bVal);
      return sortDir === 'asc' ? aStr.localeCompare(bStr) : bStr.localeCompare(aStr);
    });
  }, [trades, sortKey, sortDir]);

  const paginatedTrades = sortedTrades.slice(page * PAGE_SIZE, (page + 1) * PAGE_SIZE);
  const totalPages = Math.ceil(sortedTrades.length / PAGE_SIZE);

  const handleExport = () => {
    downloadCSV(
      trades.map((t) => ({
        Symbol: t.symbol,
        Market: t.market,
        Direction: t.direction,
        Status: t.status,
        'Entry Price': t.entry_price,
        'Exit Price': t.exit_price ?? '',
        'Stop Loss': t.stop_loss,
        'Take Profit': t.take_profit_1,
        Size: t.quantity,
        'P&L': t.pnl ?? '',
        'P&L %': t.pnl_pct ?? '',
        'Entry Time': t.entry_time,
        'Exit Time': t.exit_time ?? '',
        'Duration (min)': t.duration_minutes ?? '',
        Strategy: t.strategy,
      })),
      `nexus_trades_${new Date().toISOString().slice(0, 10)}.csv`
    );
  };

  const sortHeaderProps = (key: SortKey, align: 'left' | 'right' = 'left') => ({
    sortKey: key,
    currentKey: sortKey,
    direction: sortDir,
    onSort: handleSort,
    align,
  });

  return (
    <div className="nexus-card flex flex-col">
      <div className="flex items-center justify-between px-4 py-3 border-b border-border">
        <div className="flex items-center gap-2">
          <h3 className="font-medium text-sm text-white">{title}</h3>
          <span className="text-xs bg-nexus-blue/10 text-nexus-blue border border-nexus-blue/20 rounded-full px-2 py-0.5">
            {trades.length}
          </span>
        </div>
        {showExport && trades.length > 0 && (
          <button
            onClick={handleExport}
            className="flex items-center gap-1.5 text-xs text-muted hover:text-white transition-colors"
          >
            <Download size={12} />
            Export CSV
          </button>
        )}
      </div>

      <div className="overflow-x-auto">
        <table className="w-full">
          <thead>
            <tr className="border-b border-border">
              <SortableHeader label="Symbol" {...sortHeaderProps('symbol')} />
              <SortableHeader label="Market" {...sortHeaderProps('market')} />
              <th className="py-2 px-3 text-xs font-medium text-muted uppercase tracking-wider text-right">Dir</th>
              <SortableHeader label="Entry" {...sortHeaderProps('entry_price', 'right')} />
              <SortableHeader label="Exit" {...sortHeaderProps('exit_price', 'right')} />
              <SortableHeader label="Size" {...sortHeaderProps('quantity', 'right')} />
              <SortableHeader label="P&L $" {...sortHeaderProps('pnl', 'right')} />
              <SortableHeader label="P&L %" {...sortHeaderProps('pnl_pct', 'right')} />
              <SortableHeader label="Duration" {...sortHeaderProps('duration_minutes', 'right')} />
              <th className="py-2 px-3 text-xs font-medium text-muted uppercase tracking-wider text-left">Strategy</th>
              <SortableHeader label="Status" {...sortHeaderProps('status')} />
            </tr>
          </thead>
          <tbody>
            {isLoading ? (
              <LoadingSkeleton />
            ) : paginatedTrades.length === 0 ? (
              <tr>
                <td colSpan={11} className="py-12 text-center">
                  <div className="text-muted text-sm">No trades found</div>
                </td>
              </tr>
            ) : (
              paginatedTrades.map((trade) => {
                const pnl = trade.pnl ?? trade.unrealized_pnl ?? 0;
                const pnlPct = trade.pnl_pct ?? trade.unrealized_pnl_pct ?? 0;
                const isOpen = trade.status === 'OPEN';
                const duration = trade.duration_minutes
                  ? trade.duration_minutes < 60
                    ? `${trade.duration_minutes}m`
                    : trade.duration_minutes < 1440
                    ? `${Math.round(trade.duration_minutes / 60)}h`
                    : `${Math.round(trade.duration_minutes / 1440)}d`
                  : '—';

                return (
                  <tr
                    key={trade.id}
                    className="border-b border-[#0f0f1a] hover:bg-white/[0.02] transition-colors"
                  >
                    <td className="py-3 px-3">
                      <span className="font-medium text-white text-sm">{trade.symbol}</span>
                    </td>
                    <td className="py-3 px-3">
                      <span className="text-xs text-muted capitalize">
                        {trade.market.replace('_', ' ')}
                      </span>
                    </td>
                    <td className="py-3 px-3 text-right">
                      <span
                        className={cn(
                          'text-xs font-bold',
                          trade.direction === 'LONG' ? 'text-nexus-green' :
                          trade.direction === 'SHORT' ? 'text-nexus-red' : 'text-nexus-yellow'
                        )}
                      >
                        {trade.direction === 'LONG' ? '▲' : trade.direction === 'SHORT' ? '▼' : '◆'}
                      </span>
                    </td>
                    <td className="py-3 px-3 text-right font-mono text-xs text-white">
                      {trade.entry_price.toFixed(4)}
                    </td>
                    <td className="py-3 px-3 text-right font-mono text-xs text-muted">
                      {trade.exit_price ? trade.exit_price.toFixed(4) : '—'}
                    </td>
                    <td className="py-3 px-3 text-right font-mono text-xs text-white">
                      {(trade.quantity ?? 0).toFixed(3)}
                    </td>
                    <td className="py-3 px-3 text-right">
                      <span className={cn('font-mono text-sm font-medium', getPnlColor(pnl))}>
                        {pnl !== 0 ? (pnl >= 0 ? '+' : '') + formatCurrency(pnl) : '—'}
                        {isOpen && pnl !== 0 && (
                          <span className="text-xs opacity-60"> *</span>
                        )}
                      </span>
                    </td>
                    <td className="py-3 px-3 text-right">
                      <span className={cn('font-mono text-xs', getPnlColor(pnlPct))}>
                        {pnlPct !== 0 ? (pnlPct >= 0 ? '+' : '') + pnlPct.toFixed(2) + '%' : '—'}
                      </span>
                    </td>
                    <td className="py-3 px-3 text-right">
                      <span className="text-xs text-muted font-mono">{duration}</span>
                    </td>
                    <td className="py-3 px-3">
                      <span className="text-xs text-muted">{trade.strategy}</span>
                    </td>
                    <td className="py-3 px-3">
                      <span
                        className={cn(
                          'badge text-xs border',
                          STATUS_STYLES[trade.status]
                        )}
                      >
                        {STATUS_LABELS[trade.status]}
                      </span>
                    </td>
                  </tr>
                );
              })
            )}
          </tbody>
        </table>
      </div>

      {/* Pagination */}
      {totalPages > 1 && (
        <div className="flex items-center justify-between px-4 py-3 border-t border-border">
          <span className="text-xs text-muted">
            {page * PAGE_SIZE + 1}–{Math.min((page + 1) * PAGE_SIZE, trades.length)} of {trades.length}
          </span>
          <div className="flex items-center gap-1">
            <button
              onClick={() => setPage(Math.max(0, page - 1))}
              disabled={page === 0}
              className="px-2 py-1 text-xs rounded bg-border text-white disabled:opacity-30 hover:bg-border-bright transition-colors"
            >
              Prev
            </button>
            {Array.from({ length: Math.min(totalPages, 5) }, (_, i) => {
              const pageNum = page <= 2
                ? i
                : page >= totalPages - 3
                ? totalPages - 5 + i
                : page - 2 + i;
              return pageNum >= 0 && pageNum < totalPages ? (
                <button
                  key={pageNum}
                  onClick={() => setPage(pageNum)}
                  className={cn(
                    'w-7 h-7 text-xs rounded transition-colors',
                    page === pageNum
                      ? 'bg-nexus-blue/20 text-nexus-blue border border-nexus-blue/30'
                      : 'bg-border text-white hover:bg-border-bright'
                  )}
                >
                  {pageNum + 1}
                </button>
              ) : null;
            })}
            <button
              onClick={() => setPage(Math.min(totalPages - 1, page + 1))}
              disabled={page >= totalPages - 1}
              className="px-2 py-1 text-xs rounded bg-border text-white disabled:opacity-30 hover:bg-border-bright transition-colors"
            >
              Next
            </button>
          </div>
        </div>
      )}

      {/* Open trade indicator */}
      {trades.some((t) => t.status === 'OPEN') && (
        <div className="px-4 py-2 border-t border-border">
          <span className="text-xs text-muted">* Unrealized P&L for open positions</span>
        </div>
      )}
    </div>
  );
}
