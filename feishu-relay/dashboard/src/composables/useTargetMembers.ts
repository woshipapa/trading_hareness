import { reactive, ref } from 'vue';
import {
  targetMembersApi,
  type TargetMember,
  type TargetMemberApplication,
  type TargetMemberHistoryRow,
} from '../api/target-members';

function messageOf(cause: unknown): string {
  return cause instanceof Error ? cause.message : String(cause);
}

export function useTargetMembers() {
  const members = ref<TargetMember[]>([]);
  const history = ref<TargetMemberHistoryRow[]>([]);
  const applications = ref<TargetMemberApplication[]>([]);
  const nextHistoryCursor = ref<string | number | null>(null);
  const error = ref('');
  const loading = reactive({ members: false, history: false, applications: false, action: false });

  async function loadMembers(chatId: string): Promise<boolean> {
    loading.members = true;
    error.value = '';
    try {
      members.value = (await targetMembersApi.members(chatId)).members;
      return true;
    } catch (cause) {
      error.value = messageOf(cause);
      return false;
    } finally {
      loading.members = false;
    }
  }

  async function loadHistory(chatId: string, cursor: string | number | null = null, append = false): Promise<boolean> {
    loading.history = true;
    error.value = '';
    try {
      const result = await targetMembersApi.history(chatId, cursor);
      history.value = append ? [...history.value, ...result.rows] : result.rows;
      nextHistoryCursor.value = result.next_cursor;
      return true;
    } catch (cause) {
      error.value = messageOf(cause);
      return false;
    } finally {
      loading.history = false;
    }
  }

  async function loadApplications(chatId: string): Promise<boolean> {
    loading.applications = true;
    error.value = '';
    try {
      applications.value = (await targetMembersApi.applications(chatId)).applications;
      return true;
    } catch (cause) {
      error.value = messageOf(cause);
      return false;
    } finally {
      loading.applications = false;
    }
  }

  async function removeMember(chatId: string, userId: string): Promise<boolean> {
    loading.action = true;
    error.value = '';
    try {
      await targetMembersApi.removeMember(chatId, userId);
      return true;
    } catch (cause) {
      error.value = messageOf(cause);
      return false;
    } finally {
      loading.action = false;
    }
  }

  async function decideApplication(chatId: string, id: string | number, agree: boolean): Promise<boolean> {
    loading.action = true;
    error.value = '';
    try {
      await targetMembersApi.decideApplication(chatId, id, agree);
      return true;
    } catch (cause) {
      error.value = messageOf(cause);
      return false;
    } finally {
      loading.action = false;
    }
  }

  function reset() {
    members.value = [];
    history.value = [];
    applications.value = [];
    nextHistoryCursor.value = null;
    error.value = '';
  }

  return {
    members, history, applications, nextHistoryCursor, error, loading,
    loadMembers, loadHistory, loadApplications, removeMember, decideApplication, reset,
  };
}
