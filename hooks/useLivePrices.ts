'use client';

import { useEffect, useRef, useState } from 'react';

export interface LivePrice {
  symbol: string;
  label: string;
  price: number;
  change24h: number;
  direction: 'up' | 'down' | 'flat';
  asset_class: 'crypto' | 'forex' | 'metal' | 'index';
  lastUpdate: number;
}

type PriceMap = Record<string, LivePrice>;

const BINANCE_WS_URL = 'wss://stream.binance.com:9443/ws/!miniTicker@arr';
const METALS_URL = 'https://api.metals.live/v1/spot/gold,silver';
const FOREX_URL = 'https://api.frankfurter.app/latest?base=USD&symbols=EUR,GBP,JPY';

const CRYPTO_SYMBOLS: Record<string, { label: string }> = {
  BTCUSDT: { label: 'BTC' },
  ETHUSDT: { label: 'ETH' },
  SOLUSDT: { label: 'SOL' },
};

function getDirection(change: number): 'up' | 'down' | 'flat' {
  if (change > 0.001) return 'up';
  if (change < -0.001) return 'down';
  return 'flat';
}

export function useLivePrices(): PriceMap {
  const [prices, setPrices] = useState<PriceMap>({});
  const wsRef = useRef<WebSocket | null>(null);
  const wsReconnectRef = useRef<ReturnType<typeof setTimeout> | null>(null);
  const metalsIntervalRef = useRef<ReturnType<typeof setInterval> | null>(null);
  const forexIntervalRef = useRef<ReturnType<typeof setInterval> | null>(null);
  const mountedRef = useRef(true);

  // --- Crypto via Binance WebSocket ---
  const connectBinance = () => {
    if (!mountedRef.current) return;

    const ws = new WebSocket(BINANCE_WS_URL);
    wsRef.current = ws;

    ws.onmessage = (event) => {
      if (!mountedRef.current) return;
      try {
        const tickers: Array<{ s: string; c: string; P: string }> = JSON.parse(event.data);
        const updates: Partial<PriceMap> = {};

        for (const ticker of tickers) {
          const meta = CRYPTO_SYMBOLS[ticker.s];
          if (!meta) continue;

          const price = parseFloat(ticker.c);
          const change24h = parseFloat(ticker.P);

          updates[ticker.s] = {
            symbol: ticker.s,
            label: meta.label,
            price,
            change24h,
            direction: getDirection(change24h),
            asset_class: 'crypto',
            lastUpdate: Date.now(),
          };
        }

        if (Object.keys(updates).length > 0) {
          setPrices((prev) => ({ ...prev, ...updates }));
        }
      } catch {
        // Silently ignore parse errors
      }
    };

    ws.onclose = () => {
      if (!mountedRef.current) return;
      wsReconnectRef.current = setTimeout(connectBinance, 3000);
    };

    ws.onerror = () => {
      ws.close();
    };
  };

  // --- Metals polling ---
  const fetchMetals = async () => {
    if (!mountedRef.current) return;
    try {
      const res = await fetch(METALS_URL);
      if (!res.ok) return;
      const data: Array<{ metal: string; price: number }> = await res.json();

      const updates: Partial<PriceMap> = {};
      for (const item of data) {
        if (item.metal === 'gold') {
          updates['XAU'] = {
            symbol: 'XAU',
            label: 'XAU/USD',
            price: item.price,
            change24h: 0,
            direction: 'flat',
            asset_class: 'metal',
            lastUpdate: Date.now(),
          };
        } else if (item.metal === 'silver') {
          updates['XAG'] = {
            symbol: 'XAG',
            label: 'XAG/USD',
            price: item.price,
            change24h: 0,
            direction: 'flat',
            asset_class: 'metal',
            lastUpdate: Date.now(),
          };
        }
      }

      if (mountedRef.current && Object.keys(updates).length > 0) {
        setPrices((prev) => ({ ...prev, ...updates }));
      }
    } catch {
      // Keep last known price on error
    }
  };

  // --- Forex polling ---
  const fetchForex = async () => {
    if (!mountedRef.current) return;
    try {
      const res = await fetch(FOREX_URL);
      if (!res.ok) return;
      const data: { rates: { EUR: number; GBP: number; JPY: number } } = await res.json();
      const { EUR, GBP, JPY } = data.rates;

      const now = Date.now();
      const updates: Partial<PriceMap> = {};

      // EUR rate from Frankfurter is how many EUR per 1 USD
      // So EUR/USD = 1 / EUR_rate (price of 1 EUR in USD)
      if (EUR) {
        const eurusd = 1 / EUR;
        updates['EURUSD'] = {
          symbol: 'EURUSD',
          label: 'EUR/USD',
          price: eurusd,
          change24h: 0,
          direction: 'flat',
          asset_class: 'forex',
          lastUpdate: now,
        };
      }

      // GBP/USD = 1 / GBP_rate
      if (GBP) {
        const gbpusd = 1 / GBP;
        updates['GBPUSD'] = {
          symbol: 'GBPUSD',
          label: 'GBP/USD',
          price: gbpusd,
          change24h: 0,
          direction: 'flat',
          asset_class: 'forex',
          lastUpdate: now,
        };
      }

      // USD/JPY = JPY rate directly (JPY per 1 USD)
      if (JPY) {
        updates['USDJPY'] = {
          symbol: 'USDJPY',
          label: 'USD/JPY',
          price: JPY,
          change24h: 0,
          direction: 'flat',
          asset_class: 'forex',
          lastUpdate: now,
        };
      }

      if (mountedRef.current && Object.keys(updates).length > 0) {
        setPrices((prev) => ({ ...prev, ...updates }));
      }
    } catch {
      // Keep last known price on error
    }
  };

  useEffect(() => {
    mountedRef.current = true;

    // Start Binance WebSocket
    connectBinance();

    // Fetch metals immediately, then every 30s
    fetchMetals();
    metalsIntervalRef.current = setInterval(fetchMetals, 30_000);

    // Fetch forex immediately, then every 60s
    fetchForex();
    forexIntervalRef.current = setInterval(fetchForex, 60_000);

    return () => {
      mountedRef.current = false;

      if (wsRef.current) {
        wsRef.current.onclose = null; // Prevent reconnect loop on intentional close
        wsRef.current.close();
        wsRef.current = null;
      }
      if (wsReconnectRef.current) {
        clearTimeout(wsReconnectRef.current);
        wsReconnectRef.current = null;
      }
      if (metalsIntervalRef.current) {
        clearInterval(metalsIntervalRef.current);
        metalsIntervalRef.current = null;
      }
      if (forexIntervalRef.current) {
        clearInterval(forexIntervalRef.current);
        forexIntervalRef.current = null;
      }
    };
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, []);

  return prices;
}
