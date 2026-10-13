import { ref } from 'vue';
import { joinLinkApi, type JoinLinkInfo, type JoinLinkResult } from '../api/join-link';

export function useJoinByLink() {
  const expanded = ref(false);
  const link = ref('');
  const info = ref<JoinLinkInfo | null>(null);
  const loading = ref(false);
  const joining = ref(false);
  const error = ref('');

  function expand() {
    expanded.value = true;
  }

  function reset() {
    expanded.value = false;
    link.value = '';
    info.value = null;
    error.value = '';
  }

  function collapse() {
    reset();
  }

  async function preview() {
    loading.value = true;
    error.value = '';
    try {
      info.value = await joinLinkApi.linkInfo(link.value);
    } catch (cause) {
      error.value = cause instanceof Error ? cause.message : String(cause);
    } finally {
      loading.value = false;
    }
  }

  async function join(): Promise<JoinLinkResult | null> {
    joining.value = true;
    error.value = '';
    try {
      const result = await joinLinkApi.join(link.value);
      reset();
      return result;
    } catch (cause) {
      error.value = cause instanceof Error ? cause.message : String(cause);
      return null;
    } finally {
      joining.value = false;
    }
  }

  function choose(): string | null {
    if (!info.value) return null;
    const chatId = info.value.chat_id;
    reset();
    return chatId;
  }

  return { expanded, link, info, loading, joining, error, expand, collapse, preview, join, choose };
}
