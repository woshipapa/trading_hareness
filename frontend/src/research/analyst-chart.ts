export type AnalystChartBar = {
  bar_time: string;
  close: number;
};

export type AnalystChartAnnotation = {
  id: string;
  kind: 'point' | 'line' | 'area';
  label: string;
  start_index: number;
  end_index?: number;
  price?: number;
  lower?: number;
  upper?: number;
};

const SYMBOL_PATTERN = /^(\d{6})\.(SH|SZ|BJ)$/;

/**
 * Build the desktop deep link understood by the installed Tonghuashun client.
 * The scheme is an app boundary, so callers must still provide a UI fallback.
 */
export function buildTonghuashunDeepLink(symbol: string): string | null {
  const match = SYMBOL_PATTERN.exec(String(symbol || '').trim().toUpperCase());
  if (!match) return null;
  const params = new URLSearchParams({ stockcode: match[1], market: match[2] });
  return `hexinstock://?${params.toString()}`;
}

/** Stable browser fallback for machines where the desktop protocol is blocked. */
export function buildTonghuashunWebLink(symbol: string): string | null {
  const match = SYMBOL_PATTERN.exec(String(symbol || '').trim().toUpperCase());
  return match ? `https://stockpage.10jqka.com.cn/${match[1]}/` : null;
}

export function buildManualAnnotationSeries(
  annotations: AnalystChartAnnotation[],
  barCount: number,
) {
  const validIndex = (value: number | undefined) => value !== undefined && Number.isInteger(value) && value >= 0 && value < barCount;
  const points = annotations
    .filter((item) => item.kind === 'point' && validIndex(item.start_index) && Number.isFinite(item.price))
    .map((item) => ({
      name: item.label,
      coord: [item.start_index, item.price as number],
      value: item.label,
      itemStyle: { color: '#7b1fa2' },
    }));
  const lines = annotations
    .filter((item) => item.kind === 'line' && Number.isFinite(item.price))
    .map((item) => ({ name: item.label, yAxis: item.price as number }));
  const areas = annotations
    .filter((item) => item.kind === 'area'
      && validIndex(item.start_index)
      && validIndex(item.end_index)
      && Number.isFinite(item.lower)
      && Number.isFinite(item.upper)
      && (item.lower as number) <= (item.upper as number))
    .map((item) => ([
      { name: item.label, coord: [item.start_index, item.lower] },
      { coord: [item.end_index, item.upper] },
    ]));
  return { points, lines, areas };
}

export function annotationStorageKey(symbol: string, startDate: string, endDate: string) {
  return `quant-analyst-chart-annotations:${symbol}:${startDate}:${endDate}`;
}
