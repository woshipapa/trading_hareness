// Decision 0013: the sentiment temperature, its intraday version, the broad-ETF flow and the
// golden / silver finger, as the API returns them, and the chart options that draw them.

export type Band = '冰点' | '冷' | '中性' | '热' | '沸点';
export type TimingState = 'golden' | 'silver';

export interface TemperatureReading {
  trade_date: string;
  temperature: number | null;
  band: Band | null;
  scores: Record<string, number | null>;
  values: Record<string, number | null>;
  index_close?: number | null;
  index_change_pct?: number | null;
  etf_flow?: { ratio: number | null; basket_turnover_cny: number | null; codes: number | null; amount_estimated: boolean | null } | null;
  marker?: { kind: string; label: string; counter_trend: boolean } | null;
  timing?: { state: TimingState | null; event: TimingState | null } | null;
}

export interface TemperatureComponent { key: string; label: string; direction: number }

export interface TemperatureDaily {
  version: string;
  readings: TemperatureReading[];
  thresholds: { freezing: number; boiling: number; cold_ceiling?: number; etf_surge_ratio?: number };
  components: TemperatureComponent[];
  marker_evidence?: {
    window: string;
    cold_with_surge: { days: number; up_1d: number; up_3d: number; up_5d: number };
    cold_all: { days: number; up_1d: number; up_3d: number; up_5d: number };
    caveat: string;
  };
  timing_evidence?: { window: string; rule: string; max_drawdown: { buy_and_hold: number; golden_only: number }; caveat: string };
}

export interface IntradaySample {
  time: string;
  observed_at?: string;
  temperature: number | null;
  band: Band | null;
  scores: Record<string, number | null>;
  values: Record<string, number | null>;
  counts?: Record<string, number>;
  turnover_cny?: number;
}

export interface TemperatureIntraday {
  trade_date: string;
  samples: IntradaySample[];
  source?: 'stored' | 'live';
  previous_session?: string | null;
  limits?: number;
}

const HOT = '#d93026';
const COLD = '#1a73e8';
const INDEX = '#8a8f98';
const GOLD = '#c99a06';
const BULL_AREA = 'rgba(217, 48, 38, 0.07)';
const BEAR_AREA = 'rgba(24, 128, 56, 0.07)';
const FREEZING = 20;
const BOILING = 80;

export const BAND_TAGS: Record<Band, 'primary' | 'info' | 'success' | 'warning' | 'danger'> = {
  冰点: 'primary', 冷: 'info', 中性: 'success', 热: 'warning', 沸点: 'danger',
};

export const TIMING_LABELS: Record<TimingState, string> = { golden: '金指（多头）', silver: '银指（空头）' };

/** The five-minute axis: the morning, a 13:00 slot that is always empty so the line breaks at lunch, the afternoon. */
export function intradayAxis(): string[] {
  const times: string[] = [];
  const add = (from: number, to: number) => {
    for (let minutes = from; minutes <= to; minutes += 5) {
      times.push(`${String(Math.floor(minutes / 60)).padStart(2, '0')}:${String(minutes % 60).padStart(2, '0')}`);
    }
  };
  add(9 * 60 + 30, 11 * 60 + 30);
  times.push('13:00');
  add(13 * 60 + 5, 15 * 60);
  return times;
}

export function formatComponent(key: string, value: number | null | undefined): string {
  if (value === null || value === undefined || !Number.isFinite(value)) return '-';
  if (key === 'limit_up' || key === 'limit_down') return `${Math.round(value)} 家`;
  if (key === 'max_streak') return `${Math.round(value)} 板`;
  if (key === 'premium') return `${value > 0 ? '+' : ''}${value.toFixed(2)}%`;
  if (key === 'turnover_ratio') return `${value.toFixed(2)}×`;
  return `${(value * 100).toFixed(1)}%`;
}

export interface ComponentRow { key: string; label: string; value: string; score: number | null; colder: boolean }

export function componentRows(reading: { scores: Record<string, number | null>; values: Record<string, number | null> } | null,
                              components: TemperatureComponent[]): ComponentRow[] {
  if (!reading) return [];
  return components.map((component) => ({
    key: component.key, label: component.label, value: formatComponent(component.key, reading.values[component.key]),
    score: reading.scores[component.key] ?? null, colder: component.direction < 0,
  }));
}

export function latestReading(day: TemperatureDaily | null): TemperatureReading | null {
  const scored = (day?.readings ?? []).filter((reading) => reading.temperature !== null);
  return scored.length ? scored[scored.length - 1] : null;
}

/** Runs of one golden / silver state as ECharts markArea pairs. */
export function timingAreas(readings: TemperatureReading[]): Array<[Record<string, unknown>, Record<string, unknown>]> {
  const areas: Array<[Record<string, unknown>, Record<string, unknown>]> = [];
  let start: TemperatureReading | null = null;
  let previous: TemperatureReading | null = null;
  const close = () => {
    if (start && previous && start.timing?.state) {
      const golden = start.timing.state === 'golden';
      areas.push([{ xAxis: start.trade_date, itemStyle: { color: golden ? BULL_AREA : BEAR_AREA } }, { xAxis: previous.trade_date }]);
    }
  };
  for (const reading of readings) {
    if (!start || reading.timing?.state !== start.timing?.state) {
      close();
      start = reading;
    }
    previous = reading;
  }
  close();
  return areas;
}

function thresholdLines(freezing: number, boiling: number) {
  return {
    silent: true, symbol: 'none',
    data: [
      { yAxis: boiling, lineStyle: { color: HOT, type: 'dashed' }, label: { formatter: `沸点 ${boiling}`, color: HOT } },
      { yAxis: freezing, lineStyle: { color: COLD, type: 'dashed' }, label: { formatter: `冰点 ${freezing}`, color: COLD } },
    ],
  };
}

export function dailyTooltip(reading: TemperatureReading): string {
  const lines = [`<b>${reading.trade_date}</b>`, `情绪温度 ${reading.temperature ?? '-'}（${reading.band ?? '-'}）`];
  if (reading.index_close) {
    const change = reading.index_change_pct;
    lines.push(`上证 ${reading.index_close.toFixed(2)}${change === null || change === undefined ? '' : `（${change > 0 ? '+' : ''}${change.toFixed(2)}%）`}`);
  }
  if (reading.etf_flow?.ratio) lines.push(`宽基 ETF 放量比 ${reading.etf_flow.ratio.toFixed(2)}`);
  if (reading.timing?.state) lines.push(`${TIMING_LABELS[reading.timing.state]}${reading.timing.event ? ' · 当日信号' : ''}`);
  if (reading.marker) lines.push(`<span style="color:${GOLD}">★ ${reading.marker.label}</span>`);
  return lines.join('<br/>');
}

export function dailyOption(day: TemperatureDaily): Record<string, unknown> {
  const readings = day.readings;
  const dates = readings.map((reading) => reading.trade_date);
  const byDate = new Map(readings.map((reading) => [reading.trade_date, reading]));
  const events = readings.filter((reading) => reading.timing?.event);
  return {
    animation: false,
    tooltip: {
      trigger: 'axis',
      formatter: (params: Array<{ axisValue: string }>) => {
        const reading = byDate.get(params[0]?.axisValue ?? '');
        return reading ? dailyTooltip(reading) : '';
      },
    },
    legend: { top: 0 },
    grid: { left: 48, right: 64, top: 36, bottom: 56 },
    xAxis: { type: 'category', data: dates },
    yAxis: [
      { type: 'value', name: '温度', min: 0, max: 100, interval: 20 },
      { type: 'value', name: '上证', scale: true, splitLine: { show: false } },
    ],
    dataZoom: [{ type: 'inside' }, { type: 'slider', height: 18, bottom: 8 }],
    series: [
      {
        name: '情绪温度', type: 'line', showSymbol: false, data: readings.map((reading) => reading.temperature),
        lineStyle: { color: HOT, width: 2 }, itemStyle: { color: HOT },
        markLine: thresholdLines(day.thresholds.freezing ?? FREEZING, day.thresholds.boiling ?? BOILING),
        markArea: { silent: true, data: timingAreas(readings) },
      },
      {
        name: '上证指数', type: 'line', yAxisIndex: 1, showSymbol: false,
        data: readings.map((reading) => reading.index_close ?? null), lineStyle: { color: INDEX, width: 1 }, itemStyle: { color: INDEX },
      },
      {
        name: '冰点资金共振', type: 'scatter', symbol: 'pin', symbolSize: 26, itemStyle: { color: GOLD },
        data: readings.filter((reading) => reading.marker).map((reading) => ({
          value: [reading.trade_date, reading.temperature],
          label: { show: true, formatter: reading.marker?.counter_trend ? '逆' : '共', color: '#fff', fontSize: 10 },
        })),
      },
      {
        name: '金 / 银指', type: 'scatter', yAxisIndex: 1, symbol: 'triangle', symbolSize: 11,
        data: events.map((reading) => {
          const golden = reading.timing?.event === 'golden';
          return {
            value: [reading.trade_date, reading.index_close ?? null], symbolRotate: golden ? 0 : 180,
            itemStyle: { color: golden ? HOT : '#188038' },
            label: { show: true, position: golden ? 'bottom' : 'top', formatter: golden ? '金' : '银', color: golden ? HOT : '#188038' },
          };
        }),
      },
    ],
  };
}

export function intradayOption(day: TemperatureIntraday, thresholds?: { freezing: number; boiling: number }): Record<string, unknown> {
  const axis = intradayAxis();
  const bySlot = new Map(day.samples.map((sample) => [sample.time, sample]));
  return {
    animation: false,
    tooltip: {
      trigger: 'axis',
      formatter: (params: Array<{ axisValue: string }>) => {
        const sample = bySlot.get(params[0]?.axisValue ?? '');
        if (!sample) return '';
        const counts = sample.counts ?? {};
        return [`<b>${day.trade_date} ${sample.time}</b>`, `情绪温度 ${sample.temperature ?? '-'}（${sample.band ?? '-'}）`,
          `涨停 ${counts.limit_up ?? '-'} · 跌停 ${counts.limit_down ?? '-'} · 触板 ${counts.touched ?? '-'} · 最高 ${counts.max_streak ?? '-'} 板`,
          `上涨 ${counts.advancers ?? '-'} / 下跌 ${counts.decliners ?? '-'}`].join('<br/>');
      },
    },
    grid: { left: 48, right: 24, top: 24, bottom: 32 },
    xAxis: { type: 'category', data: axis, axisLabel: { interval: 5 } },
    yAxis: { type: 'value', name: '温度', min: 0, max: 100, interval: 20 },
    series: [{
      name: '分时情绪温度', type: 'line', connectNulls: false, showSymbol: false,
      data: axis.map((slot) => bySlot.get(slot)?.temperature ?? null),
      lineStyle: { color: HOT, width: 2 }, itemStyle: { color: HOT },
      markLine: thresholdLines(thresholds?.freezing ?? FREEZING, thresholds?.boiling ?? BOILING),
    }],
  };
}

export function percentText(share: number | null | undefined): string {
  return share === null || share === undefined || !Number.isFinite(share) ? '-' : `${Math.round(share * 100)}%`;
}
