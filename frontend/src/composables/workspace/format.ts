/**
 * Pure display helpers shared by the research tabs.
 *
 * They hold no state, so they live at module scope; `formatters` is the set
 * the dashboard context hands to the tabs under the same names as before.
 */

// Time and size
export const chinaMinute = (value?: string | null) => value ? new Intl.DateTimeFormat('zh-CN', { timeZone: 'Asia/Shanghai', hour: '2-digit', minute: '2-digit', hour12: false }).format(new Date(value)) : '-';
export const chinaDateTime = (value?: string | null) => value ? new Intl.DateTimeFormat('zh-CN', { timeZone: 'Asia/Shanghai', year: 'numeric', month: '2-digit', day: '2-digit', hour: '2-digit', minute: '2-digit', hour12: false }).format(new Date(value)) : '-';
export const dateText = (value?: string | null) => value ? new Date(value).toLocaleString() : '未运行';
export const ageText = (seconds?: number | null) => seconds === null || seconds === undefined ? '-' : seconds < 60 ? `${Math.round(seconds)} 秒` : seconds < 3600 ? `${(seconds / 60).toFixed(1)} 分钟` : `${(seconds / 3600).toFixed(1)} 小时`;
export const bytesText = (bytes?: number | null) => bytes === null || bytes === undefined ? '-' : bytes >= 1024 ** 3 ? `${(bytes / 1024 ** 3).toFixed(2)} GiB` : bytes >= 1024 ** 2 ? `${(bytes / 1024 ** 2).toFixed(1)} MiB` : `${bytes} B`;

// Values and numbers
export const displayValue = (value: unknown) => value === null || value === undefined || value === '' ? '-' : typeof value === 'object' ? JSON.stringify(value) : String(value);
export const outcomePercent = (value?: number | null) => value === undefined || value === null || !Number.isFinite(Number(value)) ? '-' : `${(Number(value) * 100).toFixed(2)}%`;
export const moneyWan = (value?: number | null) => value === undefined || value === null || !Number.isFinite(Number(value)) ? '-' : `${(Number(value) / 10_000).toFixed(0)}万`;
export const storageText = (value?: number) => value === undefined || value === null ? '-' : `${Number(value).toFixed(2)} GiB`;
export const rowText = (value?: number) => value === undefined || value === null ? '-' : Number(value).toLocaleString();
export const metricNumber = (metrics: Record<string, unknown>, name: string, digits = 3) => {
  const raw = Number(metrics[name]); return Number.isFinite(raw) ? raw.toFixed(digits) : '-';
};
export const nestedValue = (record: Record<string, unknown> | undefined, path: string) => path.split('.').reduce<unknown>((value, key) => value && typeof value === 'object' ? (value as Record<string, unknown>)[key] : undefined, record);
export const nestedNumber = (record: Record<string, unknown> | undefined, path: string, digits = 4) => {
  const raw = Number(nestedValue(record, path)); return Number.isFinite(raw) ? raw.toFixed(digits) : '-';
};

// Direction and status tags shared by several tabs
export const claimDirection = (value: number) => value > 0 ? '偏多' : value < 0 ? '偏空' : '中性';
export const recommendationDirection = (value?: number) => value && value > 0 ? '偏多' : value && value < 0 ? '偏空' : '中性';
export const recommendationType = (value?: number) => value && value > 0 ? 'success' : value && value < 0 ? 'danger' : 'info';
export const featureStatusType = (value?: string) => value === 'ready' ? 'success' : value === 'missing' ? 'danger' : 'warning';

/** The helpers as they appear in the dashboard context. */
export const formatters = {
  chinaMinute, chinaDateTime, dateText, ageText, bytesText,
  displayValue, outcomePercent, moneyWan, storageText, rowText, metricNumber, nestedValue, nestedNumber,
  claimDirection, recommendationDirection, recommendationType, featureStatusType,
};
