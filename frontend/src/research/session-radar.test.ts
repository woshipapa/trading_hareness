import { describe, expect, it } from 'vitest';
import { auctionRows, bandCounts, breadthText, failingChecks, fieldHealthText, pctText, radarLegend, radarOption, radarRows, stars, tone, yi, type RadarDay } from './session-radar';

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

describe('session radar', () => {
  it('reproduces the vendor legend: the bands add up to the pool', () => {
    const rows = radarRows(DAY, '2', 'all');
    expect(rows.map((row) => row.time)).toEqual(['09:31', '14:59']);
    const legend = radarLegend(rows);
    expect([legend.cumDown, legend.cumUp, legend.middle, legend.pool, legend.mainNet])
      .toEqual(['4411.6亿', '3549.9亿', '2341.8亿', '10303.3亿', '-160.1亿']);
  });

  it('takes the main net as of each point, and none for a segment', () => {
    expect(radarRows(DAY, '2', 'all').map((row) => row.mainNet)).toEqual([-5e9, -1.601e10]);
    const chinext = radarRows(DAY, '2', 'chinext');
    expect(chinext).toHaveLength(1);
    expect([chinext[0].cumUp, chinext[0].mainNet]).toEqual([5e9, null]);
  });

  it('counts the limit band\'s broken boards and the breadth', () => {
    const limitDay: RadarDay = { trade_date: '2026-10-08', main_net: [], points: [{
      observed_at: '2026-10-08T14:00:00+08:00', phase: 'continuous', pool: cell(1e10, 5000),
      breadth: { up: 1075, down: 4369, flat: 120 },
      bands: { limit: { cum_up: cell(5e9, 43), cum_down: cell(1e9, 13), middle: cell(4e9, 4944),
                        now_up: cell(4e9, 27), now_down: cell(8e8, 16), now_middle: cell(5.2e9, 4957) } } }] };
    const rows = radarRows(limitDay, 'limit', 'all');
    expect(bandCounts(rows, 'limit')).toBe('涨停 27（曾 43，炸板 16）· 跌停 16（曾 13）');
    expect(breadthText(rows)).toBe('上涨 1075 · 下跌 4369 · 平 120');
  });

  it('keeps the auction distribution apart from the counted bands', () => {
    expect(auctionRows(DAY)).toEqual([{ time: '09:18', phase: '竞价·可撤单', up2: 30, up5: 4, down2: 12, down5: 1, priced: 5300 }]);
  });

  it('charts turnover in 亿 and colours the main net by sign', () => {
    const option = radarOption(radarRows(DAY, '2', 'all'), '2%') as { series: { name: string; data: unknown[] }[] };
    expect(option.series[0].data).toEqual([300, 3549.9]);
    const bars = option.series.find((series) => series.name === '主力净额')!.data as { value: number; itemStyle: { color: string } }[];
    expect(bars.map((bar) => [bar.value, bar.itemStyle.color])).toEqual([[-50, '#188038'], [-160.1, '#188038']]);
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
