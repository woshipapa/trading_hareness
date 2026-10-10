import type { components } from '../api/generated';

export type DatasourceCatalog = components['schemas']['CatalogResponse'];
export type DatasourceCapability = components['schemas']['CapabilityResponse'];
export type DatasourceBinding = components['schemas']['BindingResponse'];
export type DatasourceRead = components['schemas']['DatasourceReadResponse'];

/** The quick "TDX" source filter: every TDX-backed source, the MAC, extended-market, local-file and derived ones too. */
export const TDX_SOURCE_FILTER = 'tdx';
const TDX_SOURCES = new Set(['tdx_public', 'tdx_mac', 'tdx_ext', 'tdx_local', 'derived_tdx_limits']);

export const CATEGORY_LABELS: Record<string, string> = {
  quote: '行情', bars: 'K线', ticks: '分笔', auction: '竞价', limits: '涨跌停', sector: '板块', flow: '资金流',
  lhb: '龙虎榜', attention: '关注度', news: '新闻', events: '事件', fundamentals: '基本面', fund: '基金',
  reference: '参考资料', derived: '自算', context: '上下文',
};

export const STATUS_LABELS: Record<string, string> = {
  live_verified: 'LIVE_VERIFIED', declared: 'DECLARED', dormant: 'DORMANT', unsupported: 'UNSUPPORTED', retired: 'RETIRED',
};

export function categoryLabel(category: string): string {
  return CATEGORY_LABELS[category] ?? category;
}

export function statusLabel(status: string): string {
  return STATUS_LABELS[status] ?? status.toUpperCase();
}

export function statusType(status: string): 'success' | 'warning' | 'info' | 'danger' {
  if (status === 'live_verified') return 'success';
  if (status === 'declared') return 'warning';
  if (status === 'unsupported' || status === 'retired') return 'danger';
  return 'info';
}

export function filterCapabilities(
  capabilities: DatasourceCapability[], source: string, status: string, text: string,
): DatasourceCapability[] {
  const needle = text.trim().toLowerCase();
  const fromSource = (key: string) => !source || (source === TDX_SOURCE_FILTER ? TDX_SOURCES.has(key) : key === source);
  return capabilities.flatMap((capability) => {
    const bindings = capability.bindings.filter((binding) => fromSource(binding.source) && (!status || binding.status === status));
    if (!bindings.length) return [];
    const matches = !needle
      || `${capability.key} ${capability.label} ${capability.description} ${capability.category}`.toLowerCase().includes(needle)
      || bindings.some((binding) => `${binding.source} ${binding.notes}`.toLowerCase().includes(needle));
    return matches ? [{ ...capability, bindings }] : [];
  });
}

export function isResearchReadable(binding: DatasourceBinding): boolean {
  return Boolean(binding.adapter && (/^app\/datasources\/sources\/tdx_[^/]+\.py:/u.test(binding.adapter) || binding.adapter.startsWith('app/datasources/derived/limit_pools.py:')));
}

export function queryFromForm(values: Record<string, string>): string {
  const query = new URLSearchParams();
  Object.entries(values).forEach(([key, value]) => { if (value.trim()) query.set(key, value.trim()); });
  return query.toString();
}
