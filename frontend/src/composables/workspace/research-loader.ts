import type { AnalystMarketReview, AutomationRun } from '../../api/analyst-contract';
import type {
  AnalystClaim, AnalystMarketEvaluation, AnalystObservation, AnalystPromptLab, AnalystReadiness, AnalystResearchStatus,
  AnalystScorecard, AnalystSkillProfile, Announcement, AttributionValidationGate, BoardReviewReport, ClaimReview,
  ConceptBackfill, ConceptCandidate, ConceptSignal, DailyStrategySummary, DragonLeaderWatch, Factor, FactorEvaluation,
  FeatureItem, Framework, IntradayAttributionSummary, IntradayOutcome, IntradayOutcomeSummary, LimitLadderRow,
  LimitPoolCoverage, LimitPoolRow, MarketSnapshot, MinuteImport, PaperStatus, PostCloseCandidate, PostCloseStrategyRun,
  ProviderApiCapability, ProviderHealth, QualityIssue, RealtimeProviderHealth, Recommendation, RemoteMessage, RemoteReport,
  ReplayReadiness, ResearchOverview, ResearchRun, Sector, SectorFlow, Strategy, StrategyAblation, StrategyExperiment,
  StrategyFunnel, StrategyGovernance, StrategyHealth, StrategyPatternRun, StrategyPatternSample, StrategyReview,
  TenDayLeaderRotation, TrainingRoadmap, UniverseMember,
} from './types';
import type { ResearchShell } from './shell';
import type { AnalystEvidenceSlice } from './analyst-evidence';
import type { CloseReviewSlice } from './close-review';
import type { FactorLabSlice } from './factor-lab';
import type { MarketDataSlice } from './market-data';
import type { OutcomesSlice } from './outcomes';
import type { OverviewSlice } from './overview';
import type { ProvidersSlice } from './providers';
import type { StrategyGovernanceSlice } from './strategy-governance';
import type { StrategyPoolSlice } from './strategy-pool';

/** One research read and the bindings its payload fills. */
type ResearchRead = { promise: Promise<unknown>; apply: (value: any) => void };

/** Keep each request next to its unpacking, so the list and the results cannot drift apart. */
function read<T>(promise: Promise<T>, apply: (value: T) => void): ResearchRead {
  return { promise, apply };
}

/**
 * Unpack a settled batch in list order and return how many reads failed.  A
 * rejected read unpacks as `{}`: list panels fall back to their empty
 * default and whole-payload panels take the empty object.
 */
function unpack(reads: ResearchRead[], results: PromiseSettledResult<unknown>[]) {
  const failures = results.filter((result) => result.status === 'rejected').length;
  results.forEach((result, index) => reads[index].apply(result.status === 'fulfilled' ? result.value : {}));
  return failures;
}

export type ResearchSlices = {
  overview: OverviewSlice;
  marketData: MarketDataSlice;
  closeReview: CloseReviewSlice;
  outcomes: OutcomesSlice;
  strategyPool: StrategyPoolSlice;
  factorLab: FactorLabSlice;
  analystEvidence: AnalystEvidenceSlice;
  governance: StrategyGovernanceSlice;
  providers: ProvidersSlice;
};

/**
 * `loadResearch` hydrates every research panel in one pass:
 *
 * 1. the overview and replay readiness.  An overview failure gets its own
 *    banner; a replay-readiness failure keeps the previous value;
 * 2. one concurrent batch of primary panel reads, then one of secondary reads;
 * 3. the research-run ledger, after which the daily summary from batch 2 is shown.
 *
 * A failed read never blanks the other panels.  Failures in the two batches
 * are reported together as "部分研究接口暂时不可用（N 项）", which replaces an
 * overview banner.  Only one load runs at a time, and leaving the research
 * section aborts it (see the shell).
 */
export function useResearchLoader(shell: ResearchShell, slices: ResearchSlices) {
  const { loading, researchError } = shell;
  const { getJson, researchLoaded, beginResearchLoad, endResearchLoad } = shell.transport;
  const { overview, replayReadiness, researchRuns, dailyStrategySummary } = slices.overview;
  const { providerApiCapabilities, marketSnapshots, sectors, sectorFlows, conceptSignals, conceptCandidates, announcements, sectorFlowDate } = slices.marketData;
  const {
    lhbEvents, closeBoardReport, conceptBackfill, closeStrategyReview, postCloseStrategyRun, postCloseCandidates, strategyPatternRun, tenDayLeaderRotation,
    strategyLimitPool, strategyLimitLadder, strategyContinuationCandidates, strategyDragonLeaderCandidates, strategyDragonLeaderMarket, strategyPoolCoverage,
    strategyPatternPicks, strategyPatternSamples,
  } = slices.closeReview;
  const { intradayOutcomes, intradayOutcomeSummary, intradayAttributionSummary, attributionValidationGate, analystReadiness, analystScorecards } = slices.outcomes;
  const { recommendations, universe, featureItems, universeText } = slices.strategyPool;
  const { factors, factorEvaluations, strategies, strategyExperiments, mainWaveExperiments, frameworks, trainingRoadmap, selectedFactors } = slices.factorLab;
  const {
    reports, remoteMessages, analystSkills, analystResearchStatus, claims, analystObservations, analystSyncHealth, analystPromptLab,
    analystMarketEvaluation, analystDailyReview, analystWeeklyReview, analystReviewRuns, automationRuns, claimReviews,
  } = slices.analystEvidence;
  const { paperStatus, strategyFunnel, strategyGovernance, strategyAblation, strategyHealth } = slices.governance;
  const { providerHealth, realtimeProviderHealth, qualityIssues, minuteImports, minuteDirectory } = slices.providers;

  async function loadResearch() {
    if (loading.value) return;
    const controller = beginResearchLoad();
    loading.value = true; researchError.value = '';
    try {
      const [overviewResult, replayReadinessResult] = await Promise.allSettled([
        getJson<ResearchOverview>('/api/research/overview'),
        getJson<ReplayReadiness>('/api/research/data-readiness/replay'),
      ]);
      if (overviewResult.status === 'fulfilled') overview.value = overviewResult.value;
      else researchError.value = `研究概览读取失败：${overviewResult.reason instanceof Error ? overviewResult.reason.message : String(overviewResult.reason)}`;
      if (replayReadinessResult.status === 'fulfilled') replayReadiness.value = replayReadinessResult.value;
      const primaryReads = [
        read(getJson<{ items?: RemoteReport[] }>('/api/research/reports?limit=30'), (reportsData) => { reports.value = reportsData.items ?? []; }),
        read(getJson<{ items?: AnalystClaim[] }>('/api/research/claims?limit=80'), (claimsData) => { claims.value = claimsData.items ?? []; }),
        read(getJson<{ items?: ProviderHealth[] }>('/api/research/providers'), (healthData) => { providerHealth.value = healthData.items ?? []; }),
        read(getJson<RealtimeProviderHealth>('/api/research/providers/realtime-health').catch(() => ({ items: [], evidence_mode: 'unavailable_on_current_release' })), (realtimeProviderHealthData) => { realtimeProviderHealth.value = realtimeProviderHealthData; }),
        read(getJson<{ items?: ProviderApiCapability[] }>('/api/research/provider-capabilities'), (capabilityData) => { providerApiCapabilities.value = capabilityData.items ?? []; }),
        read(getJson<{ items?: MarketSnapshot[] }>('/api/research/market/snapshots?limit=20'), (snapshotData) => { marketSnapshots.value = snapshotData.items ?? []; }),
        read(getJson<{ items?: Sector[] }>('/api/research/market/sectors?taxonomy_key=ths_index_n&limit=500'), (sectorData) => { sectors.value = sectorData.items ?? []; }),
        read(getJson<{ items?: SectorFlow[] }>('/api/research/market/sector-flows?taxonomy_key=ths_industry&limit=100'), (sectorFlowData) => { sectorFlows.value = sectorFlowData.items ?? []; }),
        read(getJson<{ items?: ConceptSignal[] }>('/api/research/market/sectors/concepts?limit=100'), (conceptSignalData) => { conceptSignals.value = conceptSignalData.items ?? []; }),
        read(getJson<{ items?: ConceptCandidate[] }>('/api/research/market/sectors/concepts/candidates?limit=100'), (conceptCandidateData) => { conceptCandidates.value = conceptCandidateData.items ?? []; }),
        read(getJson<{ items?: Announcement[] }>('/api/research/events/announcements?limit=100'), (announcementData) => { announcements.value = announcementData.items ?? []; }),
        read(getJson<{ items?: Announcement[] }>('/api/research/events/lhb?limit=100'), (lhbData) => { lhbEvents.value = lhbData.items ?? []; }),
        read(getJson<{ report?: BoardReviewReport | null }>('/api/research/market/sectors/review/report/latest'), (boardReviewData) => { closeBoardReport.value = boardReviewData.report ?? null; }),
        read(getJson<ConceptBackfill>('/api/research/market/sectors/concepts/members/backfill/status'), (backfillData) => {
          // A failed read unpacks as {}; the close-review counters still need a states list.
          const data: Partial<ConceptBackfill> = backfillData;
          conceptBackfill.value = { ...data, total_concepts: data.total_concepts ?? 0, mapped_concepts: data.mapped_concepts ?? 0, states: data.states ?? [] };
        }),
        read(getJson<{ review?: StrategyReview | null }>('/api/research/strategy/reviews/latest?session=close'), (strategyReviewData) => { closeStrategyReview.value = strategyReviewData.review ?? null; }),
        read(getJson<{ run?: PostCloseStrategyRun | null; candidates?: PostCloseCandidate[] }>('/api/research/strategy/post-close/latest'), (postCloseStrategyData) => {
          postCloseStrategyRun.value = postCloseStrategyData.run ?? null;
          postCloseCandidates.value = postCloseStrategyData.candidates ?? [];
        }),
        read(getJson<{ run?: StrategyPatternRun | null; limit_pool?: LimitPoolRow[]; limit_ladder?: LimitLadderRow[]; continuation_candidates?: LimitPoolRow[]; dragon_leader_candidates?: LimitPoolRow[]; dragon_leader_market_context?: DragonLeaderWatch['market_context']; pool_coverage?: LimitPoolCoverage; picks?: StrategyPatternSample[]; samples?: StrategyPatternSample[] }>('/api/research/strategy/pattern-mining/latest'), (patternData) => {
          strategyPatternRun.value = patternData.run ?? null;
          strategyLimitPool.value = patternData.limit_pool ?? [];
          strategyLimitLadder.value = patternData.limit_ladder ?? [];
          strategyContinuationCandidates.value = patternData.continuation_candidates ?? [];
          strategyDragonLeaderCandidates.value = patternData.dragon_leader_candidates ?? [];
          strategyDragonLeaderMarket.value = patternData.dragon_leader_market_context ?? {};
          strategyPoolCoverage.value = patternData.pool_coverage ?? {};
          strategyPatternPicks.value = patternData.picks ?? [];
          strategyPatternSamples.value = patternData.samples ?? [];
        }),
        read(getJson<TenDayLeaderRotation>('/api/research/ten-day-leader-rotation/latest?limit=90').catch(() => ({ run: null, candidates: [], scope: 'research_only_no_orders', notice: '十日排行榜影子研究尚未部署到当前服务。' })), (tenDayLeaderRotationData) => { tenDayLeaderRotation.value = tenDayLeaderRotationData; }),
        read(getJson<{ recommendations?: Recommendation[] }>('/api/research/recommendations'), (recommendationData) => { recommendations.value = recommendationData.recommendations ?? []; }),
        read(getJson<{ items?: UniverseMember[] }>('/api/research/universes/core'), (universeData) => { universe.value = universeData.items ?? []; }),
        read(getJson<{ items?: FeatureItem[] }>('/api/research/features/latest?universe_key=core'), (featuresData) => { featureItems.value = featuresData.items ?? []; }),
        read(getJson<{ items?: ClaimReview[] }>('/api/research/claim-review?status=pending'), (reviewsData) => { claimReviews.value = reviewsData.items ?? []; }),
        read(getJson<{ items?: Factor[] }>('/api/research/factors'), (factorData) => { factors.value = factorData.items ?? []; }),
        read(getJson<{ items?: FactorEvaluation[] }>('/api/research/factor-evaluations?universe_key=all_a'), (factorEvaluationData) => { factorEvaluations.value = factorEvaluationData.items ?? []; }),
        read(getJson<{ items?: Strategy[] }>('/api/research/strategies'), (strategyData) => { strategies.value = strategyData.items ?? []; }),
        read(getJson<{ items?: StrategyExperiment[] }>('/api/research/strategy-experiments?universe_key=all_a'), (experimentData) => { strategyExperiments.value = experimentData.items ?? []; }),
        read(getJson<{ items?: StrategyExperiment[] }>('/api/research/strategy-experiments-watchlist?universe_key=watchlist&limit=10'), (mainWaveData) => { mainWaveExperiments.value = mainWaveData.items ?? []; }),
        read(getJson<{ items?: Framework[] }>('/api/research/frameworks'), (frameworkData) => { frameworks.value = frameworkData.items ?? []; }),
        read(getJson<TrainingRoadmap>('/api/research/training/roadmap'), (roadmapData) => { trainingRoadmap.value = roadmapData; }),
        read(getJson<{ items?: QualityIssue[] }>('/api/research/quality?limit=100'), (qualityData) => { qualityIssues.value = qualityData.items ?? []; }),
        read(getJson<{ items?: MinuteImport[]; offline_directory?: string }>('/api/research/minute/imports'), (minuteData) => {
          minuteImports.value = minuteData.items ?? [];
          minuteDirectory.value = minuteData.offline_directory ?? '';
        }),
      ];
      const researchFailures = unpack(primaryReads, await Promise.allSettled(primaryReads.map((item) => item.promise)));
      // Published only after the research-run ledger below, as before.
      let dailyStrategySummaryData: { summary?: DailyStrategySummary | null } = {};
      const secondaryReads = [
        read(getJson<{ items?: IntradayOutcome[]; summary?: IntradayOutcomeSummary[]; attribution_summary?: IntradayAttributionSummary[]; attribution_validation_gate?: AttributionValidationGate }>('/api/research/intraday/outcomes/latest?limit=100'), (outcomeData) => {
          intradayOutcomes.value = outcomeData.items ?? [];
          intradayOutcomeSummary.value = outcomeData.summary ?? [];
          intradayAttributionSummary.value = outcomeData.attribution_summary ?? [];
          attributionValidationGate.value = outcomeData.attribution_validation_gate ?? attributionValidationGate.value;
        }),
        read(getJson<{ items?: AnalystScorecard[]; readiness?: AnalystReadiness[] }>('/api/research/analyst-scorecards'), (scorecardData) => {
          analystScorecards.value = scorecardData.items ?? [];
          analystReadiness.value = scorecardData.readiness ?? [];
        }),
        read(getJson<{ items?: RemoteMessage[] }>('/api/research/remote-archive/messages?limit=60'), (messageData) => { remoteMessages.value = messageData.items ?? []; }),
        read(getJson<{ items?: AnalystSkillProfile[] }>('/api/research/analyst-skills?limit=20'), (skillData) => { analystSkills.value = skillData.items ?? []; }),
        read(getJson<AnalystResearchStatus>('/api/research/analyst-research/status'), (analystResearchData) => { analystResearchStatus.value = analystResearchData; }),
        read(getJson<PaperStatus>('/api/research/paper/status?limit=20'), (paperData) => { paperStatus.value = paperData; }),
        read(getJson<StrategyFunnel>('/api/research/strategy/funnel?limit=30'), (funnelData) => { strategyFunnel.value = funnelData; }),
        read(getJson<{ items?: AnalystObservation[] }>('/api/research/analyst-research/observations?limit=80'), (observationData) => { analystObservations.value = observationData.items ?? []; }),
        read(getJson<StrategyGovernance>('/api/research/strategy/governance'), (governanceData) => { strategyGovernance.value = governanceData; }),
        read(getJson<typeof analystSyncHealth.value>('/api/research/analyst-research/sync-health'), (syncHealthData) => { analystSyncHealth.value = syncHealthData; }),
        read(getJson<AnalystMarketEvaluation>('/api/research/analyst-research/market-evaluation'), (evaluationData) => { analystMarketEvaluation.value = evaluationData; }),
        read(getJson<{ review?: AnalystMarketReview | null }>('/api/research/analyst-research/reviews/latest?cadence=daily'), (dailyReviewData) => { analystDailyReview.value = dailyReviewData.review ?? null; }),
        read(getJson<{ review?: AnalystMarketReview | null }>('/api/research/analyst-research/reviews/latest?cadence=weekly'), (weeklyReviewData) => { analystWeeklyReview.value = weeklyReviewData.review ?? null; }),
        read(getJson<{ items?: AutomationRun[] }>('/api/research/automation/runs?task_key=analyst_market_review&limit=5'), (analystReviewRunData) => { analystReviewRuns.value = analystReviewRunData.items ?? []; }),
        read(getJson<{ items?: AutomationRun[] }>('/api/research/automation/runs?limit=30'), (automationRunData) => { automationRuns.value = automationRunData.items ?? []; }),
        read(getJson<AnalystPromptLab>('/api/research/analyst-prompt-lab/status?limit=30'), (promptLabData) => { analystPromptLab.value = promptLabData; }),
        read(getJson<StrategyAblation>('/api/research/strategy/ablation/latest?limit=30'), (ablationData) => { strategyAblation.value = ablationData; }),
        read(getJson<StrategyHealth>('/api/research/strategy/health'), (strategyHealthData) => { strategyHealth.value = strategyHealthData; }),
        read(getJson<{ summary?: DailyStrategySummary | null }>('/api/research/strategy/daily-summary/latest').catch(() => ({ summary: null })), (data) => { dailyStrategySummaryData = data; }),
      ];
      const secondaryFailures = unpack(secondaryReads, await Promise.allSettled(secondaryReads.map((item) => item.promise)));
      researchRuns.value = (await getJson<{ items?: ResearchRun[] }>('/api/research/research-runs?limit=30').catch(() => ({ items: [] }))).items ?? [];
      dailyStrategySummary.value = dailyStrategySummaryData.summary ?? null;
      if (!universeText.value) universeText.value = universe.value.filter((item) => item.enabled).map((item) => item.symbol).join(', ');
      if (!sectorFlowDate.value) sectorFlowDate.value = sectorFlows.value[0]?.trading_date ?? overview.value.latest_market_snapshot?.exchange_date ?? '';
      if (!selectedFactors.value.length) selectedFactors.value = factors.value.filter((item) => item.implementation === 'native_sql').map((item) => item.factor_key);
      researchLoaded.value = true;
      if (researchFailures || secondaryFailures) {
        const totalFailures = researchFailures + secondaryFailures;
        researchError.value = `部分研究接口暂时不可用（${totalFailures} 项）；已保留可读取数据，稍后自动重试`;
      }
    } catch (error) {
      if (!controller.signal.aborted) researchError.value = '研究数据暂时读取不完整，已保留可用结果，稍后自动重试';
    } finally {
      endResearchLoad(controller);
      loading.value = false;
    }
  }

  return { loadResearch };
}
