import { getJson, postJson } from './http';

export type TargetRegistryRow = {
  chat_id: string;
  name: string | null;
  keyword: string | null;
  /** 'env' and 'dashboard' are relay targets; 'owned' is a group the account owns that has no relay bot yet. */
  origin: 'env' | 'dashboard' | 'owned';
  usable: boolean;
  managed?: boolean;
  websocket_chat_id?: string | null;
  bot_id?: string | null;
  bot_name?: string | null;
  owned?: boolean | null;
};

type TargetRegistryResponse = { targets: TargetRegistryRow[] };

export const targetRegistryApi = {
  list: () => getJson<TargetRegistryResponse>('/api/group-relay/target-groups'),
  resolve: () => postJson<TargetRegistryResponse>('/api/group-relay/target-groups/resolve', {}),
};
