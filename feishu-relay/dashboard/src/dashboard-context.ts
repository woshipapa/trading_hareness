import type { ComputedRef, InjectionKey, Ref } from 'vue';
import type { useFeishuRelayWorkspace } from './composables/useFeishuRelayWorkspace';

/**
 * Feature context for the independently deployed Feishu dashboard.
 *
 * Views receive only the dashboard setup bindings and never reach into a
 * parent component instance. The dashboard shell remains the owner of polling
 * and mutations while the adapter remains the HTTP/WebSocket boundary.
 */
export type DashboardContext = Record<string, unknown>;

/** Feature-scoped contract for the independently mounted Feishu workbench. */
export type FeishuWorkbenchContext = ReturnType<typeof useFeishuRelayWorkspace> & {
  mobileLayout: Ref<boolean>;
  dateText: (value?: string | null) => string;
};

export const feishuWorkbenchContextKey: InjectionKey<FeishuWorkbenchContext> = Symbol('feishu-workbench-context');

type GroupRelayEvent = {
  event_id: string;
  received_at: string;
  message_type?: string;
  text?: string;
  source_label?: string;
  n8n_status?: string;
  n8n_error?: string | null;
};

export type GroupRelayMonitorContext = ReturnType<typeof useFeishuRelayWorkspace> & {
  mobileLayout: Ref<boolean>;
  eventFilter: Ref<string>;
  visibleEvents: ComputedRef<GroupRelayEvent[]>;
  dateText: (value?: string | null) => string;
  ageText: (value?: number | null) => string;
};

export const groupRelayMonitorContextKey: InjectionKey<GroupRelayMonitorContext> = Symbol('group-relay-monitor-context');
