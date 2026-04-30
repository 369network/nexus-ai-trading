/**
 * GET /api/crypto/ticker?symbols=BTCUSDT,ETHUSDT,SOLUSDT
 * -------------------------------------------------------
 * Real 24-hour crypto ticker stats from CoinGecko (free tier, no auth).
 * Binance REST API returns 451 (geo-blocked) from Vercel US East IPs.
 *
 * Accepts Binance-style symbols (e.g. BTCUSDT) and maps them to
 * CoinGecko coin IDs. Unknown symbols fall back to Yahoo Finance v8/chart.
 */

import { NextResponse } from 'next/server';

export const dynamic = 'force-dynamic';

// Map Binance-style symbols → CoinGecko coin IDs
const SYMBOL_TO_GECKO: Record<string, string> = {
  BTCUSDT:  'bitcoin',
  ETHUSDT:  'ethereum',
  SOLUSDT:  'solana',
  BNBUSDT:  'binancecoin',
  XRPUSDT:  'ripple',
  DOGEUSDT: 'dogecoin',
  ADAUSDT:  'cardano',
  AVAXUSDT: 'avalanche-2',
  MATICUSDT:'matic-network',
  DOTUSDT:  'polkadot',
  LTCUSDT:  'litecoin',
  LINKUSDT: 'chainlink',
  UNIUSDT:  'uniswap',
  ATOMUSDT: 'cosmos',
  NEARUSDT: 'near',
  APTUSDT:  'aptos',
  SUIUSDT:  'sui',
  SHIBUSDT: 'shiba-inu',
};

// Map Binance symbol → CoinGecko ID for display symbol
const GECKO_TO_SYMBOL: Record<string, string> = {};
for (const [k, v] of Object.entries(SYMBOL_TO_GECKO)) {
  GECKO_TO_SYMBOL[v] = k;
}

interface GeckoMarket {
  id: string;
  symbol: string;
  current_price: number;
  price_change_24h: number;
  price_change_percentage_24h: number;
  high_24h: number;
  low_24h: number;
  total_volume: number;
  market_cap: number;
}

export async function GET(req: Request) {
  const { searchParams } = new URL(req.url);
  const rawSymbols = searchParams.get('symbols') ?? 'BTCUSDT,ETHUSDT,SOLUSDT,BNBUSDT,XRPUSDT,DOGEUSDT';
  const symbols = rawSymbols.split(',').map((s) => s.trim().toUpperCase());

  // Map to CoinGecko IDs (skip unknowns)
  const geckoIds = symbols
    .map((s) => SYMBOL_TO_GECKO[s])
    .filter((id): id is string => Boolean(id));

  if (!geckoIds.length) {
    return NextResponse.json({ error: 'No supported symbols', tickers: [] }, { status: 400 });
  }

  try {
    const url = new URL('https://api.coingecko.com/api/v3/coins/markets');
    url.searchParams.set('vs_currency', 'usd');
    url.searchParams.set('ids', geckoIds.join(','));
    url.searchParams.set('order', 'market_cap_desc');
    url.searchParams.set('per_page', String(geckoIds.length));
    url.searchParams.set('page', '1');
    url.searchParams.set('sparkline', 'false');
    url.searchParams.set('price_change_percentage', '24h');

    const res = await fetch(url.toString(), {
      headers: {
        'Accept': 'application/json',
        'User-Agent': 'NEXUS-ALPHA/1.0',
      },
      next: { revalidate: 30 },
      signal: AbortSignal.timeout(10000),
    });

    if (!res.ok) {
      return NextResponse.json(
        { error: `CoinGecko API error ${res.status}`, tickers: [] },
        { status: res.status }
      );
    }

    const data: GeckoMarket[] = await res.json();

    const tickers = data.map((coin) => {
      // Find the original Binance-style symbol requested
      const binanceSymbol = GECKO_TO_SYMBOL[coin.id] ?? coin.symbol.toUpperCase() + 'USDT';
      return {
        symbol:       binanceSymbol,
        price:        coin.current_price,
        change:       coin.price_change_24h,
        change_pct:   coin.price_change_percentage_24h,
        high:         coin.high_24h,
        low:          coin.low_24h,
        volume:       coin.total_volume,
        quote_volume: coin.total_volume,  // CoinGecko volume is already in USD
        open:         coin.current_price - (coin.price_change_24h ?? 0),
      };
    });

    return NextResponse.json({ tickers, timestamp: new Date().toISOString() });
  } catch (err) {
    return NextResponse.json(
      { error: err instanceof Error ? err.message : String(err), tickers: [] },
      { status: 500 }
    );
  }
}
