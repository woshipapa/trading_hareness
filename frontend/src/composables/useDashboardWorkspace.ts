import { onBeforeUnmount, onMounted, provide, proxyRefs, watch } from 'vue';
import { use } from 'echarts/core';
import { BarChart, CandlestickChart, LineChart, ScatterChart } from 'echarts/charts';
import { DataZoomComponent, GridComponent, LegendComponent, MarkAreaComponent, MarkLineComponent, MarkPointComponent, TooltipComponent } from 'echarts/components';
import { CanvasRenderer } from 'echarts/renderers';
import { dashboardContextKey } from '../dashboard-context';
import { useAnalystEvidenceSlice } from './workspace/analyst-evidence';
import { useAnalystTimelineSlice } from './workspace/analyst-timeline';
import { useBoardFlowSlice } from './workspace/board-flow';
import { useCloseReviewSlice } from './workspace/close-review';
import { useFactorLabSlice } from './workspace/factor-lab';
import { formatters } from './workspace/format';
import { useMarketDataSlice } from './workspace/market-data';
import { useOutcomesSlice } from './workspace/outcomes';
import { useOverviewSlice } from './workspace/overview';
import { useProvidersSlice } from './workspace/providers';
import { useResearchLoader } from './workspace/research-loader';
import { useResearchShell } from './workspace/shell';
import { useStockStudySlice } from './workspace/stock-study';
import { useStrategyGovernanceSlice } from './workspace/strategy-governance';
import { useStrategyPoolSlice } from './workspace/strategy-pool';

/** Names defined by more than one of `Slices`. */
type SharedKeys<Slices extends readonly object[], Seen extends PropertyKey = never> =
  Slices extends readonly [infer Head extends object, ...infer Rest extends readonly object[]]
    ? Extract<keyof Head, Seen> | SharedKeys<Rest, Seen | keyof Head>
    : never;
/** Fails to compile, naming the binding, when two slices define the same name. */
type AssertNoSharedKeys<Keys extends never> = Keys;

/**
 * The research console's workspace, shared by `App.vue` and every research tab.
 *
 * Each research area keeps its state, loaders and actions in a slice under
 * `./workspace/`.  This composable builds the slices, owns the lifecycle
 * (section loading, polling and cancellation) and provides the merged bindings
 * to the lazily mounted tabs through `dashboardContextKey`.  The merged object
 * has the same names and kinds of values as the former single-file
 * composable; `useDashboardWorkspace.test.ts` pins that contract.
 */
export function useDashboardWorkspace() {
  use([BarChart, CandlestickChart, LineChart, ScatterChart, DataZoomComponent, GridComponent, LegendComponent, MarkAreaComponent, MarkLineComponent, MarkPointComponent, TooltipComponent, CanvasRenderer]);

  const shell = useResearchShell(() => loadResearch());
  const overview = useOverviewSlice(shell);
  const marketData = useMarketDataSlice(shell);
  const closeReview = useCloseReviewSlice(shell, marketData);
  const outcomes = useOutcomesSlice(shell, marketData);
  const boardFlow = useBoardFlowSlice(shell);
  const governance = useStrategyGovernanceSlice();
  const stockStudy = useStockStudySlice(shell);
  const analystEvidence = useAnalystEvidenceSlice(shell);
  const analystTimeline = useAnalystTimelineSlice(shell, stockStudy, outcomes);
  const strategyPool = useStrategyPoolSlice(shell, { overview, closeReview, outcomes, governance, stockStudy, analystTimeline });
  const factorLab = useFactorLabSlice(shell);
  const providers = useProvidersSlice(shell);
  const { loadResearch } = useResearchLoader(shell, { overview, marketData, closeReview, outcomes, strategyPool, factorLab, analystEvidence, governance, providers });

  // Lifecycle: load the active section, poll research while it is shown, cancel on leave.
  const { transport, ...shellBindings } = shell;
  const { activeSection, loading, mobileMediaQuery, polling, syncMobileLayout } = shell;
  const { researchLoaded, abortResearchLoad } = transport;
  const { loadRealtimeServices } = providers;
  const { boardFlowIsExchangeToday, loadBoardFlowCurves, loadMarketFlowFeatures, loadBoardRotationEvents, loadBoardStockMining, loadLimitLinkageMining } = boardFlow;
  let sectionLoadTimer: number | null = null;

  function loadActiveSection() {
    if (activeSection.value === 'research') {
      if (!researchLoaded.value && !loading.value) void loadResearch();
      void loadRealtimeServices();
      void loadBoardFlowCurves(true); void loadMarketFlowFeatures(); void loadBoardRotationEvents(); void loadBoardStockMining(); void loadLimitLinkageMining();
    }
  }
  function scheduleActiveSectionLoad() {
    if (sectionLoadTimer !== null) window.clearTimeout(sectionLoadTimer);
    // Research hydrates dozens of independent panels.  A short debounce lets the
    // shell remain navigable when the user is moving between top-level sections.
    const delay = activeSection.value === 'research' ? 500 : 0;
    sectionLoadTimer = window.setTimeout(() => {
      sectionLoadTimer = null;
      loadActiveSection();
    }, delay);
  }
  onMounted(() => {
    mobileMediaQuery.addEventListener('change', syncMobileLayout); scheduleActiveSectionLoad();
    polling.every(15_000, () => { if (activeSection.value === 'research') void loadRealtimeServices(); });
    polling.every(60_000, () => {
      if (activeSection.value === 'research' && document.visibilityState === 'visible' && boardFlowIsExchangeToday.value) { void loadBoardFlowCurves(false); void loadMarketFlowFeatures(); void loadBoardRotationEvents(); void loadBoardStockMining(); void loadLimitLinkageMining(); }
    });
  });
  watch(activeSection, (section) => {
    localStorage.setItem('dashboard-active-section', section);
    if (section !== 'research') abortResearchLoad();
    scheduleActiveSectionLoad();
  });
  onBeforeUnmount(() => {
    mobileMediaQuery.removeEventListener('change', syncMobileLayout);
    if (sectionLoadTimer !== null) window.clearTimeout(sectionLoadTimer);
    abortResearchLoad();
    polling.stop();
  });

  const dashboardBindings = {
    ...shellBindings, ...formatters, ...overview, ...marketData, ...closeReview, ...outcomes, ...boardFlow, ...strategyPool,
    ...governance, ...factorLab, ...stockStudy, ...analystEvidence, ...analystTimeline, ...providers, loadResearch,
  };
  // Keep this list in step with the spread above.
  type BindingsAreDisjoint = AssertNoSharedKeys<SharedKeys<[
    typeof shellBindings, typeof formatters, typeof overview, typeof marketData, typeof closeReview, typeof outcomes,
    typeof boardFlow, typeof strategyPool, typeof governance, typeof factorLab, typeof stockStudy, typeof analystEvidence,
    typeof analystTimeline, typeof providers, { loadResearch: typeof loadResearch },
  ]>>;
  provide(dashboardContextKey, dashboardBindings);
  return proxyRefs(dashboardBindings);
}
