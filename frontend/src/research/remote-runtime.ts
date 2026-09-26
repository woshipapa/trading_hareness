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
