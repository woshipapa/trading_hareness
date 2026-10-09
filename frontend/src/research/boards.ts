// Helpers for the data-source and strategy boards (quant-research
// /api/v1/datasources/board and /api/v1/strategies/board, through the relay).

export type BoardCapability = {
  capability: string; label: string; grain?: string | null; priority: number; status: string;
  store?: string | null; decision_eligible?: boolean; notes?: string;
};
export type BoardHealth = {
  capability: string; market?: string | null; consecutive_failures?: number | null; last_success_at?: string | null;
  last_failure_at?: string | null; last_error?: string | null; last_latency_ms?: number | null;
  last_row_count?: number | null; circuit_open?: boolean; circuit_open_until?: string | null;
};
export type BoardSource = {
  key: string; label: string; upstream?: string; license?: string; protocol?: string; cost?: string; risks?: string;
  order: number; lifecycle: string; verdict: string; reasons: string[]; last_success_at?: string | null;
  capabilities: BoardCapability[]; health: BoardHealth[];
};
export type DatasourceBoard = {
  observed_at: string; in_session: boolean; summary: Record<string, number>; sources: BoardSource[];
  alerts: { key: string; label: string; verdict: string; reasons: string[] }[]; definitions?: Record<string, string>;
};
export type StrategyInput = {
  capability: string; label: string; required: boolean; purpose?: string; source?: string | null;
  binding_status?: string | null; source_verdict: string; fallbacks?: { source: string; status: string; verdict?: string | null }[];
};
export type BoardStrategy = {
  key: string; model_version: string; maturity: string; live_effect: string; alert_effect: string; deprecated: boolean;
  description: string; readiness: string; blocking_inputs: string[]; weak_inputs: string[]; inputs: StrategyInput[];
  ledger_lines: Record<string, { as_of_date: string; candidates: number } | null>;
};
export type StrategyBoard = {
  observed_at: string; summary: Record<string, number>; strategies: BoardStrategy[]; definitions?: Record<string, string>;
};

export const VERDICT_LABELS: Record<string, string> = {
  healthy: '健康', degraded: '降级', failing: '失败', circuit_open: '熔断', stale: '陈旧', never_succeeded: '从未成功',
  unmonitored: '无监控', dormant: '休眠', retired: '已退役', unserved: '无来源',
};
export const VERDICT_TYPES: Record<string, string> = {
  healthy: 'success', degraded: 'warning', failing: 'danger', circuit_open: 'danger', stale: 'danger',
  never_succeeded: 'danger', unmonitored: 'info', dormant: 'info', retired: 'info', unserved: 'danger',
};
export const READINESS_LABELS: Record<string, string> = {
  ready: '数据就绪', degraded: '部分降级', blocked: '输入受阻', no_declared_needs: '未声明数据需求',
};
export const READINESS_TYPES: Record<string, string> = {
  ready: 'success', degraded: 'warning', blocked: 'danger', no_declared_needs: 'info',
};
export const STATUS_LABELS: Record<string, string> = {
  live_verified: '在用·已验证', declared: '在用·待验证', dormant: '休眠', unsupported: '上游拒绝', retired: '已退役',
};

/** "3 分钟前", "5.2 小时前", "12 天前" from an ISO time. */
export function ageText(iso: string | null | undefined, now: number = Date.now()): string {
  if (!iso) return '从未';
  const at = Date.parse(iso);
  if (!Number.isFinite(at)) return iso;
  const minutes = Math.max(0, (now - at) / 60_000);
  if (minutes < 60) return `${Math.round(minutes)} 分钟前`;
  const hours = minutes / 60;
  if (hours < 48) return `${hours.toFixed(1)} 小时前`;
  return `${Math.round(hours / 24)} 天前`;
}

export function activeCapabilityCount(source: BoardSource): number {
  return source.capabilities.filter((item) => item.status === 'live_verified' || item.status === 'declared').length;
}

export function failingHealthCount(source: BoardSource): number {
  return source.health.filter((item) => (item.consecutive_failures ?? 0) > 0 || item.circuit_open).length;
}

export function ledgerText(strategy: BoardStrategy): string {
  const entries = Object.entries(strategy.ledger_lines);
  if (!entries.length) return '—';
  return entries.map(([line, day]) => (day ? `${line} ${day.as_of_date}（${day.candidates}）` : `${line} 无`)).join('；');
}
