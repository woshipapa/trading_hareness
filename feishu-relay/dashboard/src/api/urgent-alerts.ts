import { getJson, postJson, putJson } from './http';

export type UrgentAlertSettings = {
  enabled: boolean;
  chat_id: string;
  user_id: string;
  name: string;
};

export type UrgentAlertRecipient = {
  chat_id: string;
  user_id: string;
  name: string;
};

type SettingsResponse = { settings: UrgentAlertSettings };
type RecipientsResponse = { recipients: UrgentAlertRecipient[] };

export const urgentAlertsApi = {
  settings: () => getJson<SettingsResponse>('/api/group-relay/alerts/settings'),
  recipients: () => getJson<RecipientsResponse>('/api/group-relay/alerts/recipients'),
  saveSettings: (settings: UrgentAlertSettings) => putJson<SettingsResponse>('/api/group-relay/alerts/settings', settings),
  sendTest: () => postJson<Record<string, unknown>>('/api/group-relay/alerts/test', {}),
};
