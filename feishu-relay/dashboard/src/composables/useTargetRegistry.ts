import { ref } from 'vue';
import { targetRegistryApi, type TargetRegistryRow } from '../api/target-registry';

export function useTargetRegistry() {
  const targets = ref<TargetRegistryRow[]>([]);
  const loading = ref(false);
  const error = ref('');
  const resolving = ref(false);

  async function load() {
    loading.value = true;
    error.value = '';
    try {
      targets.value = (await targetRegistryApi.list()).targets;
    } catch (cause) {
      error.value = cause instanceof Error ? cause.message : String(cause);
    } finally {
      loading.value = false;
    }
  }

  async function resolve() {
    resolving.value = true;
    error.value = '';
    try {
      targets.value = (await targetRegistryApi.resolve()).targets;
    } catch (cause) {
      error.value = cause instanceof Error ? cause.message : String(cause);
    } finally {
      resolving.value = false;
    }
  }

  return { targets, loading, error, resolving, load, resolve };
}
