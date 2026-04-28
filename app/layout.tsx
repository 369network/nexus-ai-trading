'use client';

import './globals.css';
import { Inter } from 'next/font/google';
import Link from 'next/link';
import { usePathname } from 'next/navigation';
import { useEffect, useState, useCallback } from 'react';
import {
  LayoutDashboard,
  Bitcoin,
  TrendingUp,
  Wheat,
  IndianRupee,
  DollarSign,
  Bot,
  Shield,
  BarChart3,
  BookOpen,
  Settings,
  Wifi,
  WifiOff,
  AlertTriangle,
  ChevronLeft,
  ChevronRight,
} from 'lucide-react';
import { useNexusStore } from '@/lib/store';
import {
  subscribeToSignals,
  subscribeToTrades,
  subscribeToRiskEvents,
  subscribeToPortfolioSnapshots,
  subscribeToAgentDecisions,
  getActiveTrades,
  getRecentSignals,
  getLatestPortfolioSnapshot,
} from '@/lib/supabase';
import { cn, formatCurrency, formatPercent, getPnlColor } from '@/lib/utils';
import { LivePriceTicker } from '@/components/LivePriceTicker';
import type { RealtimeChannel } from '@supabase/supabase-js';

const inter = Inter({ subsets: ['latin'] });

const NAV_ITEMS = [
  { href: '/', label: 'Overview', icon: LayoutDashboard },
  { href: '/crypto', label: 'Crypto', icon: Bitcoin },
  { href: '/forex', label: 'Forex', icon: TrendingUp },
  { href: '/commodities', label: 'Commodities', icon: Wheat },
  { href: '/indian-stocks', label: 'Indian Stocks', icon: IndianRupee },
  { href: '/us-stocks', label: 'US Stocks', icon: DollarSign },
  { divider: true },
  { href: '/agents', label: 'Agent Network', icon: Bot },
  { href: '/risk', label: 'Risk Monitor', icon: Shield },
  { href: '/performance', label: 'Performance', icon: BarChart3 },
  { href: '/journal', label: 'Trade Journal', icon: BookOpen },
  { href: '/settings', label: 'Settings', icon: Settings },
] as const;

function RootLayoutInner({ children }: { children: React.ReactNode }) {
  const pathname = usePathname();
  const [sidebarCollapsed, setSidebarCollapsed] = useState(false);

  const {
    portfolioState,
    systemStatus,
    isConnected,
    setConnected,
    addSignal,
    addTrade,
    updateTrade,
    addRiskEvent,
    updatePortfolio,
    updateAgentState,
    setSignalFeed,
    setActiveTrades,
  } = useNexusStore();

  // Bootstrap initial data
  useEffect(() => {
    const bootstrap = async () => {
      try {
        const [signals, trades, snapshot] = await Promise.all([
          getRecentSignals(undefined, 50),
          getActiveTrades(),
          getLatestPortfolioSnapshot(),
        ]);

        if (signals.length) setSignalFeed(signals);
        if (trades.length) setActiveTrades(trades);
        if (snapshot) updatePortfolio(snapshot);
      } catch (err) {
        console.error('[Bootstrap] Failed to load initial data:', err);
      }
    };

    bootstrap();
  }, [setSignalFeed, setActiveTrades, updatePortfolio]);

  // Setup realtime subscriptions
  useEffect(() => {
    const channels: RealtimeChannel[] = [];

    const signalChannel = subscribeToSignals((signal) => {
      addSignal(signal);
      setConnected(true);
    });
    channels.push(signalChannel);

    const tradeChannel = subscribeToTrades((trade, eventType) => {
      if (eventType === 'INSERT') addTrade(trade);
      else updateTrade(trade);
      setConnected(true);
    });
    channels.push(tradeChannel);

    const riskChannel = subscribeToRiskEvents((event) => {
      addRiskEvent(event);
    });
    channels.push(riskChannel);

    const portfolioChannel = subscribeToPortfolioSnapshots((snapshot) => {
      updatePortfolio(snapshot);
    });
    channels.push(portfolioChannel);

    const agentChannel = subscribeToAgentDecisions((decision) => {
      updateAgentState(decision.role, decision);
    });
    channels.push(agentChannel);

    // Heartbeat check
    const heartbeat = setInterval(() => {
      // In production, check Supabase connection status
      setConnected(true);
    }, 30000);

    return () => {
      channels.forEach((ch) => ch.unsubscribe());
      clearInterval(heartbeat);
    };
  }, [addSignal, addTrade, updateTrade, addRiskEvent, updatePortfolio, updateAgentState, setConnected]);

  const systemMode = systemStatus.paper_mode
    ? { label: 'PAPER', color: 'text-nexus-yellow', dot: 'warning' }
    : isConnected
      ? { label: 'LIVE', color: 'text-nexus-green', dot: 'active' }
      : { label: 'OFFLINE', color: 'text-nexus-red', dot: 'error' };

  const anyCircuitOpen = Object.values(systemStatus.circuit_breakers).some(Boolean);

  return (
    <div className="flex h-screen bg-background overflow-hidden">
      {/* Sidebar */}
      <aside
        className={cn(
          'flex flex-col bg-card border-r border-border transition-all duration-300 flex-shrink-0',
          sidebarCollapsed ? 'w-16' : 'w-56'
        )}
      >
        {/* Logo */}
        <div className="flex items-center justify-between px-4 py-5 border-b border-border">
          {!sidebarCollapsed && (
            <div>
              <span className="font-bold text-sm tracking-widest text-nexus-blue">NEXUS</span>
              <span className="font-bold text-sm tracking-widest text-nexus-green"> ALPHA</span>
            </div>
          )}
          <button
            onClick={() => setSidebarCollapsed(!sidebarCollapsed)}
            className="p-1.5 rounded-md text-muted hover:text-white hover:bg-white/5 transition-colors ml-auto"
          >
            {sidebarCollapsed ? (
              <ChevronRight size={14} />
            ) : (
              <ChevronLeft size={14} />
            )}
          </button>
        </div>

        {/* Nav */}
        <nav className="flex-1 overflow-y-auto py-3 px-2">
          {NAV_ITEMS.map((item, idx) => {
            if ('divider' in item) {
              return <div key={idx} className="my-2 border-t border-border" />;
            }
            const Icon = item.icon;
            const isActive = item.href === '/'
              ? pathname === '/'
              : pathname.startsWith(item.href);

            return (
              <Link
                key={item.href}
                href={item.href}
                className={cn(
                  'sidebar-link',
                  isActive && 'active',
                  sidebarCollapsed && 'justify-center px-2'
                )}
                title={sidebarCollapsed ? item.label : undefined}
              >
                <Icon size={16} className="flex-shrink-0" />
                {!sidebarCollapsed && (
                  <span>{item.label}</span>
                )}
              </Link>
            );
          })}
        </nav>

        {/* Sidebar footer: system status */}
        {!sidebarCollapsed && (
          <div className="px-3 py-3 border-t border-border">
            <div className="flex items-center gap-2 mb-1">
              <span className={cn('status-dot', systemMode.dot)} />
              <span className={cn('text-xs font-bold', systemMode.color)}>
                {systemMode.label} MODE
              </span>
            </div>
            {anyCircuitOpen && (
              <div className="flex items-center gap-1.5 mt-1">
                <AlertTriangle size={11} className="text-nexus-red" />
                <span className="text-nexus-red text-xs">Circuit Breaker Active</span>
              </div>
            )}
          </div>
        )}
      </aside>

      {/* Main area */}
      <div className="flex flex-col flex-1 overflow-hidden">
        {/* Header */}
        <header className="flex items-center justify-between px-6 py-3 bg-card border-b border-border flex-shrink-0 z-10">
          {/* Left: breadcrumb */}
          <div className="flex items-center gap-2">
            <span className="text-xs text-muted uppercase tracking-wider">
              {pathname === '/' ? 'Overview' : pathname.slice(1).replace(/-/g, ' ')}
            </span>
          </div>

          {/* Center: Live P&L ticker */}
          <div className="flex items-center gap-6">
            <div className="flex items-center gap-3">
              <div className="text-center">
                <div className="text-xs text-muted">Equity</div>
                <div className="font-mono text-sm font-semibold text-white">
                  {portfolioState.equity > 0 ? formatCurrency(portfolioState.equity) : '—'}
                </div>
              </div>
              <div className="w-px h-8 bg-border" />
              <div className="text-center">
                <div className="text-xs text-muted">Day P&L</div>
                <div
                  className={cn(
                    'font-mono text-sm font-semibold',
                    getPnlColor(portfolioState.dailyPnl)
                  )}
                >
                  {portfolioState.equity > 0 ? formatCurrency(portfolioState.dailyPnl) : '—'}{' '}
                  {portfolioState.equity > 0 && (
                  <span className="text-xs">
                    ({formatPercent(portfolioState.dailyPnlPct)})
                  </span>
                  )}
                </div>
              </div>
              <div className="w-px h-8 bg-border" />
              <div className="text-center">
                <div className="text-xs text-muted">Drawdown</div>
                <div
                  className={cn(
                    'font-mono text-sm font-semibold',
                    portfolioState.drawdown < -10 ? 'text-nexus-red' : 'text-nexus-yellow'
                  )}
                >
                  {portfolioState.equity > 0 ? formatPercent(portfolioState.drawdown) : '—'}
                </div>
              </div>
              <div className="w-px h-8 bg-border" />
              <div className="text-center">
                <div className="text-xs text-muted">Positions</div>
                <div className="font-mono text-sm font-semibold text-nexus-blue">
                  {portfolioState.openPositions}
                </div>
              </div>
            </div>
          </div>

          {/* Right: connection status */}
          <div className="flex items-center gap-3">
            {anyCircuitOpen && (
              <div className="flex items-center gap-1.5 bg-nexus-red/10 border border-nexus-red/20 rounded-md px-2.5 py-1">
                <AlertTriangle size={12} className="text-nexus-red" />
                <span className="text-nexus-red text-xs font-medium">CIRCUIT OPEN</span>
              </div>
            )}
            <div className="flex items-center gap-2">
              {isConnected ? (
                <Wifi size={14} className="text-nexus-green" />
              ) : (
                <WifiOff size={14} className="text-nexus-red" />
              )}
              <span className={cn('text-xs font-bold', systemMode.color)}>
                {systemMode.label}
              </span>
            </div>
          </div>
        </header>

        {/* Live price ticker */}
        <div className="flex-shrink-0 border-b border-border/50">
          <LivePriceTicker />
        </div>

        {/* Page content */}
        <main className="flex-1 overflow-y-auto bg-background">
          <div className="p-6">
            {children}
          </div>
        </main>
      </div>
    </div>
  );
}

export default function RootLayout({ children }: { children: React.ReactNode }) {
  return (
    <html lang="en" className="dark">
      <head>
        <title>NEXUS ALPHA — AI Trading Dashboard</title>
        <meta name="description" content="Multi-market AI trading system dashboard" />
        <meta name="viewport" content="width=device-width, initial-scale=1" />
        <link rel="icon" href="/favicon.ico" />
      </head>
      <body className={inter.className}>
        <RootLayoutInner>{children}</RootLayoutInner>
      </body>
    </html>
  );
}
