/**
 * GET /api/crypto/whales
 * ---------------------
 * Real whale transactions from whale-alert.io API.
 * Returns the latest large on-chain transfers (≥ $500k USD).
 *
 * API docs: https://docs.whale-alert.io/
 */

import { NextResponse } from 'next/server';

export const dynamic = 'force-dynamic';

const WHALE_ALERT_KEY = process.env.WHALE_ALERT_API_KEY!;
const MIN_VALUE_USD   = 500_000;
const LIMIT           = 10; // free tier max

interface WhaleTransaction {
  id: number;
  blockchain: string;
  symbol: string;
  transaction_type: string;
  hash: string;
  from: { address: string; owner?: string; owner_type: string };
  to:   { address: string; owner?: string; owner_type: string };
  timestamp: number;
  amount: number;
  amount_usd: number;
}

interface WhaleAlertResponse {
  result: string;
  cursor?: string;
  count: number;
  transactions: WhaleTransaction[];
}

function formatOwner(party: WhaleTransaction['from']): string {
  if (party.owner) return party.owner;
  if (party.owner_type === 'exchange') return 'Exchange';
  if (party.owner_type === 'unknown')  return 'Unknown';
  return party.address.slice(0, 8) + '…';
}

function formatAmount(amount: number, symbol: string): string {
  if (amount >= 1_000_000) return `${(amount / 1_000_000).toFixed(1)}M ${symbol}`;
  if (amount >= 1_000)     return `${(amount / 1_000).toFixed(1)}K ${symbol}`;
  return `${amount.toFixed(2)} ${symbol}`;
}

export async function GET() {
  try {
    // Use `start` (unix timestamp) instead of `cursor=0` — cursor requires paid plan.
    // Free tier supports start/end within the last 24 hours.
    const start = Math.floor(Date.now() / 1000) - 3600; // last 1 hour
    const url =
      `https://api.whale-alert.io/v1/transactions` +
      `?api_key=${WHALE_ALERT_KEY}` +
      `&min_value=${MIN_VALUE_USD}` +
      `&limit=${LIMIT}` +
      `&start=${start}`;

    const res = await fetch(url, {
      next: { revalidate: 60 },
      headers: { Accept: 'application/json' },
    });

    if (!res.ok) {
      const text = await res.text().catch(() => '');
      // Return 200 with empty list so the dashboard shows an empty state rather than
      // an error badge. The Whale Alert free-tier key in Vercel may be expired — update
      // WHALE_ALERT_API_KEY in Vercel environment variables to restore live data.
      return NextResponse.json({
        transactions: [],
        count: 0,
        sources: ['whale-alert.io'],
        error: `Whale Alert API ${res.status}${text ? ': ' + text.slice(0, 120) : ''} — update WHALE_ALERT_API_KEY in Vercel env`,
        timestamp: new Date().toISOString(),
      });
    }

    const json: WhaleAlertResponse = await res.json();

    if (json.result !== 'success') {
      return NextResponse.json({ error: 'Whale Alert returned non-success', transactions: [] });
    }

    const transactions = (json.transactions ?? []).map((tx) => ({
      id:               tx.id,
      hash:             tx.hash,
      blockchain:       tx.blockchain,
      symbol:           tx.symbol.toUpperCase(),
      transaction_type: tx.transaction_type,
      amount:           tx.amount,
      amount_usd:       tx.amount_usd,
      amount_display:   formatAmount(tx.amount, tx.symbol.toUpperCase()),
      from:             formatOwner(tx.from),
      to:               formatOwner(tx.to),
      timestamp:        tx.timestamp,
      age_seconds:      Math.floor(Date.now() / 1000) - tx.timestamp,
    }));

    return NextResponse.json({
      transactions,
      count:     transactions.length,
      sources:   ['whale-alert.io'],
      timestamp: new Date().toISOString(),
    });
  } catch (err) {
    const msg = err instanceof Error ? err.message : String(err);
    return NextResponse.json({ error: msg, transactions: [] }, { status: 500 });
  }
}
