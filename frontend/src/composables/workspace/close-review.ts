import { computed, ref } from 'vue';
import { ElMessage } from 'element-plus';
import type {
  Announcement, BoardReviewReport, ConceptBackfill, DragonLeaderWatch, LimitLadderRow, LimitPoolCoverage, LimitPoolRow,
  PostCloseCandidate, PostCloseStrategyRun, StrategyPatternRun, StrategyPatternSample, StrategyReview, TenDayLeaderRotation,
} from './types';
import type { ResearchShell } from './shell';
import type { MarketDataSlice } from './market-data';

/**
 * 收盘复盘: the saved board review report and concept-member backfill, the
 * close strategy review (multi-index regime and the short-term review), the
 * LHB background, post-close candidates, limit-up pattern mining and the
 * ten-day leader rotation shadow pool.
 */
export function useCloseReviewSlice(shell: ResearchShell, market: Pick<MarketDataSlice, 'sectorFlowDate'>) {
  const { runAction } = shell;
  const { sectorFlowDate } = market;

  const lhbEvents = ref<Announcement[]>([]);
  const closeBoardReport = ref<BoardReviewReport | null>(null);
  const conceptBackfill = ref<ConceptBackfill>({ total_concepts: 0, mapped_concepts: 0, states: [] });
  const closeStrategyReview = ref<StrategyReview | null>(null);
  const selectedReviewBoardKey = ref('');
  const postCloseStrategyRun = ref<PostCloseStrategyRun | null>(null);
  const postCloseCandidates = ref<PostCloseCandidate[]>([]);
  const strategyPatternRun = ref<StrategyPatternRun | null>(null);
  const tenDayLeaderRotation = ref<TenDayLeaderRotation>({ candidates: [] });
  const strategyLimitPool = ref<LimitPoolRow[]>([]);
  const strategyLimitLadder = ref<LimitLadderRow[]>([]);
  const strategyContinuationCandidates = ref<LimitPoolRow[]>([]);
  const strategyDragonLeaderCandidates = ref<LimitPoolRow[]>([]);
  const strategyDragonLeaderMarket = ref<DragonLeaderWatch['market_context']>({});
  const strategyPoolCoverage = ref<LimitPoolCoverage>({});
  const strategyPatternPicks = ref<StrategyPatternSample[]>([]);
  const strategyPatternSamples = ref<StrategyPatternSample[]>([]);

  const postCloseCandidateLabel = (value: PostCloseCandidate['candidate_type']) => value === 'base_ready_30d' ? '30日蓄势就绪' : value === 'base_forming_15d' ? '15日形成中' : '15日首动';
  const postCloseCandidateType = (value: PostCloseCandidate['candidate_type']) => value === 'base_ready_30d' ? 'success' : value === 'fresh_start_15d' ? 'warning' : 'info';
  const patternCohortLabel = (value: string) => ({ focus: '重点研究', dragon_leader_watch: '龙头复核', limit_continuation_watch: '连板延续', ground_to_sky: '地天反转', preopen_market_leader: '盘前辨识度', market_leader: '盘后辨识度', board_leader: '板块龙头', consecutive_limit: '连板梯队', first_board: '首板' }[value] ?? value);
  const reviewTierText = (value?: string) => value === 'priority_review' ? '优先复核' : value === 'candidate_review' ? '候选复核' : '研究样本';
  const reviewConceptBoards = computed(() => (closeBoardReport.value?.payload?.items ?? [])
    .filter((item) => item.taxonomy_key === 'ths_concept_flow' && item.mapped_members > 0)
    .sort((left, right) => Number(right.net_inflow ?? -Infinity) - Number(left.net_inflow ?? -Infinity)));
  const selectedReviewBoard = computed(() => reviewConceptBoards.value.find((item) => item.sector_key === selectedReviewBoardKey.value) ?? reviewConceptBoards.value[0] ?? null);
  const selectedReviewBoardStocks = computed(() => selectedReviewBoard.value?.top_stocks ?? []);
  const completedBackfillBoards = computed(() => conceptBackfill.value.states.filter((item) => item.state === 'completed' || item.state === 'empty').reduce((total, item) => total + Number(item.boards || 0), 0));
  const closeIndexRegime = computed(() => closeStrategyReview.value?.report?.index_breadth_context?.multi_index_regime ?? null);
  const closeShortTermReview = computed(() => closeStrategyReview.value?.report?.short_term_review ?? null);
  const indexRegimeLabel = computed(() => ({
    corrective_rebound: '纠错反弹情景', trend_recovery: '趋势修复', weak_or_declining: '弱势/下行', mixed_transition: '混合过渡', insufficient_index_history: '历史不足',
  }[closeIndexRegime.value?.state ?? ''] ?? closeIndexRegime.value?.state ?? '待生成'));
  const indexRegimeType = computed((): 'success' | 'warning' | 'danger' | 'info' => closeIndexRegime.value?.state === 'trend_recovery' ? 'success' : closeIndexRegime.value?.state === 'corrective_rebound' || closeIndexRegime.value?.state === 'mixed_transition' ? 'warning' : closeIndexRegime.value?.state === 'weak_or_declining' ? 'danger' : 'info');
  const indexLabel = (symbol: string) => ({ '000001.SH': '上证指数', '000300.SH': '沪深300', '399001.SZ': '深证成指', '399006.SZ': '创业板指' }[symbol] ?? symbol);

  async function refreshCloseReview() { await runAction('保存收盘板块复盘', '/api/research/market/sectors/review/report/run', {}, true); }
  async function advanceConceptBackfill() { if (!sectorFlowDate.value) { ElMessage.error('请选择交易日'); return; } await runAction('补齐一批概念成员', '/api/research/market/sectors/concepts/members/backfill/run', { trade_date: sectorFlowDate.value, provider: 'super', batch_size: 25 }, true); }
  async function runPostCloseStrategy() { await runAction('运行盘后蓄势与首动筛选', '/api/research/strategy/post-close/run', { limit: 20 }, true); }
  async function runStrategyPatternMining() { await runAction('挖掘涨停拉升形态', '/api/research/strategy/pattern-mining/run', { max_symbols: 20, per_cohort: 6, refresh_limit_sources: true }, true); }
  async function runTenDayLeaderRotation() { await runAction('重算十日排行榜影子池', '/api/research/ten-day-leader-rotation/run', {}, true); }

  return {
    lhbEvents, closeBoardReport, conceptBackfill, closeStrategyReview, selectedReviewBoardKey,
    postCloseStrategyRun, postCloseCandidates, strategyPatternRun, tenDayLeaderRotation,
    strategyLimitPool, strategyLimitLadder, strategyContinuationCandidates, strategyDragonLeaderCandidates,
    strategyDragonLeaderMarket, strategyPoolCoverage, strategyPatternPicks, strategyPatternSamples,
    postCloseCandidateLabel, postCloseCandidateType, patternCohortLabel, reviewTierText, indexLabel,
    reviewConceptBoards, selectedReviewBoard, selectedReviewBoardStocks, completedBackfillBoards,
    closeIndexRegime, closeShortTermReview, indexRegimeLabel, indexRegimeType,
    refreshCloseReview, advanceConceptBackfill, runPostCloseStrategy, runStrategyPatternMining, runTenDayLeaderRotation,
  };
}

export type CloseReviewSlice = ReturnType<typeof useCloseReviewSlice>;
