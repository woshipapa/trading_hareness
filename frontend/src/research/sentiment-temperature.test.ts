import { describe, expect, it } from 'vitest';
import {
  componentRows, dailyOption, formatComponent, intradayAxis, intradayOption, latestReading, timingAreas,
  type TemperatureDaily, type TemperatureReading,
} from './sentiment-temperature';

const reading = (day: string, temperature: number | null, extra: Partial<TemperatureReading> = {}): TemperatureReading => ({
  trade_date: day, temperature, band: null, scores: {}, values: {}, index_close: 3800, ...extra,
});

describe('intraday axis', () => {
  it('runs every five minutes with an empty 13:00 slot for the lunch break', () => {
    const axis = intradayAxis();
    expect([axis.length, axis[0], axis[24], axis[25], axis[26], axis.at(-1)]).toEqual([50, '09:30', '11:30', '13:00', '13:05', '15:00']);
  });

  it('leaves the lunch slot and missing minutes empty so the line breaks', () => {
    const option = intradayOption({ trade_date: '2026-10-09', samples: [
      { time: '11:30', temperature: 41, band: '中性', scores: {}, values: {} },
      { time: '13:05', temperature: 44, band: '中性', scores: {}, values: {} },
    ] }) as { series: Array<{ data: Array<number | null>; connectNulls: boolean }> };
    const data = option.series[0].data;
    expect([data[24], data[25], data[26], data[0], option.series[0].connectNulls]).toEqual([41, null, 44, null, false]);
  });
});

describe('daily chart', () => {
  const day: TemperatureDaily = {
    version: 'v1', thresholds: { freezing: 20, boiling: 80 }, components: [],
    readings: [
      reading('2025-04-03', 30, { timing: { state: 'golden', event: null } }),
      reading('2025-04-07', 11, { marker: { kind: 'cold_with_etf_surge', label: '逆势放量', counter_trend: true },
                                  timing: { state: 'silver', event: 'silver' } }),
      reading('2025-04-08', 25, { timing: { state: 'silver', event: null } }),
    ],
  };

  it('marks only the marker days and the timing events', () => {
    const option = dailyOption(day) as { series: Array<{ name: string; data: Array<{ value: unknown[] }> }> };
    const series = Object.fromEntries(option.series.map((item) => [item.name, item.data]));
    expect(series['冰点资金共振'].map((point) => point.value)).toEqual([['2025-04-07', 11]]);
    expect(series['金 / 银指'].map((point) => point.value)).toEqual([['2025-04-07', 3800]]);
  });

  it('shades each run of one timing state', () => {
    const areas = timingAreas(day.readings);
    expect(areas.map(([start, end]) => [start.xAxis, end.xAxis])).toEqual([['2025-04-03', '2025-04-03'], ['2025-04-07', '2025-04-08']]);
  });

  it('takes the newest reading with a temperature', () => {
    expect(latestReading({ ...day, readings: [...day.readings, reading('2025-04-09', null)] })?.trade_date).toBe('2025-04-08');
  });
});

describe('components', () => {
  it('formats each component in its own unit', () => {
    expect([formatComponent('limit_up', 63), formatComponent('max_streak', 9), formatComponent('seal_rate', 0.712),
      formatComponent('premium', -1.234), formatComponent('turnover_ratio', 1.5438), formatComponent('up_ratio', null)])
      .toEqual(['63 家', '9 板', '71.2%', '-1.23%', '1.54×', '-']);
  });

  it('pairs each component with its score and direction', () => {
    const rows = componentRows({ scores: { limit_down: 12.5 }, values: { limit_down: 30 } },
      [{ key: 'limit_down', label: '跌停数', direction: -1 }]);
    expect(rows).toEqual([{ key: 'limit_down', label: '跌停数', value: '30 家', score: 12.5, colder: true }]);
  });
});
