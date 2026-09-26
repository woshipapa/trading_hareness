export type StrategyEvidenceRecommendation = {
  rank?: number;
  symbol: string;
  score?: number;
  direction?: number;
  decision?: string;
  confidence?: number;
  risk_flags?: string[];
};

export type StrategyEvidencePostCloseCandidate = {
  rank?: number;
  symbol: string;
  name?: string;
  candidate_type?: string;
  score?: number;
  risk_flags?: string[];
};

export type StrategyEvidenceDailyCandidate = {
  symbol: string;
  name?: string;
  candidate_type?: string;
  score?: number;
};

export type StrategyEvidenceOutcome = {
  symbol: string;
  horizon_key?: string;
  status?: string;
  raw_return?: number | null;
  observed_at?: string;
};

export type StrategyEvidenceMatrixRow = {
  symbol: string;
  name?: string;
  recommendation?: StrategyEvidenceRecommendation;
  post_close?: StrategyEvidencePostCloseCandidate;
  daily_candidate?: StrategyEvidenceDailyCandidate;
  outcome_count: number;
  matured_30m_count: number;
  pending_30m_count: number;
  last_30m_return?: number | null;
  risk_flags: string[];
  coverage: string[];
  alignment: '交叉支持' | '盘后待确认' | '仅策略' | '仅复盘';
};

export type StrategyEvidenceInput = {
  recommendations?: StrategyEvidenceRecommendation[];
  postCloseCandidates?: StrategyEvidencePostCloseCandidate[];
  dailyCandidates?: StrategyEvidenceDailyCandidate[];
  outcomes?: StrategyEvidenceOutcome[];
  names?: Record<string, string | undefined>;
};

const validSymbol = (symbol: unknown): symbol is string => typeof symbol === 'string' && /^\d{6}\.(SH|SZ|BJ)$/.test(symbol);

function addSymbol(rows: Map<string, StrategyEvidenceMatrixRow>, symbol: string, name?: string) {
  const existing = rows.get(symbol);
  if (existing) {
    if (!existing.name && name) existing.name = name;
    return existing;
  }
  const row: StrategyEvidenceMatrixRow = {
    symbol,
    name,
    outcome_count: 0,
    matured_30m_count: 0,
    pending_30m_count: 0,
    last_30m_return: null,
    risk_flags: [],
    coverage: [],
    alignment: '仅策略',
  };
  rows.set(symbol, row);
  return row;
}

function addCoverage(row: StrategyEvidenceMatrixRow, label: string) {
  if (!row.coverage.includes(label)) row.coverage.push(label);
}

export function buildStrategyEvidenceMatrix(input: StrategyEvidenceInput): StrategyEvidenceMatrixRow[] {
  const rows = new Map<string, StrategyEvidenceMatrixRow>();
  const names = input.names ?? {};
  const recommendations = input.recommendations ?? [];
  const postCloseCandidates = input.postCloseCandidates ?? [];
  const dailyCandidates = input.dailyCandidates ?? [];

  for (const recommendation of recommendations) {
    if (!validSymbol(recommendation.symbol)) continue;
    const row = addSymbol(rows, recommendation.symbol, names[recommendation.symbol]);
    row.recommendation = recommendation;
    addCoverage(row, '方向推荐');
    for (const flag of recommendation.risk_flags ?? []) if (!row.risk_flags.includes(flag)) row.risk_flags.push(flag);
  }
  for (const candidate of postCloseCandidates) {
    if (!validSymbol(candidate.symbol)) continue;
    const row = addSymbol(rows, candidate.symbol, candidate.name ?? names[candidate.symbol]);
    row.post_close = candidate;
    addCoverage(row, '盘后候选');
    for (const flag of candidate.risk_flags ?? []) if (!row.risk_flags.includes(flag)) row.risk_flags.push(flag);
  }
  for (const candidate of dailyCandidates) {
    if (!validSymbol(candidate.symbol)) continue;
    const row = addSymbol(rows, candidate.symbol, candidate.name ?? names[candidate.symbol]);
    row.daily_candidate = candidate;
    addCoverage(row, '日终摘要');
  }

  const outcomesBySymbol = new Map<string, StrategyEvidenceOutcome[]>();
  for (const outcome of input.outcomes ?? []) {
    if (!validSymbol(outcome.symbol)) continue;
    const values = outcomesBySymbol.get(outcome.symbol) ?? [];
    values.push(outcome);
    outcomesBySymbol.set(outcome.symbol, values);
    const row = addSymbol(rows, outcome.symbol, names[outcome.symbol]);
    row.outcome_count += 1;
    addCoverage(row, '盘中结果');
  }
  for (const [symbol, outcomes] of outcomesBySymbol) {
    const row = rows.get(symbol);
    if (!row) continue;
    const thirtyMinute = outcomes.filter((outcome) => outcome.horizon_key === '30m');
    row.matured_30m_count = thirtyMinute.filter((outcome) => outcome.status === 'matured').length;
    row.pending_30m_count = thirtyMinute.filter((outcome) => outcome.status === 'pending').length;
    const latest = [...thirtyMinute]
      .filter((outcome) => outcome.raw_return !== null && outcome.raw_return !== undefined)
      .sort((left, right) => String(right.observed_at ?? '').localeCompare(String(left.observed_at ?? '')))[0];
    row.last_30m_return = latest?.raw_return ?? null;
  }

  for (const row of rows.values()) {
    const strategySources = Number(Boolean(row.recommendation)) + Number(Boolean(row.post_close)) + Number(Boolean(row.daily_candidate));
    row.alignment = strategySources >= 2
      ? '交叉支持'
      : strategySources === 1 && Boolean(row.post_close)
        ? '盘后待确认'
        : strategySources === 1
          ? '仅策略'
          : '仅复盘';
    row.coverage.sort((left, right) => ['方向推荐', '盘后候选', '日终摘要', '盘中结果'].indexOf(left) - ['方向推荐', '盘后候选', '日终摘要', '盘中结果'].indexOf(right));
  }

  return [...rows.values()].sort((left, right) => {
    const score = (row: StrategyEvidenceMatrixRow) => Math.max(row.recommendation?.score ?? -Infinity, row.post_close?.score ?? -Infinity, row.daily_candidate?.score ?? -Infinity);
    return score(right) - score(left) || right.coverage.length - left.coverage.length || left.symbol.localeCompare(right.symbol);
  });
}
