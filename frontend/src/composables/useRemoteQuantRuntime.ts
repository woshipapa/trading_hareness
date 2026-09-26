import { computed, onMounted, onUnmounted, ref } from 'vue';
import { getJson } from '../api/http';
import { usePolling } from './usePolling';
import { normalizeSymbol } from '../research/remote-runtime';

export type RemoteRuntimeHealth = Record<string, any>;
export type RemoteService = { key?: string; label?: string; state?: string; last_error?: string | null; details?: Record<string, any>; [key: string]: any };
export type RemoteServices = { items?: RemoteService[]; [key: string]: any };
export type RemoteWatchlist = { items?: Record<string, any>[]; notice?: string; [key: string]: any };
export type RemoteScan = { scan?: Record<string, any> | null; signals?: Record<string, any>[]; deliveries?: Record<string, any>[]; [key: string]: any };
export type RemoteStrategyDecision = { run?: Record<string, any> | null; recommendations?: Record<string, any>[]; [key: string]: any };
export type RemotePostClose = { latest_completed?: Record<string, any> | null; candidates?: Record<string, any>[]; notice?: string; [key: string]: any };

export function useRemoteQuantRuntime() {
  const runtimeHealth = ref<RemoteRuntimeHealth>({});
  const services = ref<RemoteServices>({ items: [] });
  const watchlist = ref<RemoteWatchlist>({ items: [] });
  const latestScan = ref<RemoteScan>({ signals: [], deliveries: [] });
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
  const lastSuccessAt = ref<string | null>(null);
  const polling = usePolling();
  let coreInFlight = false;

  const signals = computed(() => latestScan.value.signals ?? []);
  const watchlistItems = computed(() => watchlist.value.items ?? []);

  async function loadRemoteRuntimeCore() {
    if (coreInFlight) return;
    coreInFlight = true;
    loading.value = true;
    error.value = '';
    const requests = [
      { label: '运行状态', promise: getJson<RemoteRuntimeHealth>('/api/research/runtime/health') },
      { label: '服务状态', promise: getJson<RemoteServices>('/api/research/intraday/services/status') },
      { label: '观察池', promise: getJson<RemoteWatchlist>('/api/research/intraday/watchlists') },
      { label: '盘中扫描', promise: getJson<RemoteScan>('/api/research/intraday/scans/latest?limit=100') },
      { label: '策略健康', promise: getJson<Record<string, any>>('/api/research/strategy/health') },
      { label: '策略决策', promise: getJson<RemoteStrategyDecision>('/api/research/strategy/decisions/latest') },
      { label: '收盘复盘', promise: getJson<Record<string, any>>('/api/research/strategy/reviews/latest?session=close') },
      { label: '盘后策略', promise: getJson<RemotePostClose>('/api/research/strategy/post-close/latest') },
      { label: '日终摘要', promise: getJson<Record<string, any>>('/api/research/strategy/daily-summary/latest') },
      { label: '晋级门禁', promise: getJson<Record<string, any>>('/api/research/strategy/promotion') },
      { label: '候选提案', promise: getJson<Record<string, any>>('/api/research/strategy/watchlist-proposals') },
    ];
    const results = await Promise.allSettled(requests.map((request) => request.promise));
    const setters = [
      (value: any) => { runtimeHealth.value = value; },
      (value: any) => { services.value = value; },
      (value: any) => { watchlist.value = value; },
      (value: any) => { latestScan.value = value; },
      (value: any) => { strategyHealth.value = value; },
      (value: any) => { strategyDecision.value = value; },
      (value: any) => { closeReview.value = value; },
      (value: any) => { postClose.value = value; },
      (value: any) => { dailySummary.value = value; },
      (value: any) => { promotion.value = value; },
      (value: any) => { watchlistProposals.value = value; },
    ];
    let succeeded = 0;
    const failures: string[] = [];
    results.forEach((result, index) => {
      if (result.status === 'fulfilled') { succeeded += 1; setters[index](result.value); }
      else failures.push(requests[index].label);
    });
    if (succeeded) lastSuccessAt.value = new Date().toISOString();
    if (failures.length) error.value = succeeded
      ? `远端只读接口暂时不可用：${failures.join('、')}`
      : '远端量化服务暂时无法读取，请稍后重试';
    loading.value = false;
    coreInFlight = false;
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
    if (document.visibilityState === 'visible') polling.every(15_000, () => { void loadRemoteRuntimeCore(); });
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
    loading, decisionCardLoading, error, decisionCardError, lastSuccessAt, loadRemoteRuntimeCore, loadDecisionCard,
  };
}
