import { computed, ref } from 'vue';
import { ElMessage } from 'element-plus';
import { buildStrategyEvidenceMatrix, type StrategyEvidenceDailyCandidate, type StrategyEvidenceMatrixRow } from '../../research/strategy-evidence';
import type { FeatureItem, Recommendation, UniverseMember } from './types';
import type { ResearchShell } from './shell';
import type { AnalystTimelineSlice } from './analyst-timeline';
import type { CloseReviewSlice } from './close-review';
import type { OutcomesSlice } from './outcomes';
import type { OverviewSlice } from './overview';
import type { StockStudySlice } from './stock-study';
import type { StrategyGovernanceSlice } from './strategy-governance';

/** What the 策略 x 日终复盘 matrix reads from the other slices. */
export type StrategyPoolSources = {
  overview: Pick<OverviewSlice, 'dailyStrategySummary'>;
  closeReview: Pick<CloseReviewSlice, 'postCloseCandidates' | 'postCloseStrategyRun' | 'closeStrategyReview' | 'closeShortTermReview'>;
  outcomes: Pick<OutcomesSlice, 'intradayOutcomes'>;
  governance: Pick<StrategyGovernanceSlice, 'strategyHealth'>;
  stockStudy: Pick<StockStudySlice, 'studySymbol'>;
  analystTimeline: Pick<AnalystTimelineSlice, 'analystTimelineDate' | 'loadAnalystStockTimeline'>;
};

/**
 * 策略与股票池: the core universe editor, direction recommendations, feature
 * evidence and the strategy x daily review matrix, whose rows open the
 * analyst evidence timeline for a symbol.
 */
export function useStrategyPoolSlice(shell: ResearchShell, sources: StrategyPoolSources) {
  const { activeResearchTab, runAction } = shell;
  const { dailyStrategySummary } = sources.overview;
  const { postCloseCandidates, postCloseStrategyRun, closeStrategyReview, closeShortTermReview } = sources.closeReview;
  const { intradayOutcomes } = sources.outcomes;
  const { strategyHealth } = sources.governance;
  const { studySymbol } = sources.stockStudy;
  const { analystTimelineDate, loadAnalystStockTimeline } = sources.analystTimeline;

  const recommendations = ref<Recommendation[]>([]);
  const universe = ref<UniverseMember[]>([]);
  const featureItems = ref<FeatureItem[]>([]);
  const universeText = ref('');
  const universePriority = ref(100);

  const featureRecord = (row: FeatureItem, name: string): Record<string, unknown> => {
    const value = row.features[name];
    return value && typeof value === 'object' && !Array.isArray(value) ? value as Record<string, unknown> : {};
  };
  const strategyEvidenceDailyCandidates = computed<StrategyEvidenceDailyCandidate[]>(() => {
    const candidates = dailyStrategySummary.value?.payload?.post_close?.candidates;
    if (!Array.isArray(candidates)) return [];
    return candidates.filter((candidate): candidate is StrategyEvidenceDailyCandidate => (
      candidate && typeof candidate === 'object' && typeof candidate.symbol === 'string'
    ));
  });
  const strategyEvidenceMatrix = computed<StrategyEvidenceMatrixRow[]>(() => buildStrategyEvidenceMatrix({
    recommendations: recommendations.value,
    postCloseCandidates: postCloseCandidates.value,
    dailyCandidates: strategyEvidenceDailyCandidates.value,
    outcomes: intradayOutcomes.value,
    names: Object.fromEntries(universe.value.map((item) => [item.symbol, item.name])),
  }));
  const strategyReviewContext = computed(() => ({
    exchange_date: dailyStrategySummary.value?.exchange_date ?? closeStrategyReview.value?.exchange_date ?? '-',
    daily_delivery_status: dailyStrategySummary.value?.delivery_status ?? '缺失',
    market_state: closeStrategyReview.value?.market_state ?? dailyStrategySummary.value?.payload?.close_review?.market_state ?? '待生成',
    post_close_status: dailyStrategySummary.value?.payload?.post_close?.status ?? postCloseStrategyRun.value?.status ?? '缺失',
    post_close_reason: dailyStrategySummary.value?.payload?.post_close?.reason ?? '',
    validation_gate: dailyStrategySummary.value?.payload?.offline_policy_learning?.validation_gate?.status ?? strategyHealth.value?.validation_gate?.status ?? '未生成',
    review_risk_flags: closeShortTermReview.value?.loss_effect?.risk_flags ?? [],
  }));
  const strategyEvidenceAlignmentType = (value: StrategyEvidenceMatrixRow['alignment']): 'success' | 'warning' | 'danger' | 'info' => value === '交叉支持' ? 'success' : value === '盘后待确认' ? 'warning' : value === '仅复盘' ? 'info' : 'warning';

  async function saveUniverse() {
    const symbols = universeText.value.split(/[\s,;]+/).map((item) => item.trim().toUpperCase()).filter(Boolean);
    if (!symbols.length) { ElMessage.error('至少输入一个股票代码'); return; }
    await runAction('更新核心股票池', '/api/research/universes/members', { universe_key: 'core', symbols, enabled: true, priority: universePriority.value });
  }
  async function openStrategyEvidence(symbol: string) {
    if (!/^\d{6}\.(SH|SZ|BJ)$/.test(symbol)) return;
    studySymbol.value = symbol;
    analystTimelineDate.value = strategyReviewContext.value.exchange_date === '-' ? '' : strategyReviewContext.value.exchange_date;
    activeResearchTab.value = 'evidence';
    await loadAnalystStockTimeline();
  }

  return {
    recommendations, universe, featureItems, universeText, universePriority,
    featureRecord, strategyEvidenceMatrix, strategyReviewContext, strategyEvidenceAlignmentType,
    saveUniverse, openStrategyEvidence,
  };
}

export type StrategyPoolSlice = ReturnType<typeof useStrategyPoolSlice>;
