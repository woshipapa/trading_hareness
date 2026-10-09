import { describe, expect, it } from 'vitest';
import { activeCapabilityCount, ageText, failingHealthCount, ledgerText, type BoardSource, type BoardStrategy } from './boards';

describe('research boards', () => {
  it('writes ages in minutes, hours or days', () => {
    const now = Date.parse('2026-10-09T05:30:00Z');
    expect(ageText('2026-10-09T05:27:00Z', now)).toBe('3 分钟前');
    expect(ageText('2026-10-09T00:18:00Z', now)).toBe('5.2 小时前');
    expect(ageText('2026-09-21T05:30:00Z', now)).toBe('18 天前');
    expect(ageText(null, now)).toBe('从未');
  });

  it('counts a source\'s live capabilities and its troubled health rows', () => {
    const source = {
      key: 'tencent_free', label: '腾讯', order: 20, lifecycle: 'active', verdict: 'circuit_open', reasons: [],
      capabilities: [
        { capability: 'limits.prices', label: '涨跌停价', priority: 20, status: 'live_verified' },
        { capability: 'bars.daily', label: '日K', priority: 45, status: 'dormant' }],
      health: [
        { capability: 'order_book_quote', consecutive_failures: 5, circuit_open: true },
        { capability: 'intraday_minute', consecutive_failures: 0, circuit_open: false }],
    } as BoardSource;
    expect([activeCapabilityCount(source), failingHealthCount(source)]).toEqual([1, 1]);
  });

  it('summarizes a strategy\'s ledger lines', () => {
    const strategy = { ledger_lines: { launch_radar: { as_of_date: '2026-10-08', candidates: 7 }, other: null } } as unknown as BoardStrategy;
    expect(ledgerText(strategy)).toBe('launch_radar 2026-10-08（7）；other 无');
  });
});
