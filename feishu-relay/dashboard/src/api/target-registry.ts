import { getJson, postJson } from './http';

export type TargetRegistryRow = {
  chat_id: string;
  name: string | null;
  keyword: string | null;
  origin: 'env' | 'dashboard';
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
