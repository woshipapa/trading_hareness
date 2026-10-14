import { getJson, postJson, putJson } from './http';

export type MembershipStatus = '正常' | '7 天内到期' | '已到期' | '已离群' | '已豁免';

/** What the member's alias says about the membership; offered in the edit form, never applied by itself. */
export type MembershipSuggestion = { started_on: string | null; term_months: number | null; expires_on: string | null; label: string; /** Where in the alias it was read. */ span?: [number, number] };

export type MembershipRow = {
  target_chat_id: string;
  user_id: string;
  display_name: string;
  /** The name the account gave the member in Feishu (备注名), as of the last sync. */
  alias?: string;
  started_on: string;
  term_months: number;
  expires_on: string;
  /** The expiry date was typed, not counted from the term. */
  expiry_fixed?: boolean;
  suggestions?: MembershipSuggestion[];
  note: string | null;
  reminded_on: string | null;
  remind_claimed_at: string | null;
  left_at: string | null;
  created_at: string;
  updated_at: string;
  status: MembershipStatus;
};

export type MembershipSettings = {
  target_chat_id: string;
  enabled: boolean;
  default_term_months: number;
  remind_text: string;
  exempt_user_ids: string[];
  last_review_on: string | null;
};

type MembershipResponse = { members: MembershipRow[]; today?: string };
type SettingsResponse = { settings: MembershipSettings };

const path = (chatId: string, suffix: string) => `/api/group-relay/target-groups/${encodeURIComponent(chatId)}/memberships${suffix}`;
const settingsPath = (chatId: string) => `/api/group-relay/target-groups/${encodeURIComponent(chatId)}/membership-settings`;

export const membershipsApi = {
  list: (chatId: string) => getJson<MembershipResponse>(path(chatId, '')),
  settings: (chatId: string) => getJson<SettingsResponse>(settingsPath(chatId)),
  sync: (chatId: string) => postJson<MembershipResponse>(path(chatId, '/sync'), {}),
  review: (chatId: string, dryRun: boolean) => postJson<MembershipResponse & { dry_run: boolean }>(path(chatId, '/review'), { dry_run: dryRun }),
  updateSettings: (chatId: string, body: { enabled: boolean; default_term_months: number; remind_text: string; exempt_user_ids: string[] }) => putJson<SettingsResponse>(settingsPath(chatId), body),
  updateMember: (chatId: string, userId: string, body: { started_on: string; term_months: number; expires_on: string | null; note: string | null; exempt: boolean; alias?: string }) => putJson<{ member: MembershipRow }>(`${path(chatId, '')}/${encodeURIComponent(userId)}`, body),
  remind: (chatId: string, userId: string) => postJson<{ member: MembershipRow }>(`${path(chatId, '')}/${encodeURIComponent(userId)}/remind`, {}),
};
