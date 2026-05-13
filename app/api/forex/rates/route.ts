/**
 * GET /api/forex/rates
 * ---------------------
 * Server-side proxy for Frankfurter.app exchange rates.
 * Returns pre-computed price + 24h change for all major USD pairs.
 *
 * Falls back to static approximate rates if Frankfurter is unreachable
 * so the frontend never shows empty data.
 */

import { NextResponse } from 'next/server';

export const dynamic = 'force-dynamic';

// Static fallback rates (approx mid-2025 values, used only when API is down)
const FALLBACK: Record<string, number> = {
  EURUSD: 1.0850,
  GBPUSD: 1.2750,
  USDJPY: 155.50,
  USDCHF: 0.9050,
  AUDUSD: 0.6450,
  USDCAD: 1.3650,
  NZDUSD: 0.5990,
};

const SYMBOLS_USD = ['EUR', 'GBP', 'JPY', 'CHF', 'AUD', 'CAD', 'NZD'];

// EUR-base cross → USD pair price mapping
function computePairFromUSDBase(sym: string, rates: Record<string, number>): number {
  if (sym === 'EURUSD') return rates['EUR'] ? 1 / rates['EUR'] : FALLBACK.EURUSD;
  if (sym === 'GBPUSD') return rates['GBP'] ? 1 / rates['GBP'] : FALLBACK.GBPUSD;
  if (sym === 'USDJPY') return rates['JPY'] ?? FALLBACK.USDJPY;
  if (sym === 'USDCHF') return rates['CHF'] ?? FALLBACK.USDCHF;
  if (sym === 'AUDUSD') return rates['AUD'] ? 1 / rates['AUD'] : FALLBACK.AUDUSD;
  if (sym === 'USDCAD') return rates['CAD'] ?? FALLBACK.USDCAD;
  if (sym === 'NZDUSD') return rates['NZD'] ? 1 / rates['NZD'] : FALLBACK.NZDUSD;
  return 0;
}

interface PairData {
  price: number;
  change: number;
  fallback?: boolean;
}

async function fetchFrankfurter(url: string): Promise<Record<string, number> | null> {
  try {
    const res = await fetch(url, {
      cache: 'no-store',
      signal: AbortSignal.timeout(6000),
    });
    if (!res.ok) return null;
    const json = await res.json();
    return json.rates ?? null;
  } catch {
    return null;
  }
}

export async function GET() {
  const PAIRS = ['EURUSD', 'GBPUSD', 'USDJPY', 'USDCHF', 'AUDUSD', 'USDCAD', 'NZDUSD'];

  // Fetch current and yesterday rates in parallel (USD base)
  const todayStr  = new Date().toISOString().slice(0, 10);
  const yesterday = new Date(Date.now() - 86400000).toISOString().slice(0, 10);

  const symsParam = SYMBOLS_USD.join(',');
  const [currentRates, prevRates] = await Promise.all([
    fetchFrankfurter(`https://api.frankfurter.app/latest?base=USD&symbols=${symsParam}`),
    fetchFrankfurter(`https://api.frankfurter.app/${yesterday}?base=USD&symbols=${symsParam}`),
  ]);

  const result: Record<string, PairData> = {};
  let usedFallback = false;

  for (const pair of PAIRS) {
    if (!currentRates) {
      // API completely down — use static fallback
      result[pair] = { price: FALLBACK[pair] ?? 1, change: 0, fallback: true };
      usedFallback = true;
      continue;
    }

    const price = computePairFromUSDBase(pair, currentRates);
    const prevPrice = prevRates ? computePairFromUSDBase(pair, prevRates) : price;
    const change = prevPrice ? ((price - prevPrice) / prevPrice) * 100 : 0;

    result[pair] = {
      price: parseFloat(price.toFixed(pair === 'USDJPY' ? 3 : 5)),
      change: parseFloat(change.toFixed(3)),
    };
  }

  return NextResponse.json(
    { pairs: result, timestamp: Date.now(), fallback: usedFallback },
    {
      headers: {
        'Cache-Control': 'public, s-maxage=60, stale-while-revalidate=300',
      },
    }
  );
}
