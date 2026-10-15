import { describe, expect, it } from 'vitest';
import { freshnessOf, normalizeSymbol, scanOverlapRows, scanStageRows, symbolFromRow } from './remote-runtime';

describe('remote runtime evidence helpers', () => {
it('normalizes only exchange-qualified A-share symbols', () => {
  expect(normalizeSymbol(' 600000.sh ')).toBe('600000.SH');
  expect(normalizeSymbol('000001.SZ')).toBe('000001.SZ');
  expect(normalizeSymbol('600000')).toBeNull();
  expect(normalizeSymbol('600000.HK')).toBeNull();
  expect(symbolFromRow({ ts_code: '300001.sz' })).toBe('300001.SZ');
});

it('classifies persisted evidence age without treating future timestamps as stale', () => {
  const now = Date.parse('2026-09-23T05:00:00.000Z');
  expect(freshnessOf('2026-09-23T04:59:30.000Z', now)).toBe('fresh');
  expect(freshnessOf('2026-09-23T04:57:00.000Z', now)).toBe('aging');
  expect(freshnessOf('2026-09-23T04:50:00.000Z', now)).toBe('stale');
  expect(freshnessOf(null, now)).toBe('missing');
  expect(freshnessOf('not-a-date', now)).toBe('invalid');
});

it('normalizes scan timing for a bounded visual timeline', () => {
  expect(scanStageRows({ capture_and_memberships: 40, signal_persist: '20', malformed: 'x' }, 50)).toEqual([
    { stage: 'capture_and_memberships', milliseconds: 40, share: 80 },
    { stage: 'signal_persist', milliseconds: 20, share: 40 },
  ]);
  expect(scanStageRows({ signal_persist: 12 }, 0)[0].share).toBe(0);
  expect(scanStageRows(null, 50)).toEqual([]);
});

it('keeps overlap telemetry displayable when newer keys arrive', () => {
  expect(scanOverlapRows({ capture_and_board_cache: 'concurrent_after_watchlist', unknown: 1 })).toEqual([
    { key: 'capture_and_board_cache', value: 'concurrent_after_watchlist' },
    { key: 'unknown', value: '1' },
  ]);
  expect(scanOverlapRows([])).toEqual([]);
});
});
