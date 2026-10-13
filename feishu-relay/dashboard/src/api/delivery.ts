import { getJson } from './http';

export type DeliveryCounts = {
  matched: number;
  unmatched: number;
  recalled: number;
  recall_failed: number;
};

export type DeliveryRow = {
  delivery_id: number | string;
  source_message_id: string;
  target_chat_id: string;
  fingerprint: string;
  sent_at: string;
  target_message_id: string | null;
  observed_at: string | null;
  recalled_at: string | null;
  recall_error: string | null;
};

export type DeliveryResponse = {
  counts: DeliveryCounts;
  deliveries: DeliveryRow[];
};

export const deliveryApi = {
  list: (chatId: string) => getJson<DeliveryResponse>(`/api/group-relay/target-groups/${encodeURIComponent(chatId)}/deliveries`),
};
