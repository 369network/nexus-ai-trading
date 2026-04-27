'use client';

/**
 * dashboard/components/Navbar.tsx
 * --------------------------------
 * Left-side navigation sidebar for the NEXUS ALPHA dashboard.
 *
 * Features:
 *  - Links to all 10 pages with active-state highlighting
 *  - Dark theme (bg-gray-900)
 *  - Live P&L shown in the header
 *  - Per-market open/closed status dots
 */

import Link from 'next/link';
import { usePathname } from 'next/navigation';
import React from 'react';
import { usePortfolioSummary } from '../hooks/useRealtimeData';
import MarketStatusBadge from './MarketStatusBadge';
import type { Market } from '../lib/types';

// ---------------------------------------------------------------------------
// Nav link definitions
// ---------------------------------------------------------------------------

interface NavItem {
  label: string;
  href: string;
  icon: string; // Unicode / emoji kept minimal; swap for an icon lib if desired
}

const NAV_ITEMS: NavItem[] = [
  { label: 'Overview',       href: '/',              icon: '▤' },
  { label: 'Crypto',         href: '/crypto',        icon: '₿' },
  { label: 'Forex',          href: '/forex',         icon: '💱' },
  { label: 'Commodities',    href: '/commodities',   icon: '🛢' },
  { label: 'Indian Stocks',  href: '/indian-stocks', icon: '🇮🇳' },
  { label: 'US Stocks',      href: '/us-stocks',     icon: '🇺🇸' },
  { label: 'Agents',         href: '/agents',        icon: '🤖' },
  { label: 'Risk',           href: '/risk',          icon: '⚡' },
  { label: 'Performance',    href: '/performance',   icon: '📈' },
  { label: 'Settings',       href: '/settings',      icon: '⚙' },
];

// Markets displayed as status dots in the sidebar footer
const MARKET_STATUS_MARKETS: Market[] = [
  'crypto',
  'forex',
  'commodities',
  'indian_stocks',
  'us_stocks',
];

// ---------------------------------------------------------------------------
// Helpers
// ---------------------------------------------------------------------------

function formatPnL(value: number): string {
  const sign = value >= 0 ? '+' : '';
  return `${sign}$${Math.abs(value).toLocaleString('en-US', {
    minimumFractionDigits: 2,
    maximumFractionDigits: 2,
  })}`;
}

function formatPct(value: number): string {
  const sign = value >= 0 ? '+' : '';
  return `${sign}${value.toFixed(2)}%`;
}

// ---------------------------------------------------------------------------
// Component
// ---------------------------------------------------------------------------

export default function Navbar() {
  const pathname = usePathname();
  const { data: portfolio, loading } = usePortfolioSummary();

  const dailyPnl = portfolio?.daily_pnl ?? 0;
  const dailyPnlPct = portfolio?.daily_pnl_pct ?? 0;
  const isProfit = dailyPnl >= 0;

  return (
    <aside className="flex h-screen w-56 shrink-0 flex-col bg-gray-900 text-gray-100 border-r border-gray-800">
      {/* ----------------------------------------------------------------- */}
      {/* Header / Logo + Live P&L                                           */}
      {/* ----------------------------------------------------------------- */}
      <div className="flex flex-col gap-1 px-4 py-5 border-b border-gray-800">
        <span className="text-lg font-bold tracking-wider text-white">
          NEXUS <span className="text-blue-400">ALPHA</span>
        </span>

        {loading ? (
          <span className="text-xs text-gray-500 animate-pulse">Loading…</span>
        ) : (
          <div className="flex flex-col">
            <span
              className={`text-sm font-semibold ${
                isProfit ? 'text-emerald-400' : 'text-red-400'
              }`}
            >
              {formatPnL(dailyPnl)}
            </span>
            <span
              className={`text-xs ${
                isProfit ? 'text-emerald-500' : 'text-red-500'
              }`}
            >
              {formatPct(dailyPnlPct)} today
            </span>
          </div>
        )}
      </div>

      {/* ----------------------------------------------------------------- */}
      {/* Navigation links                                                   */}
      {/* ----------------------------------------------------------------- */}
      <nav className="flex-1 overflow-y-auto py-3">
        <ul className="space-y-0.5 px-2">
          {NAV_ITEMS.map(({ label, href, icon }) => {
            const isActive =
              href === '/' ? pathname === '/' : pathname.startsWith(href);

            return (
              <li key={href}>
                <Link
                  href={href}
                  className={[
                    'flex items-center gap-3 rounded-md px-3 py-2 text-sm transition-colors',
                    isActive
                      ? 'bg-blue-600 text-white font-medium'
                      : 'text-gray-400 hover:bg-gray-800 hover:text-gray-100',
                  ].join(' ')}
                >
                  <span className="w-4 text-center text-base leading-none">
                    {icon}
                  </span>
                  <span>{label}</span>

                  {/* Active indicator bar on the right */}
                  {isActive && (
                    <span className="ml-auto h-1.5 w-1.5 rounded-full bg-white" />
                  )}
                </Link>
              </li>
            );
          })}
        </ul>
      </nav>

      {/* ----------------------------------------------------------------- */}
      {/* Market status indicators                                           */}
      {/* ----------------------------------------------------------------- */}
      <div className="border-t border-gray-800 px-4 py-4 space-y-2">
        <p className="text-xs font-semibold uppercase tracking-wider text-gray-500 mb-3">
          Markets
        </p>
        {MARKET_STATUS_MARKETS.map((market) => (
          <MarketStatusBadge key={market} market={market} compact />
        ))}
      </div>
    </aside>
  );
}
