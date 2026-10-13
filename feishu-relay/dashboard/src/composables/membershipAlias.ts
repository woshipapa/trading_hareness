import type { MembershipRow, MembershipSuggestion } from '../api/memberships';

type MembershipDates = { started_on: string; term_months: number; fixed: boolean; expires_on: string };

const NUMERALS = ['', '一', '两', '三', '四', '五', '六', '七', '八', '九', '十', '十一', '十二'];
const monthDay = (date: string) => `${Number(date.slice(5, 7))}${Number(date.slice(8, 10))}`;

/** A membership the way the aliases write one: month and day without padding, then the term ("108三个月") or the end date ("918到1030"). */
export function aliasToken(dates: MembershipDates) {
  if (dates.fixed) return `${monthDay(dates.started_on)}到${monthDay(dates.expires_on)}`;
  const term = NUMERALS[dates.term_months];
  return term ? `${monthDay(dates.started_on)}${term}个月` : `${monthDay(dates.started_on)} ${dates.term_months}个月`;
}

/**
 * The alias with this membership written into it. It replaces the part the person picked in the form, else the part
 * that describes the row's present start date, else the only dated part; an alias that says nothing about dates, or
 * says several things with none of them this row's, gets the new part added at the end.
 */
export function rewrittenAlias(row: MembershipRow, dates: MembershipDates, picked: MembershipSuggestion | null = null) {
  const alias = row.alias ?? '';
  const offers = row.suggestions ?? [];
  const part = picked ?? offers.find((offer) => offer.started_on === row.started_on) ?? (offers.length === 1 ? offers[0] : null);
  const token = aliasToken(dates);
  if (part?.span) return `${alias.slice(0, part.span[0])}${token}${alias.slice(part.span[1])}`;
  return `${alias || row.display_name} ${token}`.trim();
}
