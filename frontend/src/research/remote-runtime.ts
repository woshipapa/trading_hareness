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

export type ScanPerformanceHistoryRow = {
  scanId: string;
  observedAt: string;
  status: string;
  totalMs: number;
  persistenceStatus: string;
};

/** Convert the bounded scan history projection into oldest-to-newest bars. */
export function scanPerformanceHistoryRows(value: unknown): ScanPerformanceHistoryRow[] {
  if (!Array.isArray(value)) return [];
  return value
    .map((raw) => {
      if (!raw || typeof raw !== 'object' || Array.isArray(raw)) return null;
      const item = raw as Record<string, unknown>;
      const performance = item.scan_performance;
      if (!performance || typeof performance !== 'object' || Array.isArray(performance)) return null;
      const observedAt = String(item.observed_at ?? '').trim();
      const totalMs = Number((performance as Record<string, unknown>).total_ms);
      if (!observedAt || !Number.isFinite(totalMs) || totalMs < 0) return null;
      return {
        scanId: String(item.scan_id ?? observedAt),
        observedAt,
        status: String(item.status ?? 'unknown'),
        totalMs: Math.round(totalMs * 10) / 10,
        persistenceStatus: String((performance as Record<string, unknown>).persistence_status ?? 'unknown'),
      };
    })
    .filter((row): row is ScanPerformanceHistoryRow => row !== null)
    .reverse();
}

export type ExecutorCapacityRow = {
  key: string;
  workers: number;
  queueCapacity: number;
  occupied: number;
  availableSlots: number;
  utilizationPct: number;
  state: 'ready' | 'busy' | 'saturated';
};

/**
 * Normalize the local executor health payload for a bounded capacity view.
 *
 * The API is intentionally untyped JSON because the dashboard also reads
 * older owner releases.  Invalid or negative values are discarded, and the
 * displayed utilization is capped at 100% so a malformed health response
 * cannot imply extra capacity or render a broken progress bar.
 */
export function executorCapacityRows(value: unknown): ExecutorCapacityRow[] {
  if (!value || typeof value !== 'object' || Array.isArray(value)) return [];
  return Object.entries(value as Record<string, unknown>)
    .map(([key, raw]) => {
      if (!raw || typeof raw !== 'object' || Array.isArray(raw)) return null;
      const item = raw as Record<string, unknown>;
      const numberValue = (candidate: unknown, fallback?: number): number | null => {
        if (candidate === null || candidate === undefined || candidate === '') return fallback ?? null;
        const parsed = Number(candidate);
        return Number.isFinite(parsed) && parsed >= 0 ? Math.floor(parsed) : null;
      };
      const workers = numberValue(item.workers);
      const queueCapacity = numberValue(item.queue_capacity ?? item.queueCapacity, 0);
      const reportedOccupied = numberValue(item.occupied, 0);
      if (workers === null || queueCapacity === null || reportedOccupied === null) return null;
      const capacity = workers + queueCapacity;
      const occupied = Math.min(capacity, reportedOccupied);
      const availableSlots = Math.max(0, capacity - occupied);
      const utilizationPct = capacity ? Math.min(100, Math.round((occupied / capacity) * 1000) / 10) : 0;
      const state = occupied >= capacity && capacity > 0 ? 'saturated' : utilizationPct >= 80 ? 'busy' : 'ready';
      return { key, workers, queueCapacity, occupied, availableSlots, utilizationPct, state };
    })
    .filter((row): row is ExecutorCapacityRow => row !== null && row.workers + row.queueCapacity > 0);
}
