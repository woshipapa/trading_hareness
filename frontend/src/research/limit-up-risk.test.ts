import { describe, expect, it } from 'vitest';
import { sortedRows, trackOption, type LimitUpRiskReading, type LimitUpRiskTrack } from './limit-up-risk';

const reading: LimitUpRiskReading = {
  trade_date: '2026-09-18', version: 'limit-up-risk-v1', list_source: 'history', coverage: {}, ranks_reliable: true,
  research_only: true, live_effect: 'none', rows: [
    { symbol: '000001.SZ', name: '低', boards: 1, first_seal_time: '09:40:00', sealed_from_open: false, seal_ratio: 0.1, flow: 0.2, vr: 2, float_cap: 1e9, risk_rank: 12 },
    { symbol: '000002.SZ', name: '高', boards: 2, first_seal_time: '09:30:00', sealed_from_open: true, seal_ratio: 0.2, flow: 0.3, vr: 3, float_cap: 2e9, risk_rank: 88 },
  ],
};

describe('limit-up risk helpers', () => {
  it('sorts rows from highest to lowest risk rank', () => expect(sortedRows(reading).map((row) => row.name)).toEqual(['高', '低']));
  it('builds two smoothed track lines', () => {
    const track: LimitUpRiskTrack = { version: 'limit-up-risk-v1', days: [
      { trade_date: '2026-09-17', lowest: { big_loss_rate: 0.1, mean_next_day_return: 0 }, highest: { big_loss_rate: 0.2, mean_next_day_return: 0 } },
    ], recent: { sessions: 1, lowest_big_loss_rate: 0.1, highest_big_loss_rate: 0.2, difference_points: 10, t_value: null, status: 'insufficient_history' }, stale_versions: 0, research_only: true, live_effect: 'none',
    };
    const option = trackOption(track) as { series: Array<{ data: number[] }> };
    expect(option.series.map((series) => series.data)).toEqual([[10], [20]]);
  });
});
