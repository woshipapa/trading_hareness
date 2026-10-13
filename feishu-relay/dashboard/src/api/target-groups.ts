import { postJson } from './http';
import type { TargetRegistryRow } from './target-registry';

export type TargetGroupInvite = {
  status: string;
  chat_id: string;
  shared_url: string;
  kou_ling: string;
  expire_time: number;
};

export type TargetGroupCreateInput = {
  name: string;
  keyword?: string;
  only_owner_posts: boolean;
};

export type TargetGroupCreateResponse = {
  target: TargetRegistryRow;
  invite: TargetGroupInvite;
};

export type TargetGroupInviteResponse = { invite: TargetGroupInvite };

export const targetGroupsApi = {
  create: (input: TargetGroupCreateInput) => postJson<TargetGroupCreateResponse>('/api/group-relay/target-groups', input),
  invite: (chatId: string) => postJson<TargetGroupInviteResponse>(`/api/group-relay/target-groups/${encodeURIComponent(chatId)}/invite`, {}),
};
