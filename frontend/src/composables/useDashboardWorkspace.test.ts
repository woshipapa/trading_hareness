import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest';
import { defineComponent, h, inject, isReadonly, isRef, unref } from 'vue';
import { flushPromises, mount, type VueWrapper } from '@vue/test-utils';

import { dashboardContextKey } from '../dashboard-context';
import { useDashboardWorkspace } from './useDashboardWorkspace';

// The bindings the research tabs inject, by kind.  Captured from the
// single-file composable before it was split into ./workspace/ slices; a
// rename or a lost binding breaks a tab template without a compile error.
const PUBLIC_BINDINGS = {
  ref: [
    'actionLoading', 'activeResearchTab', 'activeSection', 'adapterHealth', 'analystAnnotationAreaEndIndex',
    'analystAnnotationAreaStartIndex', 'analystAnnotationLabel', 'analystAnnotationLower',
    'analystAnnotationPointIndex', 'analystAnnotationPrice', 'analystAnnotationSelectionMode',
    'analystAnnotationUpper', 'analystChartAnnotations', 'analystDailyReview', 'analystMarketEvaluation',
    'analystObservations', 'analystPromptLab', 'analystReadiness', 'analystResearchStatus',
    'analystReviewRunning', 'analystReviewRuns', 'analystScorecards', 'analystSkills', 'analystStockTimeline',
    'analystStockTimelineError', 'analystStockTimelineLoading', 'analystSyncHealth', 'analystTimelineAnalyst',
    'analystTimelineDate', 'analystWeeklyReview', 'announcements', 'attributionValidationGate',
    'automationRuns', 'backtestForm', 'boardFlowCursor', 'boardFlowDate', 'boardFlowDisplaySlots',
    'boardFlowError', 'boardFlowFocus', 'boardFlowIsExchangeToday', 'boardFlowLoading', 'boardFlowNotice',
    'boardFlowSeries', 'boardFlowSnapshots', 'boardFlowTaxonomy', 'boardRotationEvents', 'boardStockMining',
    'claimReviews', 'claims', 'closeBoardReport', 'closeStrategyReview', 'conceptBackfill', 'conceptCandidates',
    'conceptSignals', 'dailyStrategySummary', 'factorEvaluations', 'factorHorizon', 'factors', 'featureItems',
    'frameworks', 'intradayAttributionSummary', 'intradayOutcomeSummary', 'intradayOutcomes', 'lhbEvents',
    'limitLinkageMining', 'loading', 'mainWaveExperiments', 'marketFlow', 'marketFlowError', 'marketSnapshots',
    'minuteDirectory', 'minuteImports', 'mobileLayout', 'overview', 'paperStatus', 'postCloseCandidates',
    'postCloseRefresh', 'postCloseStrategyRun', 'providerApiCapabilities', 'providerHealth', 'qualityIssues',
    'realtimeError', 'realtimeLoading', 'realtimeProviderHealth', 'realtimeServices', 'recommendations',
    'remoteMessages', 'replayReadiness', 'reports', 'researchError', 'researchRuns', 'reviewSymbol',
    'runtimeHealth', 'sectorFlowDate', 'sectorFlows', 'sectorMemberLimit', 'sectorMemberOffset', 'sectors',
    'selectedFactors', 'selectedReviewBoardKey', 'stockStudy', 'strategies', 'strategyAblation',
    'strategyContinuationCandidates', 'strategyDragonLeaderCandidates', 'strategyDragonLeaderMarket',
    'strategyExperiments', 'strategyFunnel', 'strategyGovernance', 'strategyHealth', 'strategyLimitLadder',
    'strategyLimitPool', 'strategyPatternPicks', 'strategyPatternRun', 'strategyPatternSamples',
    'strategyPoolCoverage', 'studyError', 'studyLoading', 'studyLookback', 'studySymbol',
    'tenDayLeaderRotation', 'trainingRoadmap', 'universe', 'universePriority', 'universeText',
  ],
  computed: [
    'analystAnnotationSelectionLabel', 'analystReviewChartOption', 'analystStockDeepLink',
    'analystStockTimelineChartOption', 'boardFlowChartOption', 'boardFlowGaps', 'boardFlowHighlighted',
    'boardFlowLatestSnapshot', 'boardFlowLatestValues', 'boardFlowSeriesRows', 'boardFlowWindowText',
    'closeIndexRegime', 'closeShortTermReview', 'completedBackfillBoards', 'equityChartOption',
    'factorChartOption', 'featureReadinessRows', 'historyDatasetRows', 'indexRegimeLabel', 'indexRegimeType',
    'latestExperiment', 'latestFactorEvaluations', 'latestMainWaveExperiment', 'latestReboundExperiment',
    'mainWaveCurrentScores', 'mainWaveFailedChecks', 'mainWaveQualification', 'marketFlowChartOption',
    'marketFlowLatest', 'marketFlowSectorHighlights', 'reboundCurrentScores', 'reboundFailedChecks',
    'reviewConceptBoards', 'selectedReviewBoard', 'selectedReviewBoardStocks', 'strategyEvidenceMatrix',
    'strategyReviewContext', 'studyBars',
  ],
  function: [
    'addAnalystChartAnnotation', 'advanceConceptBackfill', 'ageText', 'analystReadinessText',
    'attributionCohortLabel', 'attributionDimensionLabel', 'attributionStatusType', 'boardRotationDeliveryText',
    'boardRotationKind', 'boardRotationStateText', 'boardRotationStateType', 'bytesText', 'chinaDateTime',
    'chinaMinute', 'claimDirection', 'clearAnalystChartAnnotations', 'count', 'dateText', 'decideReview',
    'displayValue', 'featureRecord', 'featureStatusType', 'handleAnalystChartClick', 'healthState',
    'indexLabel', 'loadAnalystStockTimeline', 'loadBoardFlowCurves', 'loadBoardRotationEvents',
    'loadBoardStockMining', 'loadLimitLinkageMining', 'loadMarketFlowFeatures', 'loadRealtimeServices',
    'loadResearch', 'marketFlowStateLabel', 'marketFlowStateType', 'metricNumber', 'moneyWan', 'nestedNumber',
    'nestedValue', 'openAnalystStockInTonghuashun', 'openStrategyEvidence', 'outcomePercent',
    'outcomeStatusType', 'paperSectorExposureItems', 'patternCohortLabel', 'postCloseCandidateLabel',
    'postCloseCandidateType', 'probeAkshareMacroSupplement', 'probeAkshareSupplement', 'readinessType',
    'realtimeDeliveryDetail', 'realtimeProviderStateType', 'realtimeStateText', 'realtimeStateType',
    'recommendationDirection', 'recommendationType', 'recomputeAnalystScorecards', 'reconcileStaleFetchRuns',
    'refreshCloseReview', 'resetBoardFlowCurves', 'reviewTierText', 'rowText', 'runAction',
    'runAnalystMarketReview', 'runBoardResearch', 'runFactorEvaluation', 'runMainWaveResearch',
    'runMarketSnapshot', 'runPostCloseRefresh', 'runPostCloseStrategy', 'runStockStudy', 'runStrategyBacktest',
    'runStrategyPatternMining', 'runTenDayLeaderRotation', 'saveUniverse', 'sectorFlowTransitionLabel',
    'sectorFlowTransitionType', 'selectActiveSection', 'settleIntradayOutcomes', 'snapshotType', 'sourceType',
    'storageText', 'strategyEvidenceAlignmentType', 'studyConceptCandidate', 'studyMarketRecord', 'studyStance',
    'studyType', 'syncAllMarketUniverse', 'syncCninfoAnnouncements', 'syncConceptCandidates',
    'syncConceptSignals', 'syncFullMarketDaily', 'syncMobileLayout', 'syncSectorDirectory', 'syncSectorFlows',
    'syncSectorMembers',
  ],
  value: ['initialPath', 'mobileMediaQuery', 'polling', 'sharedResearchParams', 'sharedResearchSymbol', 'sharedResearchTab'],
};

// Every read loadResearch() makes, and the bindings its payload fills.
const RESEARCH_READS: Record<string, string[]> = {
  '/api/research/overview': ['overview'],
  '/api/research/data-readiness/replay': ['replayReadiness'],
  '/api/research/reports?limit=30': ['reports'],
  '/api/research/claims?limit=80': ['claims'],
  '/api/research/providers': ['providerHealth'],
  '/api/research/providers/realtime-health': ['realtimeProviderHealth'],
  '/api/research/provider-capabilities': ['providerApiCapabilities'],
  '/api/research/market/snapshots?limit=20': ['marketSnapshots'],
  '/api/research/market/sectors?taxonomy_key=ths_index_n&limit=500': ['sectors'],
  '/api/research/market/sector-flows?taxonomy_key=ths_industry&limit=100': ['sectorFlows'],
  '/api/research/market/sectors/concepts?limit=100': ['conceptSignals'],
  '/api/research/market/sectors/concepts/candidates?limit=100': ['conceptCandidates'],
  '/api/research/events/announcements?limit=100': ['announcements'],
  '/api/research/events/lhb?limit=100': ['lhbEvents'],
  '/api/research/market/sectors/review/report/latest': ['closeBoardReport'],
  '/api/research/market/sectors/concepts/members/backfill/status': ['conceptBackfill'],
  '/api/research/strategy/reviews/latest?session=close': ['closeStrategyReview'],
  '/api/research/strategy/post-close/latest': ['postCloseStrategyRun', 'postCloseCandidates'],
  '/api/research/strategy/pattern-mining/latest': [
    'strategyPatternRun', 'strategyLimitPool', 'strategyLimitLadder', 'strategyContinuationCandidates',
    'strategyDragonLeaderCandidates', 'strategyDragonLeaderMarket', 'strategyPoolCoverage', 'strategyPatternPicks',
    'strategyPatternSamples',
  ],
  '/api/research/ten-day-leader-rotation/latest?limit=90': ['tenDayLeaderRotation'],
  '/api/research/recommendations': ['recommendations'],
  '/api/research/universes/core': ['universe'],
  '/api/research/features/latest?universe_key=core': ['featureItems'],
  '/api/research/claim-review?status=pending': ['claimReviews'],
  '/api/research/factors': ['factors'],
  '/api/research/factor-evaluations?universe_key=all_a': ['factorEvaluations'],
  '/api/research/strategies': ['strategies'],
  '/api/research/strategy-experiments?universe_key=all_a': ['strategyExperiments'],
  '/api/research/strategy-experiments-watchlist?universe_key=watchlist&limit=10': ['mainWaveExperiments'],
  '/api/research/frameworks': ['frameworks'],
  '/api/research/training/roadmap': ['trainingRoadmap'],
  '/api/research/quality?limit=100': ['qualityIssues'],
  '/api/research/minute/imports': ['minuteImports', 'minuteDirectory'],
  '/api/research/intraday/outcomes/latest?limit=100': ['intradayOutcomes', 'intradayOutcomeSummary', 'intradayAttributionSummary', 'attributionValidationGate'],
  '/api/research/analyst-scorecards': ['analystScorecards', 'analystReadiness'],
  '/api/research/remote-archive/messages?limit=60': ['remoteMessages'],
  '/api/research/analyst-skills?limit=20': ['analystSkills'],
  '/api/research/analyst-research/status': ['analystResearchStatus'],
  '/api/research/paper/status?limit=20': ['paperStatus'],
  '/api/research/strategy/funnel?limit=30': ['strategyFunnel'],
  '/api/research/analyst-research/observations?limit=80': ['analystObservations'],
  '/api/research/strategy/governance': ['strategyGovernance'],
  '/api/research/analyst-research/sync-health': ['analystSyncHealth'],
  '/api/research/analyst-research/market-evaluation': ['analystMarketEvaluation'],
  '/api/research/analyst-research/reviews/latest?cadence=daily': ['analystDailyReview'],
  '/api/research/analyst-research/reviews/latest?cadence=weekly': ['analystWeeklyReview'],
  '/api/research/automation/runs?task_key=analyst_market_review&limit=5': ['analystReviewRuns'],
  '/api/research/automation/runs?limit=30': ['automationRuns'],
  '/api/research/analyst-prompt-lab/status?limit=30': ['analystPromptLab'],
  '/api/research/strategy/ablation/latest?limit=30': ['strategyAblation'],
  '/api/research/strategy/health': ['strategyHealth'],
  '/api/research/strategy/daily-summary/latest': ['dailyStrategySummary'],
  '/api/research/research-runs?limit=30': ['researchRuns'],
};

/** A payload with every field the loader unpacks, each tagged with the path it came from. */
function payloadFor(path: string) {
  const row = { from: path, symbol: '600000.SH', enabled: true, implementation: 'native_sql', factor_key: path, trading_date: '2026-10-07' };
  return {
    from: path, items: [row], summary: [row], readiness: [row], attribution_summary: [row], attribution_validation_gate: row,
    report: row, review: row, run: row, candidates: [row], recommendations: [row], offline_directory: path,
    limit_pool: [row], limit_ladder: [row], continuation_candidates: [row], dragon_leader_candidates: [row],
    dragon_leader_market_context: row, pool_coverage: row, picks: [row], samples: [row],
  };
}

const failing = new Map<string, string>();
const requested: string[] = [];
let context: Record<string, unknown> = {};
let workspace: Record<string, any> = {};
let wrapper: VueWrapper | null = null;

function mountWorkspace() {
  const Tab = defineComponent({ setup() { context = inject(dashboardContextKey) ?? {}; return () => null; } });
  const Shell = defineComponent({ setup() { workspace = useDashboardWorkspace(); return () => h(Tab); } });
  wrapper = mount(Shell);
}

const json = (value: unknown, status = 200) => new Response(JSON.stringify(value), { status, headers: { 'content-type': 'application/json' } });

beforeEach(() => {
  failing.clear();
  requested.length = 0;
  localStorage.clear();
  vi.stubGlobal('matchMedia', (query: string) => ({ matches: false, media: query, addEventListener: () => {}, removeEventListener: () => {} }));
  vi.stubGlobal('fetch', vi.fn(async (input: RequestInfo | URL) => {
    const path = String(input);
    requested.push(path);
    const detail = failing.get(path);
    return detail ? json({ detail }, 503) : json(payloadFor(path));
  }));
  mountWorkspace();
});

afterEach(() => {
  wrapper?.unmount();
  wrapper = null;
  vi.unstubAllGlobals();
});

describe('useDashboardWorkspace', () => {
  it('provides the same public bindings, of the same kinds, as before the split', () => {
    const kinds: Record<keyof typeof PUBLIC_BINDINGS, string[]> = { ref: [], computed: [], function: [], value: [] };
    for (const [name, value] of Object.entries(context)) {
      const kind = typeof value === 'function' ? 'function' : isRef(value) ? (isReadonly(value) ? 'computed' : 'ref') : 'value';
      kinds[kind].push(name);
    }
    for (const names of Object.values(kinds)) names.sort();
    expect(kinds).toEqual(PUBLIC_BINDINGS);
  });

  it('returns the provided bindings to the shell with refs unwrapped', () => {
    expect(Object.keys(workspace).sort()).toEqual(Object.keys(context).sort());
    for (const [name, value] of Object.entries(context)) expect(workspace[name]).toBe(unref(value));
  });

  it('loads every research read into its own bindings', async () => {
    await workspace.loadResearch();

    expect([...requested].sort()).toEqual(Object.keys(RESEARCH_READS).sort());
    for (const [path, bindings] of Object.entries(RESEARCH_READS)) {
      for (const binding of bindings) expect(JSON.stringify(unref(context[binding])), `${binding} <- ${path}`).toContain(JSON.stringify(path));
    }
    expect(workspace.researchError).toBe('');
    expect(workspace.universeText).toBe('600000.SH');
    expect(workspace.sectorFlowDate).toBe('2026-10-07');
    expect(workspace.selectedFactors).toEqual(['/api/research/factors']);
  });

  it('keeps readable panels and counts only unguarded failures in the banner', async () => {
    for (const path of [
      '/api/research/claims?limit=80', '/api/research/factors', '/api/research/analyst-skills?limit=20',
      // These four reads have their own fallbacks and never count as failures.
      '/api/research/providers/realtime-health', '/api/research/ten-day-leader-rotation/latest?limit=90',
      '/api/research/strategy/daily-summary/latest', '/api/research/research-runs?limit=30',
    ]) failing.set(path, 'unavailable');

    await workspace.loadResearch();

    expect(workspace.researchError).toBe('部分研究接口暂时不可用（3 项）；已保留可读取数据，稍后自动重试');
    expect(workspace.claims).toEqual([]);
    expect(workspace.factors).toEqual([]);
    expect(workspace.analystSkills).toEqual([]);
    expect(workspace.realtimeProviderHealth).toEqual({ items: [], evidence_mode: 'unavailable_on_current_release' });
    expect(workspace.tenDayLeaderRotation.scope).toBe('research_only_no_orders');
    expect(workspace.dailyStrategySummary).toBeNull();
    expect(workspace.researchRuns).toEqual([]);
    expect(JSON.stringify(workspace.reports)).toContain('/api/research/reports?limit=30');
    expect(workspace.loading).toBe(false);
  });

  it('reports an overview failure on its own and still loads the other panels', async () => {
    failing.set('/api/research/overview', 'overview offline');

    await workspace.loadResearch();

    expect(workspace.researchError).toBe('研究概览读取失败：overview offline');
    expect(workspace.overview).toEqual({});
    expect(JSON.stringify(workspace.marketSnapshots)).toContain('/api/research/market/snapshots?limit=20');
  });

  it('runs one research load at a time', async () => {
    await Promise.all([workspace.loadResearch(), workspace.loadResearch()]);
    await flushPromises();

    expect(requested.filter((path) => path === '/api/research/overview')).toHaveLength(1);
  });
});
