import { ref } from 'vue';
import type {
  AdapterHealth, MinuteImport, ProviderHealth, QualityIssue, RealtimeProviderHealth, RealtimeService, RealtimeServiceState,
  RealtimeServiceStatus, RuntimeHealth,
} from './types';
import { ageText } from './format';
import type { ResearchShell } from './shell';

/**
 * 数据源 Doctor and 质量与分钟数据: provider capability health, realtime
 * provider evidence, the realtime delivery services with adapter and runtime
 * health (polled by the workspace), open quality issues and offline minute
 * imports.
 */
export function useProvidersSlice(shell: ResearchShell) {
  const { getJson } = shell.transport;

  const providerHealth = ref<ProviderHealth[]>([]);
  const qualityIssues = ref<QualityIssue[]>([]);
  const minuteImports = ref<MinuteImport[]>([]);
  const minuteDirectory = ref('');
  const realtimeProviderHealth = ref<RealtimeProviderHealth>({ items: [] });
  const realtimeServices = ref<RealtimeServiceStatus>({ items: [] });
  const adapterHealth = ref<AdapterHealth>({});
  const runtimeHealth = ref<RuntimeHealth>({});
  const realtimeLoading = ref(false);
  const realtimeError = ref('');

  const healthState = (provider: ProviderHealth) => provider.circuit_open_until ? 'danger' : provider.last_error ? 'warning' : provider.last_success_at ? 'success' : 'info';
  const realtimeProviderStateType = (state?: string): 'success' | 'warning' | 'danger' | 'info' => state === 'healthy' ? 'success' : ['partial', 'standby', 'stale'].includes(state ?? '') ? 'warning' : ['degraded', 'unavailable', 'unconfigured', 'disabled', 'circuit_open'].includes(state ?? '') ? 'danger' : 'info';
  const realtimeStateType = (state?: RealtimeServiceState): 'success' | 'warning' | 'danger' | 'info' => state === 'healthy' || state === 'ready' ? 'success' : state === 'starting' || state === 'standby' ? 'warning' : state === 'degraded' || state === 'disabled' ? 'danger' : 'info';
  const realtimeStateText = (state?: RealtimeServiceState) => ({ healthy: '运行正常', ready: '投递就绪', standby: '待命', starting: '启动中', degraded: '降级/延迟', disabled: '未配置', unavailable: '明确不可用' }[state ?? 'disabled']);
  const realtimeDeliveryDetail = (service: RealtimeService) => {
    const details = service.details ?? {};
    if (service.key === 'tencent_realtime' && details.public_flow_snapshot) {
      const snapshot = details.public_flow_snapshot as { status?: string; age_seconds?: number; max_decision_age_seconds?: number; decision_eligible?: boolean };
      return `资金流 ${snapshot.status ?? '未知'}；${snapshot.decision_eligible ? '可用于新入场确认' : '仅展示，禁止资金流确认'}；${ageText(snapshot.age_seconds)} / ${ageText(snapshot.max_decision_age_seconds)}`;
    }
    if (service.key === 'feishu_alert') return `最近 ${details.latest_delivery_kind ?? '无'} / ${details.latest_delivery_status ?? '无'}；待重试 ${details.pending_retry_count ?? 0}；带外关注 ${details.meta_alert_state ?? 'normal'}`;
    if (service.key === 'daily_strategy_summary') return `最近交易日 ${details.latest_exchange_date ?? '尚无'}；投递 ${details.latest_delivery_status ?? '尚无'}；尝试 ${details.attempt_count ?? 0}/3`;
    return '';
  };

  async function loadRealtimeServices() {
    realtimeLoading.value = true; realtimeError.value = '';
    try {
      const [services, adapter, runtime] = await Promise.all([
        getJson<RealtimeServiceStatus>('/api/research/intraday/services/status'),
        getJson<AdapterHealth>('/health'), getJson<typeof runtimeHealth.value>('/api/research/runtime/health'),
      ]);
      realtimeServices.value = services; adapterHealth.value = adapter; runtimeHealth.value = runtime;
      const feishu = realtimeServices.value.items?.find((item) => item.key === 'feishu_alert');
      if (feishu && (adapter.status !== 'ok' || !adapter.quant_alert_configured)) {
        feishu.state = adapter.status === 'ok' ? 'disabled' : 'degraded';
        feishu.last_error = adapter.status === 'ok' ? '飞书提醒目标或内部鉴权未配置' : '飞书适配器健康检查失败';
      }
    } catch (error) {
      realtimeError.value = error instanceof Error ? error.message : String(error);
    } finally { realtimeLoading.value = false; }
  }

  return {
    providerHealth, realtimeProviderHealth, realtimeServices, adapterHealth, runtimeHealth, realtimeLoading, realtimeError,
    qualityIssues, minuteImports, minuteDirectory,
    healthState, realtimeProviderStateType, realtimeStateType, realtimeStateText, realtimeDeliveryDetail,
    loadRealtimeServices,
  };
}

export type ProvidersSlice = ReturnType<typeof useProvidersSlice>;
