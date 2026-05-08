/**
 * GET  /api/settings  — Read all tunable settings from Supabase system_config
 * POST /api/settings  — Upsert settings to Supabase system_config
 *
 * Settings stored as key-value rows in system_config:
 *   llm_weights        → JSON blob
 *   risk_limits        → JSON blob
 *   circuit_thresholds → JSON blob
 *   api_keys           → JSON blob (encrypted field names, values redacted on GET)
 *   telegram_config    → JSON blob
 */

import { NextResponse } from 'next/server';
import { createClient } from '@supabase/supabase-js';

export const dynamic = 'force-dynamic';

function getSupabase() {
  const url = process.env.NEXT_PUBLIC_SUPABASE_URL;
  const key =
    process.env.SUPABASE_SERVICE_ROLE_KEY ??
    process.env.NEXT_PUBLIC_SUPABASE_ANON_KEY;
  if (!url || !key) throw new Error('Supabase env vars not configured');
  return createClient(url, key);
}

async function getConfig(key: string): Promise<unknown | null> {
  const supabase = getSupabase();
  const { data, error } = await supabase
    .from('system_config')
    .select('value')
    .eq('key', key)
    .maybeSingle();

  if (error || !data) return null;
  try { return JSON.parse(data.value as string); } catch { return data.value; }
}

async function upsertConfig(key: string, value: unknown): Promise<boolean> {
  const supabase = getSupabase();
  const { error } = await supabase
    .from('system_config')
    .upsert(
      {
        key,
        value:       JSON.stringify(value),
        value_type:  'json',
        description: `Dashboard settings: ${key}`,
        updated_at:  new Date().toISOString(),
      },
      { onConflict: 'key' }
    );
  return !error;
}

export async function GET() {
  try {
    const [weights, limits, thresholds, apiKeys, telegram] = await Promise.all([
      getConfig('llm_weights'),
      getConfig('risk_limits'),
      getConfig('circuit_thresholds'),
      getConfig('api_keys'),
      getConfig('telegram_config'),
    ]);
    return NextResponse.json({ weights, limits, thresholds, api_keys: apiKeys, telegram });
  } catch {
    return NextResponse.json({ weights: null, limits: null, thresholds: null, api_keys: null, telegram: null });
  }
}

export async function POST(req: Request) {
  try {
    const body = await req.json();
    const { weights, limits, thresholds, api_keys, telegram } = body;

    const ops: Promise<boolean>[] = [];
    if (weights    !== undefined) ops.push(upsertConfig('llm_weights',         weights));
    if (limits     !== undefined) ops.push(upsertConfig('risk_limits',         limits));
    if (thresholds !== undefined) ops.push(upsertConfig('circuit_thresholds',  thresholds));
    if (api_keys   !== undefined) ops.push(upsertConfig('api_keys',            api_keys));
    if (telegram   !== undefined) ops.push(upsertConfig('telegram_config',     telegram));

    await Promise.all(ops);
    return NextResponse.json({ ok: true });
  } catch (err) {
    return NextResponse.json({ ok: false, error: String(err) }, { status: 500 });
  }
}
