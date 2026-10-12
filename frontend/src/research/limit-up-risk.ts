export interface LimitUpRiskRow {
  symbol: string;
  name: string;
  boards: number;
  first_seal_time: string;
  sealed_from_open: boolean;
  seal_ratio: number;
  flow: number;
  vr: number;
  float_cap: number;
  risk_rank: number;
}

export interface LimitUpRiskReading {
  trade_date: string;
  version: string;
  list_source: 'history' | 'live';
  coverage: Record<string, number>;
  ranks_reliable: boolean;
  rows: LimitUpRiskRow[];
  research_only: boolean;
  live_effect: 'none';
}

export interface LimitUpRiskDaily {
  version: string;
  reading: LimitUpRiskReading | null;
  stale_versions: number;
  research_only: boolean;
  live_effect: 'none';
}

export interface LimitUpRiskDay {
  trade_date: string;
  lowest: { big_loss_rate: number; mean_next_day_return: number };
  highest: { big_loss_rate: number; mean_next_day_return: number };
}

export interface LimitUpRiskTrack {
  version: string;
  days: LimitUpRiskDay[];
  recent: {
    sessions: number;
    lowest_big_loss_rate: number | null;
    highest_big_loss_rate: number | null;
    difference_points: number | null;
    t_value: number | null;
    status: 'working' | 'not_working_recently' | 'insufficient_history';
  };
  stale_versions: number;
  research_only: boolean;
  live_effect: 'none';
}

export function sortedRows(reading: LimitUpRiskReading | null): LimitUpRiskRow[] {
  return [...(reading?.rows ?? [])].sort((a, b) => b.risk_rank - a.risk_rank);
}

function movingAverage(values: Array<number | null>, window: number): Array<number | null> {
  return values.map((_value, index) => {
    const sample = values.slice(Math.max(0, index - window + 1), index + 1).filter((value): value is number => value !== null);
    return sample.length ? sample.reduce((sum, value) => sum + value, 0) / sample.length : null;
  });
}

export function trackOption(track: LimitUpRiskTrack): Record<string, unknown> {
  const dates = track.days.map((day) => day.trade_date);
  const lowest = movingAverage(track.days.map((day) => day.lowest.big_loss_rate * 100), 20);
  const highest = movingAverage(track.days.map((day) => day.highest.big_loss_rate * 100), 20);
  return {
    animation: false,
    tooltip: { trigger: 'axis', valueFormatter: (value: number) => `${value.toFixed(1)}%` },
    legend: { top: 0 },
    grid: { left: 48, right: 24, top: 36, bottom: 56 },
    xAxis: { type: 'category', data: dates },
    yAxis: { type: 'value', name: '次日大面率', axisLabel: { formatter: '{value}%' } },
    dataZoom: [{ type: 'inside' }, { type: 'slider', height: 18, bottom: 8 }],
    series: [
      { name: '最低风险五分之一（20日均）', type: 'line', showSymbol: false, data: lowest, lineStyle: { color: '#188038', width: 2 } },
      { name: '最高风险五分之一（20日均）', type: 'line', showSymbol: false, data: highest, lineStyle: { color: '#d93026', width: 2 } },
    ],
  };
}

export function percent(value: number | null | undefined, digits = 1): string {
  return value === null || value === undefined || !Number.isFinite(value) ? '-' : `${(value * 100).toFixed(digits)}%`;
}

export function yuanYi(value: number | null | undefined): string {
  return value === null || value === undefined || !Number.isFinite(value) ? '-' : (value / 100000000).toFixed(2);
}
