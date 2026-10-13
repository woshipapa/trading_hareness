import { describe, expect, it } from 'vitest';
import {
  auctionRows, displayRows, failingChecks, fieldHealthText, pctText, radarHeadline, radarOption, radarRows,
  radarTooltip, stars, tone, yi, type RadarDay,
} from './session-radar';

const cell = (turnover: number, count = 1) => ({ turnover, count });
const bands = (up: number, down: number, pool: number, nowUp = up, nowDown = down) => ({
  cum_up: cell(up), cum_down: cell(down), middle: cell(pool - up - down),
  now_up: cell(nowUp), now_down: cell(nowDown), now_middle: cell(pool - nowUp - nowDown),
});

const DAY: RadarDay = {
  trade_date: '2026-10-08',
  points: [
    { observed_at: '2026-10-08T09:18:00+08:00', phase: 'auction_cancellable', auction: { up: { 2: 30, 5: 4 }, down: { 2: 12, 5: 1 }, priced: 5300 } },
    { observed_at: '2026-10-08T09:31:00+08:00', phase: 'continuous', pool: cell(1e11), bands: { 2: bands(3e10, 4e10, 1e11) },
      segments: { chinext: { pool: cell(2e10), bands: { 2: bands(5e9, 8e9, 2e10) } } } },
    { observed_at: '2026-10-08T14:59:00+08:00', phase: 'closing_auction', pool: cell(1.03033e12),
      bands: { 2: bands(3.549_9e11, 4.411_6e11, 1.03033e12, 2e11, 3e11) } },
  ],
  main_net: [
    { observed_at: '2026-10-08T09:30:30+08:00', main_net: -5e9, boards: 90 },
    { observed_at: '2026-10-08T14:58:00+08:00', main_net: -1.601e10, boards: 90 },
  ],
};

const CHART_DAY: RadarDay = {
  trade_date: '2026-10-08',
  points: [
    { observed_at: '2026-10-08T09:14:00+08:00', phase: 'auction_cancellable', auction: { up: { 2: 19 }, down: { 2: 9 }, priced: 5299 } },
    { observed_at: '2026-10-08T09:15:00+08:00', phase: 'auction_cancellable', auction: { up: { 2: 20 }, down: { 2: 10 }, priced: 5300 } },
    { observed_at: '2026-10-08T09:16:00+08:00', phase: 'auction_cancellable', auction: { up: { 2: 21 }, down: { 2: 10 }, priced: 5301 } },
    { observed_at: '2026-10-08T09:17:00+08:00', phase: 'auction_cancellable', auction: { up: { 2: 22 }, down: { 2: 11 }, priced: 5302 } },
    { observed_at: '2026-10-08T09:25:00+08:00', phase: 'auction_matched', pool: cell(1e11), bands: { 2: bands(1e10, 2e10, 1e11) } },
    { observed_at: '2026-10-08T09:26:00+08:00', phase: 'continuous', pool: cell(1.1e11), bands: { 2: bands(1.1e10, 2.2e10, 1.1e11) } },
    { observed_at: '2026-10-08T09:27:00+08:00', phase: 'continuous', pool: cell(1.2e11), bands: { 2: bands(1.2e10, 2.4e10, 1.2e11) } },
    { observed_at: '2026-10-08T09:28:00+08:00', phase: 'continuous', pool: cell(1.3e11), bands: { 2: bands(1.3e10, 2.6e10, 1.3e11) } },
    { observed_at: '2026-10-08T09:29:00+08:00', phase: 'continuous', pool: cell(1.4e11), bands: { 2: bands(1.4e10, 2.8e10, 1.4e11, 1.3e10, 2.7e10) } },
  ],
  main_net: [
    { observed_at: '2026-10-08T09:25:00+08:00', main_net: -5e9, boards: 90 },
    { observed_at: '2026-10-08T09:29:00+08:00', main_net: -5.46e9, boards: 90 },
  ],
};

describe('session radar', () => {
  it('keeps the counted bands and pool values in the row projection', () => {
    const rows = radarRows(DAY, '2', 'all');
    expect(rows.map((row) => row.time)).toEqual(['09:18', '09:31', '14:59']);
    expect([rows[2].cumDown, rows[2].cumUp, rows[2].middle, rows[2].pool, rows[2].mainNet])
      .toEqual([4.411_6e11, 3.549_9e11, 2.341_8e11, 1.03033e12, -1.601e10]);
  });

  it('takes the main net as of each point, and none for a segment', () => {
    expect(radarRows(DAY, '2', 'all').map((row) => row.mainNet)).toEqual([null, -5e9, -1.601e10]);
    const chinext = radarRows(DAY, '2', 'chinext').filter((row) => row.cumUp !== null);
    expect(chinext).toHaveLength(1);
    expect([chinext[0].cumUp, chinext[0].mainNet]).toEqual([5e9, null]);
  });

  it('buckets the chart to two minutes while keeping 09:25 and the newest point', () => {
    const displayed = displayRows(radarRows(CHART_DAY, '2', 'all'));
    expect(displayed.map((row) => row.time)).toEqual(['09:16', '09:17', '09:25', '09:26', '09:28', '09:29']);
    expect(displayed.slice(0, 2).map((row) => row.cumDown)).toEqual([null, null]);
  });

  it('keeps the auction distribution apart from the counted bands', () => {
    expect(auctionRows(DAY)).toEqual([{ time: '09:18', phase: '竞价·可撤单', up2: 30, up5: 4, down2: 12, down5: 1, priced: 5300 }]);
  });

  it('builds the headline from the last two displayed points and the 09:25 match', () => {
    expect(radarHeadline(radarRows(CHART_DAY, '2', 'all'))).toEqual({
      cumDown: 2.8e10, cumUp: 1.4e10, downDelta: 2e9, upDelta: 1e9,
      auctionDown: 2e10, auctionUp: 1e10,
    });
  });

  it('formats continuous and pre-match tooltips', () => {
    const rows = radarRows(CHART_DAY, '2', 'all');
    expect(radarTooltip(rows.find((row) => row.time === '09:29')!, '±2%')).toEqual([
      '09:29', '主动卖 280.0亿（即时 270.0亿）', '主动买 140.0亿（即时 130.0亿）',
      '中间带（±2% 内） 980.0亿', '全池总额 1400.0亿', '主力净额 -54.6亿',
      '卖/买 = 当日曾触及 ±2% 的股票累计成交额（只进不出）；即时 = 此刻仍在 ±2% 之外',
    ]);
    expect(radarTooltip(rows.find((row) => row.time === '09:17')!, '±2%')).toEqual([
      '09:17', '竞价虚拟价：高于 +2% 22 只 · 低于 -2% 11 只（已有价格 5302 只）',
    ]);
  });

  it('draws four named lines with main net dashed on the right axis', () => {
    const option = radarOption(radarRows(CHART_DAY, '2', 'all'), '±2%') as {
      series: { name: string; type: string; yAxisIndex?: number; lineStyle: { type: string }; data: unknown[]; markLine?: { data: { xAxis: string }[] } }[];
    };
    expect(option.series.map((series) => series.name)).toEqual(['主动卖（累计）', '主动买（累计）', '中间带', '主力净额']);
    expect(option.series[0].data.slice(0, 2)).toEqual([null, null]);
    expect(option.series[0].markLine?.data.map((marker) => marker.xAxis)).toEqual(['09:25', '09:30']);
    expect(option.series[3]).toMatchObject({ type: 'line', yAxisIndex: 1, lineStyle: { type: 'dashed' } });
  });

  it('names the checks that withhold an indicator', () => {
    expect(failingChecks({ key: 'market.main_net', label: '', availability: '', status: 'fail', decision_eligible: false, checks: [
      { name: 'present', status: 'ok', value: 104 },
      { name: 'plausible', status: 'fail', value: 2e7, threshold: '<=0.25', detail: '|主力净额|/全池成交额' }] }))
      .toBe('plausible=20000000（|主力净额|/全池成交额）');
  });

  it('lists each card field with its indicator health', () => {
    expect(fieldHealthText({ trade_date: '', status: 'completed', cards: [], data_health: { all_eligible: false, fields: {
      themes: { indicator: 'board.concept_strength', status: 'ok', decision_eligible: true },
      direction_gate: { indicator: 'market.direction_gate', status: 'pending', decision_eligible: false } } } }))
      .toBe('themes：board.concept_strength 可用；direction_gate：market.direction_gate 未到时');
  });

  it('formats signed percentages, tones and event stars', () => {
    expect([pctText(11.4498), pctText(-2.88), pctText(null)]).toEqual(['+11.45%', '-2.88%', '-']);
    expect([tone(1), tone(-1), tone(0)]).toEqual(['up', 'down', 'flat']);
    expect([stars(0), stars(2), yi(undefined)]).toEqual(['—', '★★', '-']);
  });
});
