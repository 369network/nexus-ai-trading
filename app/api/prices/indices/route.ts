import { NextResponse } from 'next/server';

export const dynamic = 'force-dynamic';

const YAHOO_BASE = 'https://query1.finance.yahoo.com/v8/finance/chart';

async function fetchYahoo(symbol: string) {
  try {
    const res = await fetch(
      `${YAHOO_BASE}/${encodeURIComponent(symbol)}?interval=1d&range=5d`,
      {
        headers: {
          'User-Agent': 'Mozilla/5.0',
          'Accept': 'application/json',
        },
        next: { revalidate: 60 }, // cache 60s
      }
    );
    if (!res.ok) return null;
    const data = await res.json();
    const result = data.chart?.result?.[0];
    if (!result) return null;
    const meta = result.meta;
    const close = meta.regularMarketPrice;
    const prevClose = meta.chartPreviousClose || meta.previousClose;
    const change = close - prevClose;
    const changePct = (change / prevClose) * 100;
    return { price: close, change, changePct };
  } catch {
    return null;
  }
}

export async function GET() {
  const [spx, ndx, dji, rut, wti, natgas] = await Promise.all([
    fetchYahoo('^GSPC'),
    fetchYahoo('^NDX'),
    fetchYahoo('^DJI'),
    fetchYahoo('^RUT'),
    fetchYahoo('CL=F'),
    fetchYahoo('NG=F'),
  ]);

  return NextResponse.json({
    indices: { spx, ndx, dji, rut },
    commodities: { wti, natgas },
    timestamp: new Date().toISOString(),
  });
}
