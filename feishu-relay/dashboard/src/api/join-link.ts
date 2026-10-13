import { postJson } from './http';

export type JoinLinkInfo = {
  status: 'ok';
  chat_id: string;
  name: string;
  member_count: number;
  already_in_chat: boolean;
  expire_time: number;
};

export type JoinLinkResult = {
  status: 'ok';
  chat_id: string;
  name: string;
};

export const joinLinkApi = {
  linkInfo: (link: string) => postJson<JoinLinkInfo>('/api/group-relay/chats/link-info', { link }),
  join: (link: string) => postJson<JoinLinkResult>('/api/group-relay/chats/join', { link }),
};
