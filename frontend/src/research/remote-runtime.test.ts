import { describe, expect, it } from 'vitest';
import { freshnessOf, normalizeSymbol, symbolFromRow } from './remote-runtime';

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
});
