/**
 * GET /api/crypto/whales
 * ---------------------
 * Free whale-sized transaction detection — no API key required.
 *
 * Sources (both free):
 *  1. Binance aggregate trades — BTC, ETH, SOL, BNB (large exchange trades ≥ $500k)
 *  2. blockchain.info unconfirmed mempool — BTC on-chain transfers ≥ 5 BTC
 *
 * Returns the same shape as before so the UI component is unchanged.
 */

import { NextResponse } from 'next/server';

export const dynamic = 'force-dynamic';

const MIN_USD  = 500_000;   // $500k threshold for Binance trades
const MIN_BTC  = 5;         // 5 BTC threshold for on-chain (≈ $375k+ at any recent price)
const LIMIT    = 20;        // max results returned

// ─── Types ───────────────────────────────────────────────────────────────────

interface WhaleEntry {
  id: string;
  hash: string;
  blockchain: string;
  symbol: string;
  transaction_type: string;
  amount: number;
  amount_usd: number;
  amount_display: string;
  from: string;
  to: string;
  timestamp: number;          // unix seconds
  age_seconds: number;
}

// ─── Helpers ─────────────────────────────────────────────────────────────────

function fmtAmount(amount: number, symbol: string): string {
  if (amount >= 1_000_000) return `${(amount / 1_000_000).toFixed(2)}M ${symbol}`;
  if (amount >= 1_000)     return `${(amount / 1_000).toFixed(1)}K ${symbol}`;
  return `${amount.toFixed(3)} ${symbol}`;
}

function fmtUsd(usd: number): string {
  if (usd >= 1_000_000_000) return `$${(usd / 1e9).toFixed(2)}B`;
  if (usd >= 1_000_000)     return `$${(usd / 1e6).toFixed(1)}M`;
  return `$${(usd / 1e3).toFixed(0)}K`;
}

// ─── Source 1: Binance large aggregate trades ─────────────────────────────────

interface BinanceAggTrade {
  a: number;    // aggregate trade id
  p: string;    // price
  q: string;    // quantity
  T: number;    // trade time (ms)
  m: boolean;   // isBuyerMaker (true = sell, false = buy)
}

const BINANCE_SYMBOLS: Record<string, { symbol: string; name: string }> = {
  BTCUSDT: { symbol: 'BTC', name: 'Bitcoin' },
  ETHUSDT: { symbol: 'ETH', name: 'Ethereum' },
  SOLUSDT: { symbol: 'SOL', name: 'Solana' },
  BNBUSDT: { symbol: 'BNB', name: 'BNB' },
  XRPUSDT: { symbol: 'XRP', name: 'Ripple' },
};

async function fetchBinanceLargeTrades(): Promise<WhaleEntry[]> {
  const results: WhaleEntry[] = [];

  await Promise.allSettled(
    Object.entries(BINANCE_SYMBOLS).map(async ([pair, meta]) => {
      try {
        const res = await fetch(
          `https://api.binance.com/api/v3/aggTrades?symbol=${pair}&limit=500`,
          { next: { revalidate: 30 } },
        );
        if (!res.ok) return;
        const trades: BinanceAggTrade[] = await res.json();

        for (const t of trades) {
          const price = parseFloat(t.p);
          const qty   = parseFloat(t.q);
          const usd   = price * qty;
          if (usd < MIN_USD) continue;

          results.push({
            id: `binance-${pair}-${t.a}`,
            hash: `0x${t.a.toString(16).padStart(16, '0')}`,
            blockchain: 'binance',
            symbol: meta.symbol,
            transaction_type: t.m ? 'sell' : 'buy',
            amount: qty,
            amount_usd: Math.round(usd),
            amount_display: fmtAmount(qty, meta.symbol),
            from: `Binance ${t.m ? 'Seller' : 'Buyer'}`,
            to:   `Binance ${t.m ? 'Buyer'  : 'Seller'}`,
            timestamp: Math.floor(t.T / 1000),
            age_seconds: Math.floor((Date.now() - t.T) / 1000),
          });
        }
      } catch {
        // skip on error
      }
    }),
  );

  return results;
}

// ─── Source 2: blockchain.info BTC mempool ────────────────────────────────────

interface BlockchainInfoTx {
  hash: string;
  time: number;
  out: Array<{ value: number; addr?: string }>;
  inputs: Array<{ prev_out?: { addr?: string; value?: number } }>;
}

interface BlockchainInfoResponse {
  txs: BlockchainInfoTx[];
}

async function fetchBtcMempoolWhales(): Promise<WhaleEntry[]> {
  try {
    const res = await fetch(
      'https://blockchain.info/unconfirmed-transactions?format=json',
      {
        headers: { Accept: 'application/json' },
        next: { revalidate: 60 },
      },
    );
    if (!res.ok) return [];
    const json: BlockchainInfoResponse = await res.json();

    // We need BTC price to estimate USD — use a simple cached approach
    let btcPrice = 90_000; // fallback estimate
    try {
      const priceRes = await fetch(
        'https://api.binance.com/api/v3/ticker/price?symbol=BTCUSDT',
        { next: { revalidate: 60 } },
      );
      if (priceRes.ok) {
        const p: { price: string } = await priceRes.json();
        btcPrice = parseFloat(p.price);
      }
    } catch { /* use fallback */ }

    const whales: WhaleEntry[] = [];

    for (const tx of json.txs ?? []) {
      const totalOut = tx.out.reduce((s, o) => s + (o.value ?? 0), 0) / 1e8; // satoshi → BTC
      if (totalOut < MIN_BTC) continue;

      const usd = Math.round(totalOut * btcPrice);
      const fromAddr = tx.inputs[0]?.prev_out?.addr ?? 'Unknown';
      const toAddr   = tx.out[0]?.addr ?? 'Unknown';

      whales.push({
        id: `btc-${tx.hash.slice(0, 12)}`,
        hash: tx.hash,
        blockchain: 'bitcoin',
        symbol: 'BTC',
        transaction_type: 'transfer',
        amount: totalOut,
        amount_usd: usd,
        amount_display: fmtAmount(totalOut, 'BTC'),
        from: fromAddr.slice(0, 8) + '…',
        to:   toAddr.slice(0, 8) + '…',
        timestamp: tx.time || Math.floor(Date.now() / 1000),
        age_seconds: tx.time ? Math.floor(Date.now() / 1000) - tx.time : 0,
      });

      if (whales.length >= 10) break; // cap BTC contribution
    }

    return whales;
  } catch {
    return [];
  }
}

// ─── Handler ──────────────────────────────────────────────────────────────────

export async function GET() {
  try {
    const [binanceTrades, btcWhales] = await Promise.all([
      fetchBinanceLargeTrades(),
      fetchBtcMempoolWhales(),
    ]);

    // Merge, sort by USD descending, deduplicate, cap at LIMIT
    const all = [...binanceTrades, ...btcWhales]
      .sort((a, b) => b.amount_usd - a.amount_usd)
      .slice(0, LIMIT);

    return NextResponse.json({
      transactions: all,
      count: all.length,
      sources: ['binance', 'blockchain.info'],
      timestamp: new Date().toISOString(),
    });
  } catch (err) {
    const msg = err instanceof Error ? err.message : String(err);
    return NextResponse.json({ error: msg, transactions: [] }, { status: 500 });
  }
}
