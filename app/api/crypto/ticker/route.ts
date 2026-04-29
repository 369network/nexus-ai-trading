/**
 * GET /api/crypto/ticker?symbols=BTCUSDT,ETHUSDT,SOLUSDT
 * -------------------------------------------------------
 * Real 24-hour ticker stats from Binance REST API.
 * Returns price, change%, high, low, volume for each symbol.
 */

import { NextResponse } from 'next/server';

export const dynamic = 'force-dynamic';

interface BinanceTicker {
  symbol: string;
  priceChange: string;
  priceChangePercent: string;
  lastPrice: string;
  highPrice: string;
  lowPrice: string;
  volume: string;
  quoteVolume: string;
  openPrice: string;
}

export async function GET(req: Request) {
  const { searchParams } = new URL(req.url);
  const rawSymbols = searchParams.get('symbols') ?? 'BTCUSDT,ETHUSDT,SOLUSDT,BNBUSDT,XRPUSDT,DOGEUSDT';
  const symbols = rawSymbols.split(',').map((s) => s.trim().toUpperCase());

  try {
    // Binance allows fetching multiple tickers at once
    const encoded = JSON.stringify(symbols);
    const res = await fetch(
      `https://api.binance.com/api/v3/ticker/24hr?symbols=${encodeURIComponent(encoded)}`,
      { next: { revalidate: 30 } },
    );

    if (!res.ok) {
      return NextResponse.json({ error: `Binance API error ${res.status}` }, { status: res.status });
    }

    const data: BinanceTicker[] = await res.json();

    const tickers = data.map((t) => ({
      symbol: t.symbol,
      price: parseFloat(t.lastPrice),
      change: parseFloat(t.priceChange),
      change_pct: parseFloat(t.priceChangePercent),
      high: parseFloat(t.highPrice),
      low: parseFloat(t.lowPrice),
      volume: parseFloat(t.volume),
      quote_volume: parseFloat(t.quoteVolume),
      open: parseFloat(t.openPrice),
    }));

    return NextResponse.json({ tickers, timestamp: new Date().toISOString() });
  } catch (err) {
    return NextResponse.json(
      { error: err instanceof Error ? err.message : String(err), tickers: [] },
      { status: 500 },
    );
  }
}
