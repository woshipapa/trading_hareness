import { deleteJson, getJson, postJson } from './http';

export type TargetMember = { user_id: string; name: string; joined_at: number; is_bot: boolean; is_self: boolean };
export type TargetMembersResponse = { status: 'ok'; chat_id: string; members: TargetMember[] };

export type TargetMemberHistoryRow = {
  user_id: string;
  name: string;
  action: 'join' | 'leave';
  event: number;
  chatter_type: number;
  at: number;
};
export type TargetMemberHistoryResponse = {
  status: 'ok';
  chat_id: string;
  rows: TargetMemberHistoryRow[];
  next_cursor: string | number | null;
};

export type TargetMemberApplication = {
  id: string | number;
  status: number;
  apply_time: number;
  message: string | null;
  contact_summary: Record<string, unknown> | null;
};
export type TargetMemberApplicationsResponse = {
  status: 'ok';
  chat_id: string;
  applications: TargetMemberApplication[];
};

const targetPath = (chatId: string, suffix: string) => `/api/group-relay/target-groups/${encodeURIComponent(chatId)}/${suffix}`;

export const targetMembersApi = {
  members: (chatId: string) => getJson<TargetMembersResponse>(targetPath(chatId, 'members')),
  history: (chatId: string, cursor?: string | number | null) => {
    const query = cursor === undefined || cursor === null ? '' : `?cursor=${encodeURIComponent(String(cursor))}`;
    return getJson<TargetMemberHistoryResponse>(`${targetPath(chatId, 'history')}${query}`);
  },
  applications: (chatId: string) => getJson<TargetMemberApplicationsResponse>(targetPath(chatId, 'applications')),
  decideApplication: (chatId: string, id: string | number, agree: boolean) => postJson<{ status: string }>(`${targetPath(chatId, 'applications')}/${encodeURIComponent(String(id))}`, { agree }),
  removeMember: (chatId: string, userId: string) => deleteJson<{ status: string }>(`${targetPath(chatId, 'members')}/${encodeURIComponent(userId)}`),
};
