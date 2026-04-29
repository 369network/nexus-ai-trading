/**
 * GET /api/forex/calendar
 * -----------------------
 * Real economic calendar events from Forex Factory public JSON feed.
 * Returns this week's high/medium impact events with actual forecasts & previous values.
 *
 * Source: https://nfs.faireconomy.media/ff_calendar_thisweek.json (public, no auth)
 */

import { NextResponse } from 'next/server';

export const dynamic = 'force-dynamic';

interface FFEvent {
  title: string;
  country: string;
  date: string;       // e.g. "2024-04-29T12:30:00-04:00"
  impact: 'High' | 'Medium' | 'Low' | 'Holiday';
  forecast: string;   // e.g. "185K" or ""
  previous: string;   // e.g. "206K" or ""
  actual: string;     // filled in when released
}

function currencyFromCountry(country: string): string {
  const map: Record<string, string> = {
    USD: 'USD', EUR: 'EUR', GBP: 'GBP', JPY: 'JPY',
    AUD: 'AUD', CAD: 'CAD', CHF: 'CHF', NZD: 'NZD',
    CNY: 'CNY', INR: 'INR',
  };
  return map[country] ?? country;
}

export async function GET() {
  try {
    // This week's calendar
    const res = await fetch('https://nfs.faireconomy.media/ff_calendar_thisweek.json', {
      next: { revalidate: 1800 }, // 30 min cache — events don't change often
      headers: {
        'User-Agent': 'Mozilla/5.0 (compatible; NEXUS-ALPHA/1.0)',
        Accept: 'application/json',
      },
    });

    if (!res.ok) {
      // Try next week too as fallback
      const res2 = await fetch('https://nfs.faireconomy.media/ff_calendar_nextweek.json', {
        next: { revalidate: 1800 },
        headers: { 'User-Agent': 'Mozilla/5.0', Accept: 'application/json' },
      });
      if (!res2.ok) {
        return NextResponse.json({ error: 'Calendar unavailable', events: [] }, { status: 503 });
      }
      const data2: FFEvent[] = await res2.json();
      return buildResponse(data2);
    }

    const data: FFEvent[] = await res.json();
    return buildResponse(data);
  } catch (err) {
    return NextResponse.json(
      { error: err instanceof Error ? err.message : String(err), events: [] },
      { status: 500 },
    );
  }
}

function buildResponse(data: FFEvent[]) {
  // Filter to High & Medium impact only, skip holidays
  const filtered = data
    .filter((e) => e.impact === 'High' || e.impact === 'Medium')
    .map((e) => ({
      title: e.title,
      currency: currencyFromCountry(e.country),
      country: e.country,
      datetime: e.date,
      impact: e.impact as 'High' | 'Medium',
      forecast: e.forecast || null,
      previous: e.previous || null,
      actual: e.actual || null,
      is_released: Boolean(e.actual),
    }))
    .sort((a, b) => new Date(a.datetime).getTime() - new Date(b.datetime).getTime());

  return NextResponse.json({
    events: filtered,
    count: filtered.length,
    timestamp: new Date().toISOString(),
  });
}
