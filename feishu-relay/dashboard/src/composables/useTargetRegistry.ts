import { ref } from 'vue';
import { targetRegistryApi, type TargetRegistryRow } from '../api/target-registry';
import { groupRelayApi } from '../api/group-relay';

const targets = ref<TargetRegistryRow[]>([]);
const loading = ref(false);
const error = ref('');
const resolving = ref(false);
const connecting = ref('');

export function useTargetRegistry() {

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

  // Gives a group the account owns its relay bot, which makes it a relay target. Returns the row that replaces the group's.
  async function connect(row: TargetRegistryRow, keyword: string) {
    connecting.value = row.chat_id;
    error.value = '';
    try {
      await groupRelayApi.addTarget({ name: row.name ?? '', websocket_chat_id: row.websocket_chat_id ?? '', keyword });
      targets.value = (await targetRegistryApi.list()).targets;
      return targets.value.find((item) => item.websocket_chat_id === row.websocket_chat_id) ?? null;
    } catch (cause) {
      error.value = cause instanceof Error ? cause.message : String(cause);
      return null;
    } finally {
      connecting.value = '';
    }
  }

  return { targets, loading, error, resolving, connecting, load, resolve, connect };
}
