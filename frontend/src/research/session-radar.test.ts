import { describe, expect, it } from 'vitest';
import {
  auctionRows, bandCounts, breadthText, displayRows, failingChecks, fieldHealthText, pctText, radarHeadline, radarOption, radarRows,
  radarTooltip, stars, timingPhaseGroups, tone, yi, type RadarDay, type StrategyTimingRow,
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

const MARKER_DAY: RadarDay = {
  trade_date: '2026-10-08',
  points: [
    { observed_at: '2026-10-08T09:15:00+08:00', phase: 'auction_cancellable', auction: { up: { 2: 20 }, down: { 2: 10 }, priced: 5300 } },
    { observed_at: '2026-10-08T09:26:00+08:00', phase: 'auction_matched', pool: cell(1e11), bands: { 2: bands(1e10, 2e10, 1e11) } },
    { observed_at: '2026-10-08T09:27:00+08:00', phase: 'continuous', pool: cell(1.1e11), bands: { 2: bands(1.1e10, 2.2e10, 1.1e11) } },
    { observed_at: '2026-10-08T09:31:00+08:00', phase: 'continuous', pool: cell(1.2e11), bands: { 2: bands(1.2e10, 2.4e10, 1.2e11) } },
  ],
  main_net: [],
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
      auctionDown: 2e10, auctionUp: 1e10, mainNet: -5.46e9,
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
    expect(option.series[0].markLine?.data.map((marker) => marker.xAxis)).toEqual(['09:25']);
    expect(option.series[3]).toMatchObject({ type: 'line', yAxisIndex: 1, lineStyle: { type: 'dashed' } });
  });

  it('anchors markers to displayed match and post-09:30 categories', () => {
    const option = radarOption(radarRows(MARKER_DAY, '2', 'all'), '±2%') as { series: { markLine?: { data: { xAxis: string; label: { formatter: string } }[] } }[] };
    expect(option.series[0].markLine?.data).toEqual([
      { xAxis: '09:26', label: { formatter: '9:25 定格', color: '#188038' }, lineStyle: { color: '#188038', type: 'dashed' } },
      { xAxis: '09:31', label: { formatter: '9:30', color: '#666' }, lineStyle: { color: '#999', type: 'dashed' } },
    ]);
    const stopped: RadarDay = { ...MARKER_DAY, points: MARKER_DAY.points.filter((point) => point.observed_at < '2026-10-08T09:23:00+08:00') };
    const stoppedOption = radarOption(radarRows(stopped, '2', 'all'), '±2%') as { series: { markLine?: { data: unknown[] } }[] };
    expect(stoppedOption.series[0].markLine?.data).toEqual([]);
  });

  it('retains the counts and breadth summaries', () => {
    const limitDay: RadarDay = { trade_date: '2026-10-08', main_net: [], points: [{
      observed_at: '2026-10-08T14:00:00+08:00', phase: 'continuous', pool: cell(1e10, 5000),
      breadth: { up: 1075, down: 4369, flat: 120 },
      bands: { limit: { cum_up: cell(5e9, 43), cum_down: cell(1e9, 13), middle: cell(4e9, 4944),
                        now_up: cell(4e9, 27), now_down: cell(8e8, 16), now_middle: cell(5.2e9, 4957) } } }] };
    const rows = radarRows(limitDay, 'limit', 'all');
    expect(bandCounts(rows, 'limit')).toBe('涨停 27（曾 43，炸板 16）· 跌停 16（曾 13）');
    expect(breadthText(rows)).toBe('上涨 1075 · 下跌 4369 · 平 120');
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

  it('projects the timing board into extensible phase groups', () => {
    const row = (phase: string, symbol?: string): StrategyTimingRow => ({
      strategy_key: 'dragon_leader_research', name: '龙头研究', maturity: 'shadow', status: symbol ? 'candidates_available' : 'waiting_for_candidates',
      active_phase: phase, timing_plans: [
        { phase: 'auction', window: '09:15-09:25', entry_mode: 'auction_candidate', buy_point: '竞价快照', why: '连板承接', evidence: ['auction'] },
        { phase: 'intraday', window: '09:30-14:20', entry_mode: 'intraday_confirmation', buy_point: '回封', why: '量价确认', evidence: ['entry_signals'] },
      ],
      candidates: symbol ? [{ strategy_key: 'dragon_leader_research', symbol, status: 'research_candidate', research_action: 'buy_candidate', phase, window: '09:15-09:25', entry_mode: 'auction_candidate', buy_point: '竞价快照', selection_reasons: ['连板承接'], missing_evidence: [], research_only: true, live_effect: 'none' }] : [],
      research_only: true, live_effect: 'none',
    });
    const groups = timingPhaseGroups({ active_phase: 'auction', strategies: [row('auction', '000001.SZ'), row('auction')] });
    expect(groups.map((group) => [group.key, group.active, group.rows.length, group.candidates.length])).toEqual([
      ['auction', true, 2, 1], ['intraday', false, 2, 0], ['near_close', false, 0, 0], ['post_close', false, 0, 0],
    ]);
    expect(groups[0].candidates[0].strategy_name).toBe('龙头研究');
  });
});
