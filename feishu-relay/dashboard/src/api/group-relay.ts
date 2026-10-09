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

export type ExportFormat = 'transcript' | 'ndjson';

export type ExportBookmark = {
  chat_id: string;
  next_sequence: number;
  last_from_time: number | null;
  last_to_time: number | null;
  last_event_count: number;
  cumulative_events: number;
  last_format: string | null;
  last_export_at: string;
};

export type ExportHistoryOptions = {
  format?: ExportFormat;
  // 'incremental' resumes from the server-side bookmark and advances it;
  // otherwise a fixed recent window of `days`.
  mode?: 'incremental' | 'window';
  days?: 1 | 7 | 30;
};

export const groupRelayApi = {
  status: <T>() => getJson<T>('/api/group-relay/status'),
  listExportBookmarks: () => getJson<{ bookmarks: ExportBookmark[] }>('/api/group-relay/larkagentx/export-bookmarks'),
  exportLarkAgentXHistory: async (chatId: string, options: ExportHistoryOptions = {}) => {
    const format: ExportFormat = options.format ?? 'transcript';
    const query = new URLSearchParams({ chat_id: chatId, format });
    if (options.mode === 'incremental') query.set('mode', 'incremental');
    else query.set('days', String(options.days ?? 7));
    const response = await fetch(`/api/group-relay/larkagentx/history/export?${query.toString()}`, { cache: 'no-store' });
    if (!response.ok) {
      const body = await response.text();
      let message = body;
      try { message = JSON.parse(body)?.message ?? body; } catch { /* preserve a non-JSON proxy error */ }
      throw new Error(message || `HTTP ${response.status}`);
    }
    const number = (name: string) => { const value = response.headers.get(name); return value == null ? null : Number(value); };
    return {
      blob: await response.blob(),
      filename: response.headers.get('content-disposition')?.match(/filename="([^"]+)"/)?.[1]
        ?? `larkagentx-${chatId}.${format === 'transcript' ? 'txt' : 'jsonl'}`,
      count: Number(response.headers.get('x-larkagentx-event-count') ?? 0),
      fromTime: number('x-larkagentx-from-time'),
      toTime: number('x-larkagentx-to-time'),
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
