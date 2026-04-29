/**
 * GET /api/stocks/earnings
 * ------------------------
 * Real upcoming earnings from Yahoo Finance quoteSummary API.
 * Uses /v10/finance/quoteSummary?modules=calendarEvents for each tracked ticker.
 *
 * Note: The old /v1/finance/earning_calendar endpoint returns 404 on free access.
 * This approach queries a predefined universe of major S&P 500 companies directly.
 */

import { NextResponse } from 'next/server';

export const dynamic = 'force-dynamic';

// Major S&P 500 / large-cap companies to track for earnings
const TICKERS = [
  'AAPL', 'MSFT', 'GOOGL', 'AMZN', 'NVDA', 'META', 'TSLA',
  'JPM', 'V', 'UNH', 'XOM', 'JNJ', 'WMT', 'MA', 'PG', 'HD', 'AVGO',
  'CVX', 'MRK', 'LLY', 'ABBV', 'COST', 'PEP', 'BAC', 'KO', 'TMO',
  'CSCO', 'ACN', 'MCD', 'IBM',
];

interface CalendarEventRaw {
  raw?: number;
  fmt?: string;
}

interface YFCalendarEvents {
  earnings?: {
    earningsDate?: CalendarEventRaw[];
    epsEstimate?: { raw?: number };
    epsActual?: { raw?: number };
    epsDifference?: { raw?: number };
    surprisePercent?: { raw?: number };
  };
}

interface YFQuoteSummaryResponse {
  quoteSummary?: {
    result?: Array<{
      calendarEvents?: YFCalendarEvents;
    }>;
    error?: unknown;
  };
}

interface YFQuote {
  symbol: string;
  longName?: string;
  shortName?: string;
  marketCap?: number;
}

interface EarningsEntry {
  ticker: string;
  earningsDate: string | null;
  epsEstimate: number | null;
  epsActual: number | null;
  surprisePct: number | null;
}

async function fetchCalendarEvents(ticker: string): Promise<EarningsEntry> {
  const empty: EarningsEntry = {
    ticker,
    earningsDate: null,
    epsEstimate: null,
    epsActual: null,
    surprisePct: null,
  };
  try {
    const res = await fetch(
      `https://query1.finance.yahoo.com/v10/finance/quoteSummary/${encodeURIComponent(ticker)}?modules=calendarEvents`,
      {
        headers: {
          'User-Agent':
            'Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0 Safari/537.36',
          Accept: 'application/json',
        },
        next: { revalidate: 3600 }, // 1h cache
      },
    );
    if (!res.ok) return empty;
    const json: YFQuoteSummaryResponse = await res.json();
    const cal = json.quoteSummary?.result?.[0]?.calendarEvents;
    if (!cal) return empty;
    const earningsDates = cal.earnings?.earningsDate;
    const nextFmt = earningsDates?.[0]?.fmt ?? null;
    return {
      ticker,
      earningsDate: nextFmt,
      epsEstimate: cal.earnings?.epsEstimate?.raw ?? null,
      epsActual: cal.earnings?.epsActual?.raw ?? null,
      surprisePct: cal.earnings?.surprisePercent?.raw ?? null,
    };
  } catch {
    return empty;
  }
}

async function fetchQuotes(
  symbols: string[],
): Promise<Record<string, { name: string; marketCap: number | null }>> {
  if (symbols.length === 0) return {};
  try {
    const res = await fetch(
      `https://query1.finance.yahoo.com/v7/finance/quote?symbols=${encodeURIComponent(
        symbols.join(','),
      )}&fields=longName,shortName,marketCap`,
      {
        headers: {
          'User-Agent': 'Mozilla/5.0',
          Accept: 'application/json',
        },
        next: { revalidate: 3600 },
      },
    );
    if (!res.ok) return {};
    const json = await res.json();
    const quotes: YFQuote[] = json.quoteResponse?.result ?? [];
    return Object.fromEntries(
      quotes.map((q) => [
        q.symbol,
        { name: q.longName ?? q.shortName ?? q.symbol, marketCap: q.marketCap ?? null },
      ]),
    );
  } catch {
    return {};
  }
}

function formatMarketCap(mc: number | null): string {
  if (!mc) return 'N/A';
  if (mc >= 1e12) return `${(mc / 1e12).toFixed(1)}T`;
  if (mc >= 1e9) return `${(mc / 1e9).toFixed(0)}B`;
  return `${(mc / 1e6).toFixed(0)}M`;
}

export async function GET() {
  try {
    const now = Date.now();
    const windowEnd = now + 14 * 86_400_000; // next 14 days
    const windowStart = now - 86_400_000;    // yesterday (catch "today" reports)

    // Fetch in batches of 10 to avoid Yahoo rate-limiting
    const BATCH_SIZE = 10;
    const allEntries: EarningsEntry[] = [];
    for (let i = 0; i < TICKERS.length; i += BATCH_SIZE) {
      const batch = TICKERS.slice(i, i + BATCH_SIZE);
      const results = await Promise.all(batch.map(fetchCalendarEvents));
      allEntries.push(...results);
    }

    // Filter to companies with a known upcoming earnings date in the window
    const upcoming = allEntries.filter((e) => {
      if (!e.earningsDate) return false;
      const d = new Date(e.earningsDate).getTime();
      return d >= windowStart && d <= windowEnd;
    });

    // Sort ascending by date
    upcoming.sort((a, b) => {
      const da = a.earningsDate ? new Date(a.earningsDate).getTime() : Infinity;
      const db = b.earningsDate ? new Date(b.earningsDate).getTime() : Infinity;
      return da - db;
    });

    // Take top 10
    const top = upcoming.slice(0, 10);

    // Fetch names + market caps
    const quotes = top.length > 0 ? await fetchQuotes(top.map((e) => e.ticker)) : {};

    const events = top.map((e) => ({
      symbol: e.ticker,
      company: quotes[e.ticker]?.name ?? e.ticker,
      datetime: e.earningsDate,
      eps_estimate: e.epsEstimate,
      eps_actual: e.epsActual,
      eps_surprise_pct: e.surprisePct,
      is_released: e.epsActual !== null,
      market_cap: formatMarketCap(quotes[e.ticker]?.marketCap ?? null),
    }));

    return NextResponse.json({ events, timestamp: new Date().toISOString() });
  } catch (err) {
    return NextResponse.json(
      { error: err instanceof Error ? err.message : String(err), events: [] },
      { status: 500 },
    );
  }
}
