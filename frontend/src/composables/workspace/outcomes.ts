import { ref } from 'vue';
import type { AnalystReadiness, AnalystScorecard, AttributionValidationGate, IntradayAttributionSummary, IntradayOutcome, IntradayOutcomeSummary } from './types';
import type { ResearchShell } from './shell';
import type { MarketDataSlice } from './market-data';

/**
 * Settled outcomes: intraday strategy signals with their attribution cohorts
 * and validation gate, plus the analyst scorecards and their readiness gate.
 * Shown on 收盘复盘; the strategy evidence matrix and the analyst timeline
 * chart read the same `intradayOutcomes`.
 */
export function useOutcomesSlice(shell: ResearchShell, market: Pick<MarketDataSlice, 'sectorFlowDate'>) {
  const { runAction } = shell;
  const { sectorFlowDate } = market;

  const intradayOutcomes = ref<IntradayOutcome[]>([]);
  const intradayOutcomeSummary = ref<IntradayOutcomeSummary[]>([]);
  const intradayAttributionSummary = ref<IntradayAttributionSummary[]>([]);
  const attributionValidationGate = ref<AttributionValidationGate>({ status: 'accumulating', matured_unique_signals: 0, trading_days: 0, required_unique_signals: 200, required_trading_days: 60 });
  const analystReadiness = ref<AnalystReadiness[]>([]);
  const analystScorecards = ref<AnalystScorecard[]>([]);

  const outcomeStatusType = (value: string) => value === 'matured' ? 'success' : value === 'pending' ? 'warning' : 'info';
  const attributionDimensionLabel = (value: string) => ({ model_version: '模型版本', stage: '信号阶段', market_state: '市场环境', sector_linkage: '板块联动', volume_baseline: '同刻量能', microstructure_state: '盘口状态', price_volume_state: '量价相关', smart_money_state: '高信息量价' }[value] ?? value);
  const attributionCohortLabel = (value: string) => ({ acceptance: '承接确认', expansion: '首动扩张', extension_watch: '延伸观察', risk_exit: '风控退出', generic: '通用信号', rotation_defensive: '防御/资源轮动', rotation_technology: '科技轮动', broad_risk_on: '广泛偏强', broad_risk_off: '广泛偏弱', mixed_or_neutral: '混合/中性', peer_and_board_top10_confirmed: '同伴+板块Top10', peer_confirmed: '同伴联动确认', board_top10_positive: '正流入板块Top10', board_top10_nonpositive: '非正流入板块Top10', peers_not_confirmed: '同伴未确认', unobserved: '未观察到联动', ready: '基线可用', insufficient: '基线不足', not_applicable: '不适用' }[value] ?? value);
  const attributionStatusType = (value: string): 'success' | 'warning' | 'info' => value === 'cohort_reviewable' ? 'success' : value === 'descriptive_only' ? 'warning' : 'info';
  const analystReadinessText = (value: string) => ({ no_directional_stock_claims: '缺少方向明确的股票观点', fewer_than_30_settled_stock_outcomes: '已结算样本少于30条', eligible_for_scorecard_review: '达到成绩单复核门槛' }[value] ?? value);

  async function settleIntradayOutcomes() { await runAction('结算盘中信号', '/api/research/intraday/outcomes/recompute', { as_of_date: sectorFlowDate.value || undefined }, true); }
  async function recomputeAnalystScorecards() { await runAction('刷新分析师成绩单', '/api/research/scorecards/recompute', { as_of_date: sectorFlowDate.value || undefined }, true); }

  return {
    intradayOutcomes, intradayOutcomeSummary, intradayAttributionSummary, attributionValidationGate,
    analystReadiness, analystScorecards,
    outcomeStatusType, attributionDimensionLabel, attributionCohortLabel, attributionStatusType, analystReadinessText,
    settleIntradayOutcomes, recomputeAnalystScorecards,
  };
}

export type OutcomesSlice = ReturnType<typeof useOutcomesSlice>;
