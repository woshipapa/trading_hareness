import { describe, expect, it } from 'vitest';
import {
  boardRows, labelName, pairRows, percent, recordRows, summarySections,
  type BoardRow, type TacticBoard, type TacticCard, type TacticRecord, type TacticSummaryReading,
} from './tactic-trials';

const card = (key: string, name: string, combination = true): TacticCard => ({
  key, name, slot: 'close', side: 'focus', source: 'plan:v1', entry: 'buy_open', exit: 'gap_rule', primary: 'seal',
  primary_name: '再封板率', expects: 1, registered_on: '2026-10-13', combination,
});
const record = (picks: number, in_sample = false): TacticRecord => ({
  days_with_picks: picks, picks, mean_daily_picks_mean: 0.01, mean_daily_difference: 0.02, t: 1.5, positive_share: 0.6, in_sample,
});
const row = (symbol: string, tactics: string[]): BoardRow => ({
  symbol, name: symbol, sector: null, boards: 2, gap: 0.03, opens_at_limit: false, amount: null, auction_amount_rank: null,
  risk_rank: 40, first_seal_time: null, seal_money: null, tactics, conditions: [],
});

describe('tactic trial helpers', () => {
  it('formats returns with a sign, booleans as words and a missing value as a dash', () => {
    expect([percent(0.0312), percent(-0.05, 1), percent(true), percent(null)]).toEqual(['+3.12%', '-5.0%', '是', '—']);
  });
  it('names labels', () => expect([labelName('supports'), labelName(null)]).toEqual(['支持', '尚无战绩']));
  it('puts the name and label of each picking tactic on a board row', () => {
    const board = {
      tactics: [{ ...card('relay_2_3', '二三板接力'), label: 'collecting', q: null }],
    } as unknown as TacticBoard;
    const sides = { focus: [row('000001.SZ', ['relay_2_3', 'gone'])], hold: [], avoid: [] };
    expect(boardRows(sides, 'focus', board)[0].picked_by).toEqual([
      { key: 'relay_2_3', name: '二三板接力', label: 'collecting' }, { key: 'gone', name: 'gone', label: null },
    ]);
    expect(boardRows(null, 'avoid', board)).toEqual([]);
  });
  it('lists the registered combinations before the single conditions', () => {
    const summary = { records: {
      'only.theme_big': { name: '大题材', slot: 'close', primary: 'seal', primary_name: '再封板率', expects: 1, label: 'collecting', q: null,
        records: { replay: record(40, true), forward: record(2), recent: record(2) } },
      relay_2_3: { name: '二三板接力', slot: 'close', primary: 'seal', primary_name: '再封板率', expects: 1, label: 'supports', q: 0.04,
        records: { replay: record(300, true), forward: record(25), recent: record(25) } },
    } } as unknown as TacticSummaryReading;
    const rows = recordRows(summary, [card('relay_2_3', '二三板接力')]);
    expect(rows.map((item) => [item.key, item.combination, item.side])).toEqual([['relay_2_3', true, 'focus'], ['only.theme_big', false, null]]);
    expect(rows[0].replay.in_sample).toBe(true);
    expect(recordRows(null, [])).toEqual([]);
  });
  it('cuts the summary lines at the numbered headings', () => {
    expect(summarySections(['一、当日', '收盘：2行', '二、战法', '未选中：无数据'])).toEqual([
      { title: '一、当日', lines: ['收盘：2行'] }, { title: '二、战法', lines: ['未选中：无数据'] },
    ]);
  });
  it('lists the strongest pairs of both directions', () => {
    const pair = { conditions: ['a', 'b'], names: ['甲', '乙'], days_with_picks: 30, picks: 60, mean_daily_difference: 0.01, t: 2.2, positive_share: 0.6 };
    const rows = pairRows({ mined: true, pairs_examined: 10, pairs_reaching_minimum: 2, highest: [pair], lowest: [{ ...pair, t: -2.4 }] });
    expect(rows.map((item) => [item.direction, item.t])).toEqual([['最高', 2.2], ['最低', -2.4]]);
    expect(pairRows(undefined)).toEqual([]);
  });
});
