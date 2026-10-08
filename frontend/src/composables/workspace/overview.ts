import { computed, ref } from 'vue';
import { ElMessage, ElMessageBox } from 'element-plus';
import { postJson } from '../../api/http';
import type { DailyStrategySummary, PostCloseRefresh, ReplayReadiness, ResearchOverview, ResearchRun } from './types';
import type { ResearchShell } from './shell';

/**
 * 研究概览: research counts and data coverage, live versus replay readiness,
 * the reproducible run ledger, the daily learning summary and the post-close
 * one-click refresh.
 */
export function useOverviewSlice(shell: ResearchShell) {
  const { actionLoading, runAction } = shell;
  const { loadResearch } = shell.transport;

  const overview = ref<ResearchOverview>({});
  const replayReadiness = ref<ReplayReadiness>({});
  const researchRuns = ref<ResearchRun[]>([]);
  const dailyStrategySummary = ref<DailyStrategySummary | null>(null);
  const postCloseRefresh = ref<PostCloseRefresh | null>(null);

  const count = (name: string) => overview.value.counts?.[name] ?? 0;
  const readinessType = (value: number) => value > 0 ? 'warning' : 'success';
  const historyDatasetRows = computed(() => overview.value.history_estimate?.datasets.slice(0, 8) ?? []);
  const featureReadinessRows = computed(() => overview.value.feature_readiness?.items ?? []);

  async function runPostCloseRefresh() {
    try {
      await ElMessageBox.confirm('确认执行盘后一键更新？', '研究操作', { type: 'warning', confirmButtonText: '执行', cancelButtonText: '取消' });
      actionLoading.value = '盘后一键更新';
      postCloseRefresh.value = await postJson<PostCloseRefresh>(
        '/api/research/market/post-close/refresh', { include_macro_cross_asset: true, include_announcements: true },
      );
      ElMessage.success(`盘后一键更新：${postCloseRefresh.value.status ?? '已完成'}`);
      await loadResearch();
    } catch (error) {
      const message = error instanceof Error ? error.message : String(error);
      if (message.includes('already running')) {
        postCloseRefresh.value = { status: 'running', retry_hint: '已有盘后更新在运行；请等待其完成后刷新页面。' };
        ElMessage.info('已有盘后更新在运行中，本次未重复提交。');
        return;
      }
      if (error !== 'cancel') ElMessage.error(`盘后一键更新失败：${message}`);
    } finally {
      actionLoading.value = '';
    }
  }
  async function reconcileStaleFetchRuns() { await runAction('修复陈旧运行任务', '/api/research/operations/fetch-runs/reconcile-stale', { max_age_minutes: 90, terminal_status: 'failed' }, true); }

  return {
    overview, replayReadiness, researchRuns, dailyStrategySummary, postCloseRefresh,
    count, readinessType, historyDatasetRows, featureReadinessRows,
    runPostCloseRefresh, reconcileStaleFetchRuns,
  };
}

export type OverviewSlice = ReturnType<typeof useOverviewSlice>;
