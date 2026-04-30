import { NextRequest, NextResponse } from 'next/server';
import type { PolymarketMarket } from '@/lib/types';

export const dynamic = 'force-dynamic';

const GAMMA_BASE = 'https://gamma-api.polymarket.com';

// eslint-disable-next-line @typescript-eslint/no-explicit-any
function mapGammaMarket(raw: any): PolymarketMarket | null {
  try {
    const outcomes: string[] = raw.outcomes ?? [];
    const prices: string[] = raw.outcomePrices ?? [];
    const tokenIds: string[] = raw.clobTokenIds ?? [];

    // We expect at minimum 2 outcomes (Yes / No)
    if (outcomes.length < 2 || prices.length < 2) return null;

    const yesIdx = outcomes.findIndex((o: string) => o.toLowerCase() === 'yes');
    const noIdx  = outcomes.findIndex((o: string) => o.toLowerCase() === 'no');
    const yi = yesIdx >= 0 ? yesIdx : 0;
    const ni = noIdx  >= 0 ? noIdx  : 1;

    return {
      id:           raw.conditionId ?? raw.id ?? '',
      question:     raw.question ?? '',
      description:  raw.description ?? '',
      category:     raw.category ?? '',
      market_slug:  raw.slug ?? '',
      yes_token_id: tokenIds[yi] ?? '',
      no_token_id:  tokenIds[ni] ?? '',
      yes_price:    parseFloat(prices[yi] ?? '0'),
      no_price:     parseFloat(prices[ni] ?? '0'),
      volume_24h:   parseFloat(raw.volume24hr ?? raw.volume24h ?? '0'),
      total_volume: parseFloat(raw.volume ?? '0'),
      active:       raw.active === true,
      closed:       raw.closed === true,
      resolved:     raw.resolved === true,
      resolution:   raw.resolution ?? undefined,
      end_date:     raw.endDate ?? undefined,
    };
  } catch {
    return null;
  }
}

export async function GET(req: NextRequest) {
  const { searchParams } = new URL(req.url);
  const limit    = parseInt(searchParams.get('limit') ?? '50', 10);
  const category = searchParams.get('category') ?? 'all';
  const minVol   = parseFloat(searchParams.get('min_volume') ?? '5000');

  try {
    const url = new URL(`${GAMMA_BASE}/markets`);
    url.searchParams.set('active',      'true');
    url.searchParams.set('closed',      'false');
    url.searchParams.set('order',       'volume24hr');
    url.searchParams.set('ascending',   'false');
    url.searchParams.set('limit',       String(Math.min(limit, 200)));

    const res = await fetch(url.toString(), {
      headers: { 'Accept': 'application/json' },
      next: { revalidate: 60 },
    });

    if (!res.ok) {
      return NextResponse.json(
        { markets: [], count: 0, timestamp: new Date().toISOString(), error: `Gamma API ${res.status}` },
        { status: 502 }
      );
    }

    // eslint-disable-next-line @typescript-eslint/no-explicit-any
    const raw: any[] = await res.json();
    const data = Array.isArray(raw) ? raw : [];

    let markets = data
      .map(mapGammaMarket)
      .filter((m): m is PolymarketMarket => m !== null)
      .filter((m) => m.volume_24h >= minVol);

    if (category && category !== 'all') {
      markets = markets.filter(
        (m) => (m.category ?? '').toLowerCase() === category.toLowerCase()
      );
    }

    return NextResponse.json({
      markets,
      count: markets.length,
      timestamp: new Date().toISOString(),
    });
  } catch (err) {
    console.error('[/api/polymarket/markets]', err);
    return NextResponse.json(
      { markets: [], count: 0, timestamp: new Date().toISOString(), error: 'Internal error' },
      { status: 500 }
    );
  }
}
