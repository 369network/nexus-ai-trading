/**
 * GET  /api/settings  — Read all tunable settings from Supabase system_config
 * POST /api/settings  — Upsert settings to Supabase system_config
 *
 * Settings stored as key-value rows in system_config:
 *   llm_weights       → JSON blob
 *   risk_limits       → JSON blob
 *   circuit_thresholds → JSON blob
 */

import { NextResponse } from 'next/server';

export const dynamic = 'force-dynamic';

const SUPABASE_URL  = process.env.SUPABASE_URL!;
const SERVICE_KEY   = process.env.SUPABASE_SERVICE_KEY!;

const HEADERS = {
  'Content-Type':  'application/json',
  apikey:          SERVICE_KEY,
  Authorization:   `Bearer ${SERVICE_KEY}`,
  Prefer:          'return=representation',
};

async function getConfig(key: string): Promise<unknown | null> {
  const res = await fetch(
    `${SUPABASE_URL}/rest/v1/system_config?key=eq.${encodeURIComponent(key)}&select=value`,
    { headers: HEADERS, signal: AbortSignal.timeout(5000) }
  );
  if (!res.ok) return null;
  const rows: { value: string }[] = await res.json();
  if (!rows.length) return null;
  try { return JSON.parse(rows[0].value); } catch { return rows[0].value; }
}

async function upsertConfig(key: string, value: unknown): Promise<boolean> {
  const res = await fetch(
    `${SUPABASE_URL}/rest/v1/system_config`,
    {
      method: 'POST',
      headers: { ...HEADERS, Prefer: 'resolution=merge-duplicates,return=minimal' },
      body: JSON.stringify({
        key,
        value:      JSON.stringify(value),
        value_type: 'json',
        description: `Dashboard settings: ${key}`,
        updated_at: new Date().toISOString(),
      }),
      signal: AbortSignal.timeout(5000),
    }
  );
  return res.ok || res.status === 201;
}

export async function GET() {
  try {
    const [weights, limits, thresholds] = await Promise.all([
      getConfig('llm_weights'),
      getConfig('risk_limits'),
      getConfig('circuit_thresholds'),
    ]);
    return NextResponse.json({ weights, limits, thresholds });
  } catch {
    return NextResponse.json({ weights: null, limits: null, thresholds: null });
  }
}

export async function POST(req: Request) {
  try {
    const body = await req.json();
    const { weights, limits, thresholds } = body;

    const ops: Promise<boolean>[] = [];
    if (weights    !== undefined) ops.push(upsertConfig('llm_weights',         weights));
    if (limits     !== undefined) ops.push(upsertConfig('risk_limits',         limits));
    if (thresholds !== undefined) ops.push(upsertConfig('circuit_thresholds',  thresholds));

    await Promise.all(ops);
    return NextResponse.json({ ok: true });
  } catch (err) {
    return NextResponse.json({ ok: false, error: String(err) }, { status: 500 });
  }
}
