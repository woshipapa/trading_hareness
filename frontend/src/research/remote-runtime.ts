export type RemoteFreshness = 'fresh' | 'aging' | 'stale' | 'missing' | 'invalid';

export function normalizeSymbol(value: unknown): string | null {
  const symbol = String(value ?? '').trim().toUpperCase();
  return /^\d{6}\.(SH|SZ|BJ)$/.test(symbol) ? symbol : null;
}

export function freshnessOf(value: unknown, nowMs = Date.now(), freshMs = 90_000, staleMs = 300_000): RemoteFreshness {
  if (!value) return 'missing';
  const timestamp = Date.parse(String(value));
  if (!Number.isFinite(timestamp)) return 'invalid';
  const age = nowMs - timestamp;
  if (age < 0) return 'fresh';
  if (age <= freshMs) return 'fresh';
  if (age <= staleMs) return 'aging';
  return 'stale';
}

export function freshnessLabel(value: RemoteFreshness): string {
  return ({ fresh: '新鲜', aging: '变旧', stale: '陈旧', missing: '缺失', invalid: '时间无效' } as Record<RemoteFreshness, string>)[value];
}

export function symbolFromRow(row: Record<string, unknown>): string | null {
  return normalizeSymbol(row.symbol ?? row.ts_code ?? row.stock_code);
}

export type ScanStageRow = {
  stage: string;
  milliseconds: number;
  share: number;
};

/**
 * Turn persisted research timing into safe, bounded display rows.
 * Unknown or malformed telemetry is ignored rather than rendered as NaN.
 */
export function scanStageRows(stageMs: unknown, totalMs: unknown): ScanStageRow[] {
  if (!stageMs || typeof stageMs !== 'object' || Array.isArray(stageMs)) return [];
  const total = Number(totalMs);
  const denominator = Number.isFinite(total) && total > 0 ? total : 0;
  return Object.entries(stageMs as Record<string, unknown>)
    .map(([stage, value]) => ({ stage, milliseconds: Number(value) }))
    .filter((row) => row.stage.length > 0 && Number.isFinite(row.milliseconds) && row.milliseconds >= 0)
    .map((row) => ({
      ...row,
      share: denominator ? Math.min(100, Math.round((row.milliseconds / denominator) * 1000) / 10) : 0,
    }));
}

export type ScanOverlapRow = { key: string; value: string };

/** Keep overlap labels stable while allowing newer telemetry keys to display. */
export function scanOverlapRows(overlap: unknown): ScanOverlapRow[] {
  if (!overlap || typeof overlap !== 'object' || Array.isArray(overlap)) return [];
  return Object.entries(overlap as Record<string, unknown>)
    .filter(([key, value]) => key.length > 0 && value !== null && value !== undefined)
    .map(([key, value]) => ({ key, value: String(value) }));
}
