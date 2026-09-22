/** Typed transport boundary for managed group-relay routes. */

import { deleteJson, getJson, postJson, putJson } from './http';

export type GroupRelayRouteInput = {
  chat_name: string;
  chat_id?: string;
  tag: string;
  target_chat_ids?: string[];
  target_chat_names?: string[];
  enabled: boolean;
};

export const groupRelayApi = {
  status: <T>() => getJson<T>('/api/group-relay/status'),
  exportLarkAgentXHistory: async (chatId: string, days: 1 | 7 | 30) => {
    const query = new URLSearchParams({ chat_id: chatId, days: String(days) });
    const response = await fetch(`/api/group-relay/larkagentx/history/export?${query.toString()}`, { cache: 'no-store' });
    if (!response.ok) {
      const body = await response.text();
      let message = body;
      try { message = JSON.parse(body)?.message ?? body; } catch { /* preserve a non-JSON proxy error */ }
      throw new Error(message || `HTTP ${response.status}`);
    }
    return {
      blob: await response.blob(),
      filename: response.headers.get('content-disposition')?.match(/filename="([^"]+)"/)?.[1] ?? `larkagentx-${chatId}-last-${days}d.jsonl`,
      count: Number(response.headers.get('x-larkagentx-event-count') ?? 0),
    };
  },
  refreshItougu: <T>() => postJson<T>('/api/group-relay/itougu/refresh'),
  upsertRoute: <T>(key: string, input: GroupRelayRouteInput) => (
    key
      ? putJson<T>(`/api/group-relay/routes/${encodeURIComponent(key)}`, input)
      : postJson<T>('/api/group-relay/routes', input)
  ),
  removeRoute: <T>(key: string) => deleteJson<T>(`/api/group-relay/routes/${encodeURIComponent(key)}`),
};
