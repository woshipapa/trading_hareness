import { ref } from 'vue';
import { ElMessage, ElMessageBox } from 'element-plus';
import { getJson as getJsonBase, postJson } from '../../api/http';
import { resolveInitialDashboardSection, type DashboardSection } from '../../dashboard-navigation';
import { usePolling } from '../usePolling';

/**
 * The research console's shell state: section and tab navigation, the shared
 * loading/error flags, the abortable research read and `runAction`, the
 * confirm, POST, toast and reload step that every research mutation goes through.
 *
 * `loadResearch` is the workspace's full reload.  It is passed in rather than
 * imported because the research loader needs the slices built from this shell.
 */
export function useResearchShell(loadResearch: () => Promise<void>) {
  const initialPath = window.location.pathname;
  const mobileMediaQuery = window.matchMedia('(max-width: 760px)');
  const mobileLayout = ref(mobileMediaQuery.matches);
  const syncMobileLayout = (event: MediaQueryListEvent) => { mobileLayout.value = event.matches; };
  const activeSection = ref<DashboardSection>(resolveInitialDashboardSection(initialPath, localStorage.getItem('dashboard-active-section')));
  const sharedResearchParams = new URLSearchParams(window.location.search);
  const sharedResearchSymbol = (sharedResearchParams.get('symbol') || '').toUpperCase();
  const sharedResearchTab = sharedResearchParams.get('tab');
  const activeResearchTab = ref(sharedResearchTab === 'stock-study' && /^\d{6}\.(SH|SZ|BJ)$/.test(sharedResearchSymbol) ? 'stock-study' : 'overview');
  const loading = ref(false); const researchLoaded = ref(false); const actionLoading = ref(''); const researchError = ref('');
  let researchAbortController: AbortController | null = null;
  // Research reads made while a full reload is running share its abort signal,
  // so leaving the research section cancels the whole batch.
  const getJson = <T>(path: string) => getJsonBase<T>(path, {
    signal: loading.value && path.startsWith('/api/research/') ? researchAbortController?.signal : undefined,
  });
  const polling = usePolling();

  /** Cancel the previous reload's reads and hand out the signal for a new one. */
  function beginResearchLoad() {
    researchAbortController?.abort();
    const controller = new AbortController();
    researchAbortController = controller;
    return controller;
  }
  function endResearchLoad(controller: AbortController) {
    if (researchAbortController === controller) researchAbortController = null;
  }
  function abortResearchLoad() {
    researchAbortController?.abort();
  }

  async function runAction(label: string, path: string, body: Record<string, unknown> = {}, confirmation = true) {
    if (confirmation) await ElMessageBox.confirm(`确认执行${label}？`, '研究操作', { type: 'warning', confirmButtonText: '执行', cancelButtonText: '取消' });
    actionLoading.value = label;
    try { const result = await postJson<Record<string, unknown>>(path, body); ElMessage.success(`${label}：${String(result.status ?? '已提交')}`); await loadResearch(); return result; } catch (error) { if (error !== 'cancel') ElMessage.error(`${label}失败：${error instanceof Error ? error.message : String(error)}`); return undefined; } finally { actionLoading.value = ''; }
  }
  function selectActiveSection(value: string) {
    if (!['research', 'personal'].includes(value)) return;
    activeSection.value = value as DashboardSection;
  }

  return {
    initialPath, mobileMediaQuery, mobileLayout, syncMobileLayout,
    activeSection, selectActiveSection,
    sharedResearchParams, sharedResearchSymbol, sharedResearchTab, activeResearchTab,
    loading, actionLoading, researchError, polling, runAction,
    /** Plumbing for the slices and the research loader; it is not part of the tab context. */
    transport: { getJson, loadResearch, researchLoaded, beginResearchLoad, endResearchLoad, abortResearchLoad },
  };
}

export type ResearchShell = ReturnType<typeof useResearchShell>;
