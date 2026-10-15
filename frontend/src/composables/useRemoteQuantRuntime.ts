import { computed, onMounted, onUnmounted, ref } from 'vue';
import { getJson } from '../api/http';
import { usePolling } from './usePolling';
import { normalizeSymbol } from '../research/remote-runtime';

export type RemoteRuntimeHealth = Record<string, any>;
export type RemoteService = { key?: string; label?: string; state?: string; last_error?: string | null; details?: Record<string, any>; [key: string]: any };
export type RemoteServices = { items?: RemoteService[]; [key: string]: any };
export type RemoteWatchlist = { items?: Record<string, any>[]; notice?: string; [key: string]: any };
export type RemoteScan = { scan?: Record<string, any> | null; signals?: Record<string, any>[]; deliveries?: Record<string, any>[]; history?: Record<string, any>[]; [key: string]: any };
export type RemoteStrategyDecision = { run?: Record<string, any> | null; recommendations?: Record<string, any>[]; [key: string]: any };
export type RemotePostClose = { latest_completed?: Record<string, any> | null; candidates?: Record<string, any>[]; notice?: string; [key: string]: any };

const HOT_REFRESH_INTERVAL_MS = 15_000;
const SLOW_REFRESH_INTERVAL_MS = 60_000;

export function useRemoteQuantRuntime() {
  const runtimeHealth = ref<RemoteRuntimeHealth>({});
  const services = ref<RemoteServices>({ items: [] });
  const watchlist = ref<RemoteWatchlist>({ items: [] });
  const latestScan = ref<RemoteScan>({ signals: [], deliveries: [], history: [] });
  const strategyHealth = ref<Record<string, any>>({});
  const strategyDecision = ref<RemoteStrategyDecision>({ recommendations: [] });
  const closeReview = ref<Record<string, any>>({});
  const postClose = ref<RemotePostClose>({ candidates: [] });
  const dailySummary = ref<Record<string, any>>({});
  const promotion = ref<Record<string, any>>({ strategies: [] });
  const watchlistProposals = ref<Record<string, any>>({ proposals: [] });
  const selectedSymbol = ref('');
  const decisionCard = ref<Record<string, any> | null>(null);
  const loading = ref(false);
  const decisionCardLoading = ref(false);
  const error = ref('');
  const decisionCardError = ref('');
  const lastAttemptAt = ref<string | null>(null);
  const lastSuccessAt = ref<string | null>(null);
  const polling = usePolling();
  let hotInFlight = false;
  let slowInFlight = false;
  let loadingCount = 0;
  const tierFailures: Record<'hot' | 'slow', string[]> = { hot: [], slow: [] };

  const signals = computed(() => latestScan.value.signals ?? []);
  const watchlistItems = computed(() => watchlist.value.items ?? []);

  function beginLoad() {
    loadingCount += 1;
    loading.value = true;
  }

  function endLoad() {
    loadingCount = Math.max(0, loadingCount - 1);
    loading.value = loadingCount > 0;
  }

  function refreshErrorMessage() {
    const failures = [...tierFailures.hot, ...tierFailures.slow];
    error.value = failures.length ? `远端只读接口暂时不可用：${failures.join('、')}` : '';
  }

  async function loadRuntimeTier(tier: 'hot' | 'slow') {
    const inFlight = tier === 'hot' ? hotInFlight : slowInFlight;
    if (inFlight) return;
    if (tier === 'hot') hotInFlight = true;
    else slowInFlight = true;
    beginLoad();
    if (tier === 'hot') lastAttemptAt.value = new Date().toISOString();
    const requests = tier === 'hot' ? [
      { label: '运行状态', promise: getJson<RemoteRuntimeHealth>('/api/research/runtime/health'), set: (value: any) => { runtimeHealth.value = value; }, empty: {} },
      { label: '服务状态', promise: getJson<RemoteServices>('/api/research/intraday/services/status'), set: (value: any) => { services.value = value; }, empty: { items: [] } },
      { label: '观察池', promise: getJson<RemoteWatchlist>('/api/research/intraday/watchlists'), set: (value: any) => { watchlist.value = value; }, empty: { items: [] } },
      { label: '盘中扫描', promise: getJson<RemoteScan>('/api/research/intraday/scans/latest?limit=100'), set: (value: any) => { latestScan.value = value; }, empty: { signals: [], deliveries: [], history: [] } },
      { label: '策略健康', promise: getJson<Record<string, any>>('/api/research/strategy/health'), set: (value: any) => { strategyHealth.value = value; }, empty: {} },
    ] : [
      { label: '策略决策', promise: getJson<RemoteStrategyDecision>('/api/research/strategy/decisions/latest'), set: (value: any) => { strategyDecision.value = value; }, empty: { recommendations: [] } },
      { label: '收盘复盘', promise: getJson<Record<string, any>>('/api/research/strategy/reviews/latest?session=close'), set: (value: any) => { closeReview.value = value; }, empty: {} },
      { label: '盘后策略', promise: getJson<RemotePostClose>('/api/research/strategy/post-close/latest'), set: (value: any) => { postClose.value = value; }, empty: { candidates: [] } },
      { label: '日终摘要', promise: getJson<Record<string, any>>('/api/research/strategy/daily-summary/latest'), set: (value: any) => { dailySummary.value = value; }, empty: {} },
      { label: '晋级门禁', promise: getJson<Record<string, any>>('/api/research/strategy/promotion'), set: (value: any) => { promotion.value = value; }, empty: { strategies: [] } },
      { label: '候选提案', promise: getJson<Record<string, any>>('/api/research/strategy/watchlist-proposals'), set: (value: any) => { watchlistProposals.value = value; }, empty: { proposals: [] } },
    ];
    try {
      const results = await Promise.allSettled(requests.map((request) => request.promise));
      let succeeded = 0;
      const failures: string[] = [];
      results.forEach((result, index) => {
        const request = requests[index];
        if (result.status === 'fulfilled') { succeeded += 1; request.set(result.value); }
        else { request.set(request.empty); failures.push(request.label); }
      });
      tierFailures[tier] = failures;
      if (succeeded) lastSuccessAt.value = new Date().toISOString();
      refreshErrorMessage();
    } finally {
      if (tier === 'hot') hotInFlight = false;
      else slowInFlight = false;
      endLoad();
    }
  }

  async function loadRemoteRuntimeCore() {
    await Promise.all([loadRuntimeTier('hot'), loadRuntimeTier('slow')]);
  }

  async function loadDecisionCard(symbol: string) {
    const normalized = normalizeSymbol(symbol);
    if (!normalized) { decisionCardError.value = '请输入交易所格式代码，例如 600000.SH'; return; }
    selectedSymbol.value = normalized;
    decisionCardLoading.value = true;
    decisionCardError.value = '';
    try {
      decisionCard.value = await getJson<Record<string, any>>(`/api/research/intraday/decision-cards/${normalized}`);
    } catch (reason) {
      decisionCard.value = null;
      decisionCardError.value = reason instanceof Error ? reason.message : String(reason);
    } finally { decisionCardLoading.value = false; }
  }

  function startPolling() {
    polling.stop();
    if (document.visibilityState === 'visible') {
      polling.every(HOT_REFRESH_INTERVAL_MS, () => { void loadRuntimeTier('hot'); });
      polling.every(SLOW_REFRESH_INTERVAL_MS, () => { void loadRuntimeTier('slow'); });
    }
  }
  function handleVisibility() { if (document.visibilityState === 'visible') startPolling(); else polling.stop(); }

  onMounted(() => {
    document.addEventListener('visibilitychange', handleVisibility);
    startPolling();
  });
  onUnmounted(() => { document.removeEventListener('visibilitychange', handleVisibility); polling.stop(); });

  return {
    runtimeHealth, services, watchlist, watchlistItems, latestScan, signals, strategyHealth, strategyDecision,
    closeReview, postClose, dailySummary, promotion, watchlistProposals, selectedSymbol, decisionCard,
    loading, decisionCardLoading, error, decisionCardError, lastAttemptAt, lastSuccessAt, loadRemoteRuntimeCore, loadDecisionCard,
  };
}
