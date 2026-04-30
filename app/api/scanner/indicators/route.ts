/**
 * GET /api/scanner/indicators?symbols=EURUSD=X,GBPUSD=X,GC=F,^GSPC
 * ------------------------------------------------------------------
 * Fetches 30-day daily OHLC from Yahoo Finance chart API and computes
 * real technical indicators (RSI-14, MACD, EMA-trend) for each symbol.
 *
 * Used by the Multi-Asset Scanner page to replace hardcoded rsi=50 for
 * forex, commodities, and US index symbols.
 */

import { NextResponse } from 'next/server';

export const dynamic = 'force-dynamic';

const YAHOO_CHART = 'https://query1.finance.yahoo.com/v8/finance/chart';

interface Indicators {
  rsi: number;
  macdBull: boolean;
  trendUp: boolean;
}

// ── RSI(14) ──────────────────────────────────────────────────────────────────

function computeRSI(closes: number[], period = 14): number {
  if (closes.length < period + 1) return 50;
  let gains = 0, losses = 0;
  for (let i = closes.length - period; i < closes.length; i++) {
    const diff = closes[i] - closes[i - 1];
    if (diff > 0) gains += diff;
    else losses += Math.abs(diff);
  }
  const avgGain = gains / period;
  const avgLoss = losses / period;
  if (avgLoss === 0) return 100;
  const rs = avgGain / avgLoss;
  return parseFloat((100 - 100 / (1 + rs)).toFixed(1));
}

// ── EMA ──────────────────────────────────────────────────────────────────────

function ema(closes: number[], period: number): number[] {
  if (closes.length < period) return closes.slice();
  const k = 2 / (period + 1);
  const result: number[] = [closes[0]];
  for (let i = 1; i < closes.length; i++) {
    result.push(closes[i] * k + result[i - 1] * (1 - k));
  }
  return result;
}

// ── MACD (12/26) bullish when MACD line > 0 ─────────────────────────────────

function computeMACD(closes: number[]): boolean {
  if (closes.length < 26) return closes[closes.length - 1] > closes[0];
  const ema12 = ema(closes, 12);
  const ema26 = ema(closes, 26);
  const macdLine = ema12[ema12.length - 1] - ema26[ema26.length - 1];
  return macdLine > 0;
}

// ── Fetch from Yahoo Finance ─────────────────────────────────────────────────

async function fetchIndicators(symbol: string): Promise<Indicators | null> {
  try {
    const url = `${YAHOO_CHART}/${encodeURIComponent(symbol)}?interval=1d&range=60d`;
    const res = await fetch(url, {
      headers: { 'User-Agent': 'Mozilla/5.0' },
      signal: AbortSignal.timeout(8000),
      next: { revalidate: 3600 }, // 1-hour cache — daily data doesn't change minute-to-minute
    });
    if (!res.ok) return null;
    const json = await res.json();
    const result = json?.chart?.result?.[0];
    if (!result) return null;
    const closes: number[] = result.indicators?.quote?.[0]?.close ?? [];
    const validCloses = closes.filter((c) => typeof c === 'number' && !isNaN(c));
    if (validCloses.length < 15) return null;

    const rsi = computeRSI(validCloses);
    const macdBull = computeMACD(validCloses);
    const trendUp = validCloses[validCloses.length - 1] > validCloses[validCloses.length - 6]; // 5-day trend

    return { rsi, macdBull, trendUp };
  } catch {
    return null;
  }
}

// ── Handler ──────────────────────────────────────────────────────────────────

export async function GET(req: Request) {
  const { searchParams } = new URL(req.url);
  const raw = searchParams.get('symbols') ?? '';
  const symbols = raw.split(',').map((s) => s.trim()).filter(Boolean);

  if (!symbols.length) {
    return NextResponse.json({ error: 'No symbols provided' }, { status: 400 });
  }

  // Fetch all in parallel (cap at 10 to avoid overloading Yahoo)
  const batch = symbols.slice(0, 10);
  const results = await Promise.all(batch.map(fetchIndicators));

  const data: Record<string, Indicators | null> = {};
  batch.forEach((sym, i) => {
    data[sym] = results[i];
  });

  return NextResponse.json({ data, timestamp: new Date().toISOString() });
}
