// ============================================================
// NEXUS ALPHA - Zustand Global State Store
// ============================================================

import { create } from 'zustand';
import { devtools, subscribeWithSelector } from 'zustand/middleware';
import type {
  NexusStore,
  PortfolioState,
  Signal,
  Trade,
  RiskEvent,
  AgentDecision,
  AgentRole,
  PortfolioSnapshot,
  SystemStatus,
} from './types';

const DEFAULT_PORTFOLIO: PortfolioState = {
  equity: 0,
  cash: 0,
  dailyPnl: 0,
  dailyPnlPct: 0,
  drawdown: 0,
  maxDrawdown: 0,
  openPositions: 0,
  exposurePct: 0,
  equityCurve: [],
};

const DEFAULT_SYSTEM_STATUS: SystemStatus = {
  paper_mode: true,
  markets_active: {
    crypto: false,
    forex: false,
    commodities: false,
    indian_stocks: false,
    us_stocks: false,
  },
  circuit_breakers: {
    daily_loss_limit: false,
    max_drawdown: false,
    position_concentration: false,
    correlation_limit: false,
    volatility_spike: false,
    api_failure: false,
  },
  last_heartbeat: new Date().toISOString(),
  uptime_seconds: 0,
  agent_count: 7,
  active_trades: 0,
};

export const useNexusStore = create<NexusStore>()(
  devtools(
    subscribeWithSelector((set, get) => ({
      // ---- Initial State ----
      portfolioState: DEFAULT_PORTFOLIO,
      signalFeed: [],
      activeTrades: [],
      riskEvents: [],
      agentStates: {},
      marketPrices: {},
      systemStatus: DEFAULT_SYSTEM_STATUS,
      isConnected: false,
      lastUpdate: null,

      // ---- Portfolio Actions ----
      updatePortfolio: (snapshot: PortfolioSnapshot) => {
        set((state) => {
          const ts = snapshot.timestamp ?? snapshot.created_at ?? new Date().toISOString();
          const newPoint = {
            time: ts,
            value: snapshot.equity,
          };
          const curve = [...state.portfolioState.equityCurve, newPoint].slice(-288); // 24h at 5min intervals

          // Support both old field names (realized_pnl_today/unrealized_pnl) and
          // new DB field names (daily_pnl, portfolio_heat).
          const dailyPnl = snapshot.daily_pnl
            ?? ((snapshot.realized_pnl_today ?? 0) + (snapshot.unrealized_pnl ?? 0));
          const baseEquity = snapshot.equity - dailyPnl || 1;
          const dailyPnlPct = snapshot.daily_pnl_pct
            ?? ((dailyPnl / baseEquity) * 100);
          const maxDrawdown = snapshot.max_drawdown_pct ?? snapshot.drawdown_pct ?? 0;
          const exposurePct = snapshot.exposure_pct
            ?? ((snapshot.portfolio_heat ?? 0) * 100);

          return {
            portfolioState: {
              equity: snapshot.equity,
              cash: snapshot.cash,
              dailyPnl,
              dailyPnlPct,
              drawdown: snapshot.drawdown_pct,
              maxDrawdown,
              openPositions: snapshot.open_positions,
              exposurePct,
              equityCurve: curve,
            },
            lastUpdate: new Date().toISOString(),
          };
        });
      },

      // ---- Signal Actions ----
      addSignal: (signal: Signal) => {
        set((state) => ({
          signalFeed: [signal, ...state.signalFeed].slice(0, 100),
          lastUpdate: new Date().toISOString(),
        }));
      },

      setSignalFeed: (signals: Signal[]) => {
        set({ signalFeed: signals.slice(0, 100) });
      },

      // ---- Trade Actions ----
      addTrade: (trade: Trade) => {
        set((state) => {
          const existing = state.activeTrades.find((t) => t.id === trade.id);
          if (existing) return state; // prevent duplicate
          return {
            activeTrades: [trade, ...state.activeTrades],
            lastUpdate: new Date().toISOString(),
          };
        });
      },

      updateTrade: (trade: Trade) => {
        set((state) => {
          const isOpen = trade.status === 'OPEN';
          const activeTrades = isOpen
            ? state.activeTrades.map((t) => (t.id === trade.id ? trade : t))
            : state.activeTrades.filter((t) => t.id !== trade.id);

          return {
            activeTrades,
            lastUpdate: new Date().toISOString(),
          };
        });
      },

      setActiveTrades: (trades: Trade[]) => {
        set({ activeTrades: trades });
      },

      // ---- Risk Event Actions ----
      addRiskEvent: (event: RiskEvent) => {
        set((state) => ({
          riskEvents: [event, ...state.riskEvents].slice(0, 50),
          lastUpdate: new Date().toISOString(),
        }));
      },

      setRiskEvents: (events: RiskEvent[]) => {
        set({ riskEvents: events.slice(0, 50) });
      },

      // ---- Agent State Actions ----
      updateAgentState: (role: AgentRole, decision: AgentDecision) => {
        set((state) => ({
          agentStates: {
            ...state.agentStates,
            [role]: decision,
          },
          lastUpdate: new Date().toISOString(),
        }));
      },

      // ---- Market Price Actions ----
      setMarketPrice: (symbol: string, price: number) => {
        set((state) => ({
          marketPrices: {
            ...state.marketPrices,
            [symbol]: price,
          },
        }));
      },

      setMarketPrices: (prices: Record<string, number>) => {
        set((state) => ({
          marketPrices: {
            ...state.marketPrices,
            ...prices,
          },
        }));
      },

      // ---- System Status Actions ----
      updateSystemStatus: (status: Partial<SystemStatus>) => {
        set((state) => ({
          systemStatus: {
            ...state.systemStatus,
            ...status,
          },
        }));
      },

      setConnected: (connected: boolean) => {
        set({ isConnected: connected });
      },

      setLastUpdate: (time: string) => {
        set({ lastUpdate: time });
      },
    })),
    { name: 'NexusAlphaStore' }
  )
);

// ---- Derived Selectors ----
export const selectPortfolio = (state: NexusStore) => state.portfolioState;
export const selectSignalFeed = (state: NexusStore) => state.signalFeed;
export const selectActiveTrades = (state: NexusStore) => state.activeTrades;
export const selectRiskEvents = (state: NexusStore) => state.riskEvents;
export const selectAgentStates = (state: NexusStore) => state.agentStates;
export const selectSystemStatus = (state: NexusStore) => state.systemStatus;
export const selectMarketPrices = (state: NexusStore) => state.marketPrices;
export const selectIsConnected = (state: NexusStore) => state.isConnected;
