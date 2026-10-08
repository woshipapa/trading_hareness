import { computed, ref } from 'vue';
import { ElMessage } from 'element-plus';
import { postJson } from '../../api/http';
import type { StockStudy } from './types';
import type { ResearchShell } from './shell';

/**
 * 个股研究: the on-demand stock study and its AkShare supplement probes.  A
 * shared `?tab=stock-study&symbol=` link seeds the symbol; other tabs open a
 * candidate here through `studyConceptCandidate`.
 */
export function useStockStudySlice(shell: ResearchShell) {
  const { sharedResearchSymbol, activeResearchTab, runAction } = shell;
  const { loadResearch } = shell.transport;

  const studySymbol = ref(/^\d{6}\.(SH|SZ|BJ)$/.test(sharedResearchSymbol) ? sharedResearchSymbol : '000636.SZ');
  const studyLookback = ref(21);
  const stockStudy = ref<StockStudy | null>(null);
  const studyLoading = ref(false);
  const studyError = ref('');

  const studyStance = (value?: string) => value === 'research_positive' ? '研究偏正面' : value === 'research_negative' ? '研究偏负面' : '证据混合或不足';
  const studyType = (value?: string) => value === 'research_positive' ? 'success' : value === 'research_negative' ? 'danger' : 'info';
  const sourceType = (value?: string) => value === 'completed' || value === 'unchanged' ? 'success' : value === 'partial' ? 'warning' : value === 'failed' ? 'danger' : 'info';
  const studyBars = computed<Record<string, unknown>[]>(() => {
    const bars = stockStudy.value?.market.daily_bars;
    return Array.isArray(bars) ? bars : [];
  });
  const studyMarketRecord = (name: string): Record<string, unknown> => {
    const value = stockStudy.value?.market[name];
    return value && !Array.isArray(value) ? value : {};
  };

  async function runStockStudy() {
    const symbol = studySymbol.value.trim().toUpperCase();
    if (!/^\d{6}\.(SH|SZ|BJ)$/.test(symbol)) { ElMessage.error('代码格式应为 000636.SZ'); return; }
    studyLoading.value = true; studyError.value = ''; stockStudy.value = null;
    try {
      stockStudy.value = await postJson<StockStudy>(`/api/research/stocks/${symbol}/study`, { lookback_days: studyLookback.value });
      studySymbol.value = symbol; ElMessage.success(`${symbol} 的研究证据已刷新`); await loadResearch();
    } catch (error) { studyError.value = error instanceof Error ? error.message : String(error); } finally { studyLoading.value = false; }
  }
  async function probeAkshareSupplement() {
    const symbol = studySymbol.value.trim().toUpperCase();
    if (!/^\d{6}\.(SH|SZ|BJ)$/.test(symbol)) { ElMessage.error('代码格式应为 000636.SZ'); return; }
    await runAction('AkShare补充探测', '/api/research/providers/akshare/probe', {
      symbol, lookback_days: studyLookback.value, include_supplements: true, include_macro_cross_asset: false, board_limit: 3,
    }, false);
  }
  async function probeAkshareMacroSupplement() {
    const symbol = studySymbol.value.trim().toUpperCase();
    if (!/^\d{6}\.(SH|SZ|BJ)$/.test(symbol)) { ElMessage.error('代码格式应为 000636.SZ'); return; }
    await runAction('AkShare宏观跨资产补充', '/api/research/providers/akshare/probe', {
      symbol, lookback_days: studyLookback.value, include_supplements: true, include_macro_cross_asset: true,
      include_board_taxonomy: false, include_moneyflow: false, include_limit_pools: false, include_lhb_supplements: false,
      include_block_trades: false, include_corporate_risk: false, include_analyst_heat: false, include_index_fund: false, board_limit: 0,
    }, false);
  }
  async function studyConceptCandidate(symbol: string) { studySymbol.value = symbol; activeResearchTab.value = 'stock-study'; await runStockStudy(); }

  return {
    studySymbol, studyLookback, stockStudy, studyLoading, studyError,
    studyStance, studyType, sourceType, studyBars, studyMarketRecord,
    runStockStudy, probeAkshareSupplement, probeAkshareMacroSupplement, studyConceptCandidate,
  };
}

export type StockStudySlice = ReturnType<typeof useStockStudySlice>;
