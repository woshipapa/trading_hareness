import { ref, watch, type Ref } from 'vue';
import { deliveryApi, type DeliveryCounts, type DeliveryRow } from '../api/delivery';
import type { TargetRegistryRow } from '../api/target-registry';

const emptyCounts = (): DeliveryCounts => ({ matched: 0, unmatched: 0, recalled: 0, recall_failed: 0 });

export function useDeliveryCheck(selectedTarget: Ref<TargetRegistryRow | null>) {
  const counts = ref<DeliveryCounts>(emptyCounts());
  const deliveries = ref<DeliveryRow[]>([]);
  const loading = ref(false);
  const error = ref('');
  let requestVersion = 0;

  async function load() {
    const target = selectedTarget.value;
    const version = ++requestVersion;
    if (!target) {
      counts.value = emptyCounts();
      deliveries.value = [];
      error.value = '';
      return;
    }
    loading.value = true;
    error.value = '';
    const chatId = target.chat_id;
    try {
      const result = await deliveryApi.list(chatId);
      if (version !== requestVersion || selectedTarget.value?.chat_id !== chatId) return;
      counts.value = result.counts;
      deliveries.value = result.deliveries;
    } catch (cause) {
      if (version !== requestVersion || selectedTarget.value?.chat_id !== chatId) return;
      error.value = cause instanceof Error ? cause.message : String(cause);
    } finally {
      if (version === requestVersion) loading.value = false;
    }
  }

  watch(selectedTarget, () => { void load(); }, { immediate: true });

  return { counts, deliveries, loading, error, load };
}
