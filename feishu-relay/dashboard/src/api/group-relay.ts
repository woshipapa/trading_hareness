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
  // 'ledger' = OAuth-backfilled official content (real text for downgraded
  // groups); transcript + fixed window only.
  source?: 'archive' | 'ledger';
};

export type SendAuditRow = {
  id: number; idempotency_key: string | null; chat_id: string; text_sha256: string; text_length: number;
  root_id: string | null; result: 'pending' | 'accepted' | 'failed' | 'unknown'; error: string | null;
  created_at: number; finished_at: number | null;
};

export type SendStats = {
  per_chat_per_min: number; global_per_hour: number; global_remaining: number;
  chat_remaining: Record<string, number>; failed_last_hour: number;
  last_failure: { id: number; chat_id: string; error: string | null; created_at: number } | null;
};

export type WebsocketBinding = {
  status: 'bound' | 'unbound' | 'not_bound' | 'failed' | 'unavailable';
  chat_id?: string | null;
  chat_name?: string | null;
  message?: string;
};

export type BundlePreview = {
  chat_name: string; from: string; to: string; filename: string; chat_ids: string[];
  messages: number; images: number; first_message_at: string | null; last_message_at: string | null;
  source: BundleSource; relayed_messages: number; history_positions: number | null;
};

// 'full' reads the group's own history from Feishu and lays the relay ledger
// over it; 'relayed' exports only what the relay forwarded.
export type BundleSource = 'full' | 'relayed';

const bundlePath = (key: string, from: string, to: string, source: BundleSource = 'full') =>
  `/api/group-relay/routes/${encodeURIComponent(key)}/bundle?from=${encodeURIComponent(from)}&to=${encodeURIComponent(to)}&source=${source}`;

export type SendInput = { chat_id: string; text: string; root_id?: string; idempotency_key?: string };

export const groupRelayApi = {
  sendMessage: <T>(input: SendInput) => postJson<T>('/api/group-relay/larkagentx/send', input),
  sendAudit: (limit = 50) => getJson<{ audit: SendAuditRow[]; stats: SendStats }>(`/api/group-relay/larkagentx/send/audit?limit=${limit}`),
  status: <T>() => getJson<T>('/api/group-relay/status'),
  qrInit: <T>() => postJson<T>('/api/group-relay/larkagentx/qr/init'),
  qrPoll: <T>(sessionId: string) => postJson<T>('/api/group-relay/larkagentx/qr/poll', { session_id: sessionId }),
  oauthAuthorizeUrl: <T>() => getJson<T>('/api/group-relay/larkagentx/oauth/authorize-url'),
  oauthExchange: <T>(code: string) => postJson<T>('/api/group-relay/larkagentx/oauth/exchange', { authorization_code: code }),
  listExportBookmarks: () => getJson<{ bookmarks: ExportBookmark[] }>('/api/group-relay/larkagentx/export-bookmarks'),
  exportLarkAgentXHistory: async (chatId: string, options: ExportHistoryOptions = {}) => {
    const format: ExportFormat = options.format ?? 'transcript';
    const query = new URLSearchParams({ chat_id: chatId, format });
    if (options.source === 'ledger') query.set('source', 'ledger');
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
  // The zip is streamed straight to disk by the browser, so only the counts
  // go through fetch; the download itself is a plain navigation to bundleUrl.
  bundlePreview: (key: string, from: string, to: string, source: BundleSource = 'full') => getJson<BundlePreview>(`${bundlePath(key, from, to, source)}&dry_run=1`),
  bundleUrl: bundlePath,
  bindRoute: <T>(key: string) => postJson<T>(`/api/group-relay/routes/${encodeURIComponent(key)}/bind`, {}),
  removeRoute: <T>(key: string) => deleteJson<T>(`/api/group-relay/routes/${encodeURIComponent(key)}`),
};
