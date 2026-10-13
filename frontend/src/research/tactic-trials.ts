/** 战法试验台: types of the three reads and the pure helpers of the tab. Research-only evidence. */

export type TacticSide = 'focus' | 'hold' | 'avoid';
export type TacticSlot = 'close' | 'auction';
export type TacticLabel = 'collecting' | 'supports' | 'against' | 'undecided';

export interface TacticCard {
  key: string;
  name: string;
  slot: TacticSlot;
  side: TacticSide;
  source: string;
  entry: string;
  exit: string;
  primary: string;
  primary_name: string;
  expects: number;
  registered_on: string;
  combination: boolean;
}

export interface BoardRow {
  symbol: string;
  name: string | null;
  sector: string | null;
  boards: number | null;
  gap: number | null;
  opens_at_limit: boolean | null;
  amount: number | null;
  auction_amount_rank: number | null;
  risk_rank: number | null;
  first_seal_time: string | null;
  seal_money: number | null;
  tactics: string[];
  conditions: string[];
}

export type BoardSides = Record<TacticSide, BoardRow[]>;

export interface TacticBoard {
  version: string;
  trade_date: string;
  auction: (BoardSides & { decided_at: string; source: string | null; rows: number }) | null;
  close: (BoardSides & { trade_date: string; rows: number }) | null;
  labels_as_of: string | null;
  tactics: (TacticCard & { label: TacticLabel | null; q: number | null })[];
  research_only: boolean;
  live_effect: 'none';
}

export interface TacticRecord {
  days_with_picks: number;
  picks: number;
  mean_daily_picks_mean: number | null;
  mean_daily_difference: number | null;
  t: number | null;
  positive_share: number | null;
  in_sample: boolean;
}

export interface SummaryRecord {
  name: string;
  slot: TacticSlot;
  primary: string;
  primary_name: string;
  expects: number;
  records: { replay: TacticRecord; forward: TacticRecord; recent: TacticRecord };
  label: TacticLabel;
  q: number | null;
}

export interface ConditionDay {
  key: string;
  name: string;
  picks: number;
  pick_mean: number | null;
  comparison_mean: number | null;
  difference: number | null;
  expects: number;
}

export interface MinedPair {
  conditions: string[];
  names: string[];
  days_with_picks: number;
  picks: number;
  mean_daily_difference: number | null;
  t: number | null;
  positive_share: number | null;
}

export interface PairTable {
  mined: boolean;
  pairs_examined: number;
  pairs_reaching_minimum: number;
  highest: MinedPair[];
  lowest: MinedPair[];
}

export interface TacticSummaryReading {
  trade_date: string;
  version: string;
  lines: string[];
  records: Record<string, SummaryRecord>;
  conditions: ConditionDay[];
  pairs: Record<TacticSlot, PairTable>;
  pair_sessions: number;
  label_changes: { key: string; previous: TacticLabel | null; current: TacticLabel }[];
}

export interface TacticSummary {
  version: string;
  reading: TacticSummaryReading | null;
  research_only: boolean;
  live_effect: 'none';
}

export interface TacticCaseDay {
  trade_date: string;
  settled: boolean;
  picks: { symbol: string; name: string | null; boards: number | null; primary: number | boolean | null }[];
  pick_mean: number | null;
  comparison_mean: number | null;
  difference: number | null;
}

export interface TacticCases {
  version: string;
  tactic: TacticCard;
  sessions: number;
  days: TacticCaseDay[];
  research_only: boolean;
  live_effect: 'none';
}

export const SIDE_NAMES: Record<TacticSide, string> = { focus: '关注', hold: '持有参考', avoid: '回避' };
export const SLOT_NAMES: Record<TacticSlot, string> = { close: '收盘', auction: '竞价' };
export const LABEL_NAMES: Record<TacticLabel, string> = { collecting: '样本不足', supports: '支持', against: '相反', undecided: '未定' };
const LABEL_TAGS: Record<TacticLabel, 'info' | 'success' | 'danger' | 'warning'> = {
  collecting: 'info', supports: 'success', against: 'danger', undecided: 'warning',
};

export const labelName = (label: TacticLabel | null | undefined): string => (label ? LABEL_NAMES[label] : '尚无战绩');
export const labelTag = (label: TacticLabel | null | undefined) => (label ? LABEL_TAGS[label] : 'info');

export function percent(value: number | boolean | null | undefined, digits = 2): string {
  if (value === null || value === undefined) return '—';
  if (typeof value === 'boolean') return value ? '是' : '否';
  return `${value >= 0 ? '+' : ''}${(value * 100).toFixed(digits)}%`;
}

export const fixed = (value: number | null | undefined, digits = 2): string => (value === null || value === undefined ? '—' : value.toFixed(digits));

/** The board's rows of one side, each with the names and labels of the tactics that picked it. */
export function boardRows(sides: BoardSides | null, side: TacticSide, board: TacticBoard) {
  const cards = new Map(board.tactics.map((item) => [item.key, item]));
  return (sides?.[side] ?? []).map((row) => ({
    ...row,
    picked_by: row.tactics.map((key) => ({ key, name: cards.get(key)?.name ?? key, label: cards.get(key)?.label ?? null })),
  }));
}

export interface RecordRow {
  key: string;
  name: string;
  slot: TacticSlot;
  side: TacticSide | null;
  source: string | null;
  primary_name: string;
  combination: boolean;
  label: TacticLabel;
  q: number | null;
  replay: TacticRecord;
  forward: TacticRecord;
  recent: TacticRecord;
}

/** The tactic table: the registered combinations first, in registry order, then the single conditions. */
export function recordRows(summary: TacticSummaryReading | null, cards: TacticCard[]): RecordRow[] {
  if (!summary) return [];
  const byKey = new Map(cards.map((item) => [item.key, item]));
  return Object.entries(summary.records)
    .map(([key, value]) => ({
      key, name: value.name, slot: value.slot, side: byKey.get(key)?.side ?? null, source: byKey.get(key)?.source ?? null,
      primary_name: value.primary_name, combination: byKey.has(key), label: value.label, q: value.q, ...value.records,
    }))
    .sort((left, right) => Number(right.combination) - Number(left.combination));
}

export interface SummarySection { title: string; lines: string[] }

/** The summary's lines cut at its numbered headings (一、…七、). */
export function summarySections(lines: string[]): SummarySection[] {
  const sections: SummarySection[] = [];
  for (const line of lines) {
    if (/^[一二三四五六七八九十]+、/.test(line)) sections.push({ title: line, lines: [] });
    else if (sections.length) sections[sections.length - 1].lines.push(line);
  }
  return sections;
}

export const pairRows = (table: PairTable | undefined): (MinedPair & { direction: '最高' | '最低' })[] => (table
  ? [...table.highest.map((pair) => ({ ...pair, direction: '最高' as const })), ...table.lowest.map((pair) => ({ ...pair, direction: '最低' as const }))]
  : []);
