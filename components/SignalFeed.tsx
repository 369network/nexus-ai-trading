'use client';

import { useState, useMemo } from 'react';
import { ChevronDown, ChevronUp, Filter, Zap, TrendingUp, TrendingDown, Minus } from 'lucide-react';
import { useNexusStore } from '@/lib/store';
import {
  cn,
  formatTimeAgo,
  getDirectionBg,
  getStrengthLabel,
  getStrengthColor,
  formatNumber,
  formatCurrency,
} from '@/lib/utils';
import type { Signal, Market, Direction } from '@/lib/types';

interface SignalFeedProps {
  market?: Market;
  limit?: number;
  showFilter?: boolean;
}

function DirectionIcon({ direction }: { direction: Direction }) {
  if (direction === 'LONG') return <TrendingUp size={14} className="text-nexus-green" />;
  if (direction === 'SHORT') return <TrendingDown size={14} className="text-nexus-red" />;
  return <Minus size={14} className="text-nexus-yellow" />;
}

function SignalItem({
  signal,
  expanded,
  onToggle,
}: {
  signal: Signal;
  expanded: boolean;
  onToggle: () => void;
}) {
  const strengthColor = getStrengthColor(signal.strength);
  const totalVotes = Object.values(signal.agent_votes).reduce((s, v) => s + v, 0);
  const bullPct = totalVotes > 0 ? (signal.agent_votes.bull / totalVotes) * 100 : 50;
  const bearPct = totalVotes > 0 ? (signal.agent_votes.bear / totalVotes) * 100 : 50;

  return (
    <div
      className={cn(
        'border rounded-lg overflow-hidden transition-all',
        signal.direction === 'LONG'
          ? 'border-nexus-green/20 hover:border-nexus-green/30'
          : signal.direction === 'SHORT'
          ? 'border-nexus-red/20 hover:border-nexus-red/30'
          : 'border-nexus-yellow/20 hover:border-nexus-yellow/30'
      )}
    >
      {/* Main row */}
      <button
        onClick={onToggle}
        className="w-full flex items-center justify-between p-3 text-left hover:bg-white/2 transition-colors"
      >
        <div className="flex items-center gap-3">
          <DirectionIcon direction={signal.direction} />
          <div>
            <span className="text-sm font-bold text-white">{signal.symbol}</span>
            <span className="text-xs text-muted ml-2">{signal.market.replace('_', ' ')}</span>
          </div>
        </div>

        <div className="flex items-center gap-3">
          {/* Strength bar */}
          <div className="flex items-center gap-1.5">
            <div className="w-14 h-1.5 bg-border rounded-full overflow-hidden">
              <div
                className={cn(
                  'h-full rounded-full',
                  signal.direction === 'LONG' ? 'bg-nexus-green' :
                  signal.direction === 'SHORT' ? 'bg-nexus-red' : 'bg-nexus-yellow'
                )}
                style={{ width: `${signal.strength}%` }}
              />
            </div>
            <span className={cn('text-xs font-mono', strengthColor)}>
              {signal.strength.toFixed(0)}
            </span>
          </div>

          <span
            className={cn(
              'badge text-xs',
              signal.direction === 'LONG'
                ? 'bg-nexus-green/10 text-nexus-green border border-nexus-green/20'
                : signal.direction === 'SHORT'
                ? 'bg-nexus-red/10 text-nexus-red border border-nexus-red/20'
                : 'bg-nexus-yellow/10 text-nexus-yellow border border-nexus-yellow/20'
            )}
          >
            {signal.direction}
          </span>

          <span className="text-xs font-mono text-nexus-blue">
            {(signal.confidence * 100).toFixed(0)}%
          </span>

          <span className="text-xs text-muted">
            {formatTimeAgo(signal.created_at)}
          </span>

          {expanded ? (
            <ChevronUp size={12} className="text-muted" />
          ) : (
            <ChevronDown size={12} className="text-muted" />
          )}
        </div>
      </button>

      {/* Expanded details */}
      {expanded && (
        <div className="px-3 pb-3 border-t border-border pt-3 space-y-3 animate-fade-in">
          {/* Prices */}
          <div className="grid grid-cols-4 gap-2 text-xs">
            <div className="text-center">
              <div className="text-muted mb-0.5">Entry</div>
              <div className="font-mono text-white">{formatNumber(signal.entry, 2)}</div>
            </div>
            <div className="text-center">
              <div className="text-muted mb-0.5">Stop</div>
              <div className="font-mono text-nexus-red">{formatNumber(signal.stop_loss, 2)}</div>
            </div>
            <div className="text-center">
              <div className="text-muted mb-0.5">TP1</div>
              <div className="font-mono text-nexus-green">{formatNumber(signal.tp1, 2)}</div>
            </div>
            <div className="text-center">
              <div className="text-muted mb-0.5">R:R</div>
              <div className="font-mono text-nexus-blue">{signal.risk_reward.toFixed(1)}</div>
            </div>
          </div>

          {/* Agent votes */}
          <div>
            <div className="text-xs text-muted mb-2">Agent Votes</div>
            <div className="flex gap-1.5">
              {Object.entries(signal.agent_votes).map(([agent, votes]) => (
                <div
                  key={agent}
                  className="flex flex-col items-center text-xs"
                >
                  <div className="w-8 h-8 rounded-full bg-nexus-blue/10 border border-nexus-blue/20 flex items-center justify-center font-bold text-nexus-blue">
                    {votes}
                  </div>
                  <span className="text-muted capitalize mt-0.5 text-xs">
                    {agent.slice(0, 3)}
                  </span>
                </div>
              ))}
            </div>
          </div>

          {/* Bull/Bear distribution */}
          <div>
            <div className="flex justify-between text-xs text-muted mb-1">
              <span>Bearish {bearPct.toFixed(0)}%</span>
              <span>Bullish {bullPct.toFixed(0)}%</span>
            </div>
            <div className="flex h-1.5 rounded-full overflow-hidden">
              <div className="bg-nexus-red h-full" style={{ width: `${bearPct}%` }} />
              <div className="bg-nexus-green h-full" style={{ width: `${bullPct}%` }} />
            </div>
          </div>

          {/* Reasoning */}
          <div>
            <div className="text-xs text-muted mb-1">Reasoning</div>
            <p className="text-xs text-gray-300 leading-relaxed">{signal.reasoning}</p>
          </div>
        </div>
      )}
    </div>
  );
}

export function SignalFeed({ market, limit = 10, showFilter = false }: SignalFeedProps) {
  const { signalFeed } = useNexusStore();
  const [expandedId, setExpandedId] = useState<string | null>(null);
  const [directionFilter, setDirectionFilter] = useState<Direction | 'ALL'>('ALL');
  const [marketFilter, setMarketFilter] = useState<Market | 'ALL'>(market ?? 'ALL');

  const filteredSignals = useMemo(() => {
    let signals = [...signalFeed];

    if (market) {
      signals = signals.filter((s) => s.market === market);
    } else if (marketFilter !== 'ALL') {
      signals = signals.filter((s) => s.market === marketFilter);
    }

    if (directionFilter !== 'ALL') {
      signals = signals.filter((s) => s.direction === directionFilter);
    }

    return signals.slice(0, limit);
  }, [signalFeed, market, marketFilter, directionFilter, limit]);

  const toggleExpand = (id: string) => {
    setExpandedId(expandedId === id ? null : id);
  };

  return (
    <div className="nexus-card p-4 flex flex-col h-full">
      <div className="flex items-center justify-between mb-4 flex-shrink-0">
        <div className="flex items-center gap-2">
          <Zap size={14} className="text-nexus-yellow" />
          <h3 className="font-medium text-sm text-white">Signal Feed</h3>
          <span className="text-xs bg-nexus-blue/10 text-nexus-blue border border-nexus-blue/20 rounded-full px-2 py-0.5">
            {filteredSignals.length}
          </span>
        </div>

        {showFilter && (
          <div className="flex items-center gap-2">
            <Filter size={12} className="text-muted" />
            <div className="flex gap-1">
              {(['ALL', 'LONG', 'SHORT', 'NEUTRAL'] as const).map((dir) => (
                <button
                  key={dir}
                  onClick={() => setDirectionFilter(dir)}
                  className={cn(
                    'px-2 py-0.5 rounded text-xs transition-colors',
                    directionFilter === dir
                      ? dir === 'LONG' ? 'bg-nexus-green/10 text-nexus-green'
                        : dir === 'SHORT' ? 'bg-nexus-red/10 text-nexus-red'
                        : 'bg-nexus-blue/10 text-nexus-blue'
                      : 'text-muted hover:text-white'
                  )}
                >
                  {dir}
                </button>
              ))}
            </div>
          </div>
        )}
      </div>

      <div className="space-y-2 overflow-y-auto flex-1">
        {filteredSignals.length === 0 ? (
          <div className="flex flex-col items-center justify-center py-12 text-center">
            <Zap size={24} className="text-muted mb-2" />
            <p className="text-sm text-muted">No signals yet</p>
            <p className="text-xs text-muted mt-1">Waiting for market activity...</p>
          </div>
        ) : (
          filteredSignals.map((signal) => (
            <SignalItem
              key={signal.id}
              signal={signal}
              expanded={expandedId === signal.id}
              onToggle={() => toggleExpand(signal.id)}
            />
          ))
        )}
      </div>
    </div>
  );
}
