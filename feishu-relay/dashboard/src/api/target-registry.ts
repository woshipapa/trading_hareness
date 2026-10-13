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

/** A custom bot that is already in a group; `usable` says whether the relay can send through it as it is set up. */
export type ExistingBot = { bot_id: string; name: string; key_words: string[]; usable: boolean; reason: string | null };

export const targetRegistryApi = {
  list: () => getJson<TargetRegistryResponse>('/api/group-relay/target-groups'),
  resolve: () => postJson<TargetRegistryResponse>('/api/group-relay/target-groups/resolve', {}),
  bots: (chatId: string) => getJson<{ bots: ExistingBot[] }>(`/api/group-relay/target-groups/${encodeURIComponent(chatId)}/bots`),
  useBot: (chatId: string, botId: string) => postJson<{ target: TargetRegistryRow }>(`/api/group-relay/target-groups/${encodeURIComponent(chatId)}/bots/${encodeURIComponent(botId)}/use`, {}),
};
