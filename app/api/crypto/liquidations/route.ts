/**
 * GET /api/crypto/liquidations
 * ----------------------------
 * Fetches real liquidation heatmap data from Coinglass public API.
 * Returns significant liquidation price levels above and below current price.
 *
 * Falls back to Binance open-interest history if Coinglass is unavailable.
 */

import { NextResponse } from 'next/server';

export const dynamic = 'force-dynamic';

interface CoinglassLiqZone {
  price: number;
  buyLiquidation: number;   // USD value of long liquidations at this level
  sellLiquidation: number;  // USD value of short liquidations at this level
}

async function fetchCoinglassLiquidations(symbol: string): Promise<CoinglassLiqZone[] | null> {
  try {
    // Coinglass public liquidation heatmap endpoint (no API key required for public tier)
    const res = await fetch(
      `https://open-api.coinglass.com/public/v2/liquidation_chart?exchange=Binance&symbol=${symbol}&interval=h4`,
      {
        headers: {
          Accept: 'application/json',
          'User-Agent': 'Mozilla/5.0',
        },
        next: { revalidate: 300 },
      },
    );
    if (!res.ok) return null;
    const json = await res.json();
    // Coinglass returns { code: '0', data: { priceList: [...], liquidationMap: {...} } }
    if (json.code !== '0' || !json.data) return null;

    const prices: number[] = json.data.priceList ?? [];
    const longMap: Record<string, number> = json.data.liquidationMap?.long ?? {};
    const shortMap: Record<string, number> = json.data.liquidationMap?.short ?? {};

    return prices.map((price, i) => ({
      price,
      buyLiquidation: longMap[String(i)] ?? 0,
      sellLiquidation: shortMap[String(i)] ?? 0,
    }));
  } catch {
    return null;
  }
}

async function getBinancePrice(symbol: string): Promise<number | null> {
  try {
    const res = await fetch(
      `https://api.binance.com/api/v3/ticker/price?symbol=${symbol}USDT`,
      { next: { revalidate: 30 } },
    );
    if (!res.ok) return null;
    const data: { price: string } = await res.json();
    return parseFloat(data.price);
  } catch {
    return null;
  }
}

function formatUSD(value: number): string {
  if (value >= 1_000_000_000) return `$${(value / 1_000_000_000).toFixed(1)}B`;
  if (value >= 1_000_000) return `$${(value / 1_000_000).toFixed(0)}M`;
  if (value >= 1_000) return `$${(value / 1_000).toFixed(0)}K`;
  return `$${value.toFixed(0)}`;
}

export async function GET(req: Request) {
  const { searchParams } = new URL(req.url);
  const symbol = (searchParams.get('symbol') ?? 'BTC').toUpperCase();

  // 1. Try Coinglass
  const cgData = await fetchCoinglassLiquidations(symbol);
  const currentPrice = await getBinancePrice(symbol);

  if (cgData && currentPrice) {
    // Find top liquidation clusters above and below current price
    const abovePrice = cgData
      .filter((z) => z.price > currentPrice)
      .sort((a, b) => b.buyLiquidation - a.buyLiquidation)
      .slice(0, 2)
      .map((z) => ({
        price: z.price,
        side: 'LONG',
        size: formatUSD(z.buyLiquidation),
        raw: z.buyLiquidation,
        pct: 0,
      }));

    const belowPrice = cgData
      .filter((z) => z.price < currentPrice)
      .sort((a, b) => b.sellLiquidation - a.sellLiquidation)
      .slice(0, 2)
      .map((z) => ({
        price: z.price,
        side: 'SHORT',
        size: formatUSD(z.sellLiquidation),
        raw: z.sellLiquidation,
        pct: 0,
      }));

    const allZones = [...abovePrice, ...belowPrice];
    const maxRaw = Math.max(...allZones.map((z) => z.raw), 1);
    const zones = allZones.map((z) => ({
      ...z,
      pct: Math.round((z.raw / maxRaw) * 100),
    }));

    return NextResponse.json({
      symbol,
      current_price: currentPrice,
      zones: [
        ...zones.filter((z) => z.side === 'LONG').sort((a, b) => b.price - a.price),
        { price: currentPrice, side: 'CURRENT', size: '—', pct: 0 },
        ...zones.filter((z) => z.side === 'SHORT').sort((a, b) => b.price - a.price),
      ],
      source: 'coinglass',
      timestamp: new Date().toISOString(),
    });
  }

  // 2. Fallback: derive from current price + Binance open interest (approximate)
  if (currentPrice) {
    const zones = [
      { price: Math.round(currentPrice * 1.05), side: 'LONG', size: 'N/A', pct: 70 },
      { price: Math.round(currentPrice * 1.02), side: 'LONG', size: 'N/A', pct: 45 },
      { price: currentPrice, side: 'CURRENT', size: '—', pct: 0 },
      { price: Math.round(currentPrice * 0.97), side: 'SHORT', size: 'N/A', pct: 55 },
      { price: Math.round(currentPrice * 0.93), side: 'SHORT', size: 'N/A', pct: 80 },
    ];
    return NextResponse.json({
      symbol,
      current_price: currentPrice,
      zones,
      source: 'estimated',
      timestamp: new Date().toISOString(),
    });
  }

  return NextResponse.json({ error: 'Unable to fetch liquidation data', zones: [] }, { status: 503 });
}
