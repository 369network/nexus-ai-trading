/**
 * GET  /api/settings  — Read all tunable settings from Supabase system_config
 * POST /api/settings  — Upsert settings to Supabase system_config
 *
 * Settings stored as key-value rows in system_config:
 *   llm_weights        → JSON blob
 *   risk_limits        → JSON blob
 *   circuit_thresholds → JSON blob
 */

import { NextResponse } from 'next/server';
import { createClient } from '@/lib/supabase';

export const dynamic = 'force-dynamic';

async function getConfig(key: string): Promise<unknown | null> {
  const supabase = createClient();
  const { data, error } = await supabase
    .from('system_config')
    .select('value')
    .eq('key', key)
    .maybeSingle();

  if (error || !data) return null;
  try { return JSON.parse(data.value as string); } catch { return data.value; }
}

async function upsertConfig(key: string, value: unknown): Promise<boolean> {
  const supabase = createClient();
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
