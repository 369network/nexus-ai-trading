'use client';

import { useMemo } from 'react';
import { cn } from '@/lib/utils';
import type { Trade } from '@/lib/types';

interface RiskHeatmapProps {
  activeTrades: Trade[];
}

const MARKETS = ['Crypto', 'Forex', 'Commodities', 'Indian', 'US Stocks'];
const RISK_FACTORS = ['P&L Risk', 'Correlation', 'Liquidity', 'Volatility', 'Concentration'];

function getRiskLevel(value: number): { color: string; label: string } {
  if (value < 0.3) return { color: 'bg-nexus-green/20 text-nexus-green border-nexus-green/20', label: 'LOW' };
  if (value < 0.6) return { color: 'bg-nexus-yellow/20 text-nexus-yellow border-nexus-yellow/20', label: 'MED' };
  if (value < 0.85) return { color: 'bg-orange-500/20 text-orange-400 border-orange-500/20', label: 'HIGH' };
  return { color: 'bg-nexus-red/20 text-nexus-red border-nexus-red/20', label: 'CRIT' };
}

export function RiskHeatmap({ activeTrades }: RiskHeatmapProps) {
  // Generate risk matrix from active trades + mock values
  const riskMatrix = useMemo(() => {
    return MARKETS.map((market) => {
      const marketTrades = activeTrades.filter((t) =>
        t.market.replace('_', ' ').toLowerCase().includes(market.toLowerCase().split(' ')[0])
      );

      const hasPositions = marketTrades.length > 0;

      return RISK_FACTORS.map((factor) => {
        if (!hasPositions) return 0.1; // Low risk if no positions

        // Simulate risk values based on factor + random
        const base: Record<string, number> = {
          'P&L Risk': 0.4 + Math.random() * 0.3,
          'Correlation': 0.3 + Math.random() * 0.4,
          'Liquidity': 0.1 + Math.random() * 0.2,
          'Volatility': 0.3 + Math.random() * 0.5,
          'Concentration': marketTrades.length > 2 ? 0.5 + Math.random() * 0.4 : 0.2 + Math.random() * 0.3,
        };
        return base[factor] ?? 0.3;
      });
    });
  }, [activeTrades]);

  // Circuit breaker status row
  const cbStatus = [false, false, true, false, false]; // index 2 = warning

  return (
    <div className="nexus-card p-4">
      <div className="flex items-center justify-between mb-4">
        <h3 className="font-medium text-sm text-white">Risk Exposure Heatmap</h3>
        <div className="flex items-center gap-3 text-xs">
          {[
            { label: 'Low < 30%', color: 'text-nexus-green' },
            { label: 'Med < 60%', color: 'text-nexus-yellow' },
            { label: 'High < 85%', color: 'text-orange-400' },
            { label: 'Critical', color: 'text-nexus-red' },
          ].map((item) => (
            <span key={item.label} className={cn('text-xs', item.color)}>
              {item.label}
            </span>
          ))}
        </div>
      </div>

      <div className="overflow-x-auto">
        <table className="w-full text-xs border-collapse">
          <thead>
            <tr>
              <th className="text-left pb-2 pr-3 font-normal text-muted w-24">Market</th>
              {RISK_FACTORS.map((factor) => (
                <th key={factor} className="text-center pb-2 px-1 font-normal text-muted">
                  {factor}
                </th>
              ))}
              <th className="text-center pb-2 px-1 font-normal text-muted">Circuit Breaker</th>
            </tr>
          </thead>
          <tbody>
            {MARKETS.map((market, mi) => {
              const marketTrades = activeTrades.filter((t) =>
                t.market.replace('_', ' ').toLowerCase().includes(market.toLowerCase().split(' ')[0])
              );

              return (
                <tr key={market}>
                  <td className="py-1.5 pr-3">
                    <div className="flex items-center gap-2">
                      <span className="text-white font-medium">{market}</span>
                      {marketTrades.length > 0 && (
                        <span className="text-nexus-blue">{marketTrades.length}</span>
                      )}
                    </div>
                  </td>
                  {riskMatrix[mi].map((risk, fi) => {
                    const level = getRiskLevel(risk);
                    return (
                      <td key={RISK_FACTORS[fi]} className="py-1.5 px-1 text-center">
                        <div
                          className={cn(
                            'inline-flex items-center justify-center px-2 py-1 rounded border text-xs font-medium w-full',
                            level.color
                          )}
                          title={`${(risk * 100).toFixed(0)}% risk`}
                        >
                          {(risk * 100).toFixed(0)}%
                        </div>
                      </td>
                    );
                  })}
                  <td className="py-1.5 px-1 text-center">
                    <div
                      className={cn(
                        'inline-flex items-center justify-center px-2 py-1 rounded border text-xs font-medium',
                        cbStatus[mi]
                          ? 'bg-nexus-red/10 text-nexus-red border-nexus-red/20'
                          : 'bg-nexus-green/10 text-nexus-green border-nexus-green/20'
                      )}
                    >
                      {cbStatus[mi] ? 'ALERT' : 'OK'}
                    </div>
                  </td>
                </tr>
              );
            })}
          </tbody>
        </table>
      </div>

      {/* Position correlation warnings */}
      {activeTrades.length > 1 && (
        <div className="mt-3 pt-3 border-t border-border">
          <div className="text-xs text-muted mb-2">Correlation Warnings</div>
          <div className="flex flex-wrap gap-2">
            <div className="flex items-center gap-1.5 text-xs bg-nexus-yellow/5 border border-nexus-yellow/20 rounded px-2 py-1">
              <span className="w-1.5 h-1.5 rounded-full bg-nexus-yellow" />
              <span className="text-nexus-yellow">BTC/ETH: 0.89 correlation</span>
            </div>
            <div className="flex items-center gap-1.5 text-xs bg-nexus-green/5 border border-nexus-green/20 rounded px-2 py-1">
              <span className="w-1.5 h-1.5 rounded-full bg-nexus-green" />
              <span className="text-nexus-green">EURUSD/GOLD: -0.32 (diversified)</span>
            </div>
          </div>
        </div>
      )}
    </div>
  );
}
