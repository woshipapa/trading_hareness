import { describe, expect, it } from 'vitest';
import { aliasToken, rewrittenAlias } from './membershipAlias';
import type { MembershipRow, MembershipSuggestion } from '../api/memberships';

const row = (alias: string, started_on: string, suggestions: MembershipSuggestion[]): MembershipRow => ({
  target_chat_id: 'g', user_id: 'u', display_name: '群昵称', alias, started_on, term_months: 3, expires_on: '2026-11-23', note: null, reminded_on: null, remind_claimed_at: null, left_at: null, created_at: '', updated_at: '', status: '正常', suggestions,
});
const offer = (started_on: string | null, span: [number, number], label = ''): MembershipSuggestion => ({ started_on, term_months: 3, expires_on: null, label, span });
const counted = (started_on: string, term_months: number) => ({ started_on, term_months, fixed: false, expires_on: '' });

describe('writing a membership into an alias', () => {
  it('writes month and day without padding, then the term or the end date', () => {
    expect(aliasToken(counted('2026-10-08', 3))).toBe('108三个月');
    expect(aliasToken(counted('2026-09-04', 1))).toBe('94一个月');
    expect(aliasToken(counted('2027-01-08', 12))).toBe('18十二个月');
    expect(aliasToken(counted('2026-10-08', 18))).toBe('108 18个月');
    expect(aliasToken({ started_on: '2026-09-18', term_months: 1, fixed: true, expires_on: '2026-10-30' })).toBe('918到1030');
  });

  it('replaces the part that described the present dates and leaves the rest of the alias alone', () => {
    const one = row('阿明 823三个月', '2026-08-23', [offer('2026-08-23', [3, 9])]);
    expect(rewrittenAlias(one, counted('2026-11-23', 3))).toBe('阿明 1123三个月');
    const two = row('阿强安强827三个917一个', '2026-09-17', [offer('2026-08-27', [4, 9]), offer('2026-09-17', [9, 14])]);
    expect(rewrittenAlias(two, counted('2026-10-17', 1))).toBe('阿强安强827三个1017一个月');
  });

  it('replaces the part the person picked in the form before any other', () => {
    const two = row('阿强安强827三个917一个', '2026-09-30', [offer('2026-08-27', [4, 9]), offer('2026-09-17', [9, 14])]);
    expect(rewrittenAlias(two, counted('2026-11-27', 3), two.suggestions![0])).toBe('阿强安强1127三个月917一个');
  });

  it('adds the new part at the end when the alias says nothing about this row, and starts from the group name when there is no alias', () => {
    const several = row('阿强安强827三个917一个', '2026-09-30', [offer('2026-08-27', [4, 9]), offer('2026-09-17', [9, 14])]);
    expect(rewrittenAlias(several, counted('2026-10-08', 3))).toBe('阿强安强827三个917一个 108三个月');
    expect(rewrittenAlias(row('老王', '2026-09-30', []), counted('2026-10-08', 3))).toBe('老王 108三个月');
    expect(rewrittenAlias(row('', '2026-09-30', []), counted('2026-10-08', 3))).toBe('群昵称 108三个月');
  });
});
