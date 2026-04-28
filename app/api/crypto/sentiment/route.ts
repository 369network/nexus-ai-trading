import { NextResponse } from 'next/server';

// ── Types ─────────────────────────────────────────────────────────────────────

interface FngEntry {
  value: string;
  value_classification: string;
  timestamp: string;
  time_until_update?: string;
}

interface BinancePremiumIndex {
  symbol: string;
  markPrice: string;
  indexPrice: string;
  estimatedSettlePrice: string;
  lastFundingRate: string;
  interestRate: string;
  nextFundingTime: number;
  time: number;
}

interface BinanceOpenInterest {
  openInterest: string;
  symbol: string;
  time: number;
}

// ── Helpers ───────────────────────────────────────────────────────────────────

function deriveFundingSignal(annualizedPct: number): string {
  if (annualizedPct > 30) return 'HIGH_LONG_BIAS';
  if (annualizedPct < -10) return 'HIGH_SHORT_BIAS';
  return 'NEUTRAL';
}

function mapFngClassification(
  raw: string,
): 'Extreme Fear' | 'Fear' | 'Neutral' | 'Greed' | 'Extreme Greed' {
  const map: Record<string, 'Extreme Fear' | 'Fear' | 'Neutral' | 'Greed' | 'Extreme Greed'> = {
    'Extreme Fear': 'Extreme Fear',
    Fear: 'Fear',
    Neutral: 'Neutral',
    Greed: 'Greed',
    'Extreme Greed': 'Extreme Greed',
  };
  return map[raw] ?? 'Neutral';
}

function parseFunding(data: BinancePremiumIndex) {
  const rate = parseFloat(data.lastFundingRate);
  // 8h funding paid 3× per day → annualized %
  const annualized = rate * 3 * 365 * 100;
  return {
    symbol: data.symbol,
    rate,
    annualized,
    signal: deriveFundingSignal(annualized),
    nextFundingTime: data.nextFundingTime,
  };
}

async function fetchFundingRate(
  symbol: string,
): Promise<ReturnType<typeof parseFunding> | null> {
  try {
    const res = await fetch(
      `https://fapi.binance.com/fapi/v1/premiumIndex?symbol=${symbol}`,
      { next: { revalidate: 60 } },
    );
    if (!res.ok) return null;
    const data: BinancePremiumIndex = await res.json();
    return parseFunding(data);
  } catch {
    return null;
  }
}

async function fetchOpenInterest(): Promise<{
  symbol: string;
  openInterest: string;
  time: number;
} | null> {
  try {
    const res = await fetch(
      'https://fapi.binance.com/fapi/v1/openInterest?symbol=BTCUSDT',
      { next: { revalidate: 60 } },
    );
    if (!res.ok) return null;
    const data: BinanceOpenInterest = await res.json();
    return { symbol: data.symbol, openInterest: data.openInterest, time: data.time };
  } catch {
    return null;
  }
}

async function fetchFearGreed(): Promise<{
  value: number;
  classification: ReturnType<typeof mapFngClassification>;
  timestamp: string;
  previous_value: number;
  previous_close: number;
  weekly_average: number;
  monthly_average: number;
  history: { value: number; classification: string; timestamp: string }[];
} | null> {
  try {
    const res = await fetch('https://api.alternative.me/fng/?limit=30', {
      next: { revalidate: 300 },
    });
    if (!res.ok) return null;
    const json = await res.json();
    const entries: FngEntry[] = json.data ?? [];
    if (entries.length === 0) return null;

    const toNum = (e: FngEntry) => parseInt(e.value, 10);
    const current = entries[0];
    const prev = entries[1] ?? entries[0];

    // Compute weekly (7-day) and monthly (30-day) averages from the history window
    const weekSlice = entries.slice(0, 7);
    const monthSlice = entries.slice(0, 30);
    const avg = (arr: FngEntry[]) =>
      Math.round(arr.reduce((s, e) => s + toNum(e), 0) / arr.length);

    return {
      value: toNum(current),
      classification: mapFngClassification(current.value_classification),
      timestamp: new Date(parseInt(current.timestamp, 10) * 1000).toISOString(),
      previous_value: toNum(prev),
      previous_close: toNum(prev),
      weekly_average: avg(weekSlice),
      monthly_average: avg(monthSlice),
      history: entries.slice(0, 10).map((e) => ({
        value: toNum(e),
        classification: e.value_classification,
        timestamp: new Date(parseInt(e.timestamp, 10) * 1000).toISOString(),
      })),
    };
  } catch {
    return null;
  }
}

// ── Route Handler ─────────────────────────────────────────────────────────────

export async function GET() {
  const [fearGreed, btcFunding, ethFunding, solFunding, openInterest] = await Promise.all([
    fetchFearGreed(),
    fetchFundingRate('BTCUSDT'),
    fetchFundingRate('ETHUSDT'),
    fetchFundingRate('SOLUSDT'),
    fetchOpenInterest(),
  ]);

  // Primary funding reference (BTC) kept at top-level for backward compat
  const funding = btcFunding
    ? {
        symbol: btcFunding.symbol,
        rate: btcFunding.rate,
        annualized: btcFunding.annualized,
        signal: btcFunding.signal,
      }
    : null;

  const fundingRates = {
    BTC: btcFunding,
    ETH: ethFunding,
    SOL: solFunding,
  };

  return NextResponse.json({
    fearGreed,
    funding,
    fundingRates,
    openInterest,
    timestamp: new Date().toISOString(),
  });
}
