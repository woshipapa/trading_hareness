import { ref, watch, type Ref } from 'vue';
import { deliveryApi, type DeliveryCounts, type DeliveryRow } from '../api/delivery';
import type { TargetRegistryRow } from '../api/target-registry';

const emptyCounts = (): DeliveryCounts => ({ matched: 0, unmatched: 0, recalled: 0, recall_failed: 0 });

export function useDeliveryCheck(selectedTarget: Ref<TargetRegistryRow | null>) {
  const counts = ref<DeliveryCounts>(emptyCounts());
  const deliveries = ref<DeliveryRow[]>([]);
  const loading = ref(false);
  const error = ref('');

  async function load() {
    const target = selectedTarget.value;
    if (!target) {
      counts.value = emptyCounts();
      deliveries.value = [];
      error.value = '';
      return;
    }
    loading.value = true;
    error.value = '';
    try {
      const result = await deliveryApi.list(target.chat_id);
      counts.value = result.counts;
      deliveries.value = result.deliveries;
    } catch (cause) {
      error.value = cause instanceof Error ? cause.message : String(cause);
    } finally {
      loading.value = false;
    }
  }

  watch(selectedTarget, () => { void load(); }, { immediate: true });

  return { counts, deliveries, loading, error, load };
}
