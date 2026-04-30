/**
 * GET /api/risk/correlations?symbols=BTC/USDT,ETH/USDT,SOL/USDT
 * ---------------------------------------------------------------
 * Computes real Pearson correlation coefficients from 30-day daily
 * returns fetched from Yahoo Finance. Used by the Risk page to replace
 * the static lookup table.
 *
 * Symbol mapping: crypto "BTC/USDT" → Yahoo "BTC-USD"
 */

import { NextResponse } from 'next/server';

export const dynamic = 'force-dynamic';

const YAHOO_CHART = 'https://query1.finance.yahoo.com/v8/finance/chart';

// ── Symbol mapping ────────────────────────────────────────────────────────────

function toYahoo(sym: string): string {
  // Crypto: "BTC/USDT" → "BTC-USD"
  if (sym.includes('/')) {
    const base = sym.split('/')[0];
    return `${base}-USD`;
  }
  return sym;
}

// ── Fetch 30-day daily closes ─────────────────────────────────────────────────

async function fetchCloses(symbol: string): Promise<number[] | null> {
  try {
    const yahoo = toYahoo(symbol);
    const url = `${YAHOO_CHART}/${encodeURIComponent(yahoo)}?interval=1d&range=35d`;
    const res = await fetch(url, {
      headers: { 'User-Agent': 'Mozilla/5.0' },
      signal: AbortSignal.timeout(8000),
      next: { revalidate: 3600 },
    });
    if (!res.ok) return null;
    const json = await res.json();
    const closes: number[] = json?.chart?.result?.[0]?.indicators?.quote?.[0]?.close ?? [];
    return closes.filter((c) => typeof c === 'number' && !isNaN(c));
  } catch {
    return null;
  }
}

// ── Daily returns ─────────────────────────────────────────────────────────────

function dailyReturns(closes: number[]): number[] {
  const out: number[] = [];
  for (let i = 1; i < closes.length; i++) {
    out.push((closes[i] - closes[i - 1]) / closes[i - 1]);
  }
  return out;
}

// ── Pearson correlation ───────────────────────────────────────────────────────

function pearson(a: number[], b: number[]): number {
  const n = Math.min(a.length, b.length);
  if (n < 5) return 0;
  const ax = a.slice(-n);
  const bx = b.slice(-n);
  const meanA = ax.reduce((s, v) => s + v, 0) / n;
  const meanB = bx.reduce((s, v) => s + v, 0) / n;
  let num = 0, da2 = 0, db2 = 0;
  for (let i = 0; i < n; i++) {
    const da = ax[i] - meanA;
    const db = bx[i] - meanB;
    num += da * db;
    da2 += da * da;
    db2 += db * db;
  }
  const denom = Math.sqrt(da2 * db2);
  if (denom === 0) return 1;
  return parseFloat((num / denom).toFixed(3));
}

// ── Handler ───────────────────────────────────────────────────────────────────

export async function GET(req: Request) {
  const { searchParams } = new URL(req.url);
  const raw = searchParams.get('symbols') ?? '';
  const symbols = raw.split(',').map((s) => s.trim()).filter(Boolean);

  if (symbols.length < 2) {
    return NextResponse.json({ symbols: [], matrix: [], timestamp: new Date().toISOString() });
  }

  // Fetch closes for all symbols in parallel
  const closesList = await Promise.all(symbols.map(fetchCloses));

  // Compute daily returns; null out if fetch failed
  const returnsList: (number[] | null)[] = closesList.map((c) =>
    c && c.length >= 6 ? dailyReturns(c) : null
  );

  // Build N×N correlation matrix; use fallback for missing data
  const matrix: number[][] = symbols.map((_, i) =>
    symbols.map((__, j) => {
      if (i === j) return 1.0;
      const ri = returnsList[i];
      const rj = returnsList[j];
      if (!ri || !rj) return 0;
      return pearson(ri, rj);
    })
  );

  return NextResponse.json({
    symbols,
    matrix,
    data_points: returnsList.map((r) => r?.length ?? 0),
    timestamp: new Date().toISOString(),
  });
}
