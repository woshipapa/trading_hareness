import { computed, ref } from 'vue';
import { ElMessage } from 'element-plus';
import type { AnalystMarketReview, AutomationRun } from '../../api/analyst-contract';
import { postJson } from '../../api/http';
import type {
  AnalystClaim, AnalystMarketEvaluation, AnalystObservation, AnalystPromptLab, AnalystResearchStatus, AnalystSkillProfile,
  AnalystSyncHealth, ClaimReview, RemoteMessage, RemoteReport,
} from './types';
import type { ResearchShell } from './shell';

/**
 * 分析师证据 and 观点复核: the remote report/message archive, structured
 * claims and observations, skill cards, sync health, the prompt lab, the
 * analyst x market evaluation, automated daily/weekly reviews, the automation
 * ledger and the pending claim-to-symbol mappings.
 */
export function useAnalystEvidenceSlice(shell: ResearchShell) {
  const { runAction } = shell;

  const reports = ref<RemoteReport[]>([]);
  const remoteMessages = ref<RemoteMessage[]>([]);
  const analystSkills = ref<AnalystSkillProfile[]>([]);
  const analystResearchStatus = ref<AnalystResearchStatus>({});
  const claims = ref<AnalystClaim[]>([]);
  const claimReviews = ref<ClaimReview[]>([]);
  const analystObservations = ref<AnalystObservation[]>([]);
  const analystSyncHealth = ref<AnalystSyncHealth>({});
  const analystPromptLab = ref<AnalystPromptLab>({});
  const analystMarketEvaluation = ref<AnalystMarketEvaluation>({});
  const analystDailyReview = ref<AnalystMarketReview | null>(null);
  const analystWeeklyReview = ref<AnalystMarketReview | null>(null);
  const analystReviewRunning = ref('');
  const analystReviewRuns = ref<AutomationRun[]>([]);
  const automationRuns = ref<AutomationRun[]>([]);
  const reviewSymbol = ref<Record<string, string>>({});

  const analystReviewChartOption = computed(() => {
    const points = analystWeeklyReview.value?.summary?.daily_points ?? analystDailyReview.value?.summary?.daily_points ?? [];
    return { animation: false, tooltip: { trigger: 'axis' }, legend: { top: 0 }, grid: { left: 52, right: 18, top: 32, bottom: 42 }, xAxis: { type: 'category', data: points.map((item) => item.exchange_date) }, yAxis: [{ type: 'value', name: '观点净方向' }, { type: 'value', name: '市场涨跌%', axisLabel: { formatter: '{value}%' } }], series: [{ name: '观点净方向', type: 'bar', data: points.map((item) => item.net_direction_score ?? 0), itemStyle: { color: '#7e57c2' } }, { name: '市场均涨跌%', type: 'line', yAxisIndex: 1, smooth: true, data: points.map((item) => item.market_mean_change_pct ?? null), lineStyle: { color: '#00897b', width: 2 } }] };
  });

  async function runAnalystMarketReview(cadence: 'daily' | 'weekly') {
    analystReviewRunning.value = cadence;
    try {
      const result = await postJson<{ review?: AnalystMarketReview }>('/api/research/analyst-research/reviews/run', { cadence });
      if (cadence === 'daily') analystDailyReview.value = result.review ?? null; else analystWeeklyReview.value = result.review ?? null;
      ElMessage.success(`${cadence === 'daily' ? '日报' : '周报'}已生成`);
    } catch (error) { ElMessage.error(`分析师复盘失败：${error instanceof Error ? error.message : String(error)}`); }
    finally { analystReviewRunning.value = ''; }
  }
  async function decideReview(item: ClaimReview, status: 'approved' | 'rejected') {
    if (status === 'approved' && !/^\d{6}\.(SH|SZ|BJ)$/.test((reviewSymbol.value[item.review_id] || item.suggested_symbol || '').toUpperCase())) { ElMessage.error('批准前请填写有效股票代码'); return; }
    await runAction(status === 'approved' ? '批准分析师标的映射' : '拒绝分析师标的映射', `/api/research/claim-review/${item.review_id}`, { status, symbol: (reviewSymbol.value[item.review_id] || item.suggested_symbol || '').toUpperCase() });
  }

  return {
    reports, remoteMessages, analystSkills, analystResearchStatus, claims, analystObservations,
    analystSyncHealth, analystPromptLab, analystMarketEvaluation,
    analystDailyReview, analystWeeklyReview, analystReviewRunning, analystReviewRuns, automationRuns,
    claimReviews, reviewSymbol,
    analystReviewChartOption, runAnalystMarketReview, decideReview,
  };
}

export type AnalystEvidenceSlice = ReturnType<typeof useAnalystEvidenceSlice>;
