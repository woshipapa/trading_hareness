import { computed, ref, watch, type Ref } from 'vue';
import { membershipsApi, type MembershipRow, type MembershipSettings } from '../api/memberships';
import type { TargetRegistryRow } from '../api/target-registry';

type MembershipEdit = { started_on: string; term_months: number; note: string | null; exempt: boolean };

function sortRank(row: MembershipRow, exempt: boolean): number {
  if (exempt) return 3;
  if (row.status === '已到期') return 0;
  if (row.status === '7 天内到期') return 1;
  return 2;
}

function sortMembers(rows: MembershipRow[], exemptIds: string[]): MembershipRow[] {
  const exempt = new Set(exemptIds);
  return [...rows].sort((left, right) => sortRank(left, exempt.has(left.user_id)) - sortRank(right, exempt.has(right.user_id)) || left.expires_on.localeCompare(right.expires_on) || left.display_name.localeCompare(right.display_name, 'zh') || left.user_id.localeCompare(right.user_id));
}

export function useMemberships(selectedTarget: Ref<TargetRegistryRow | null>) {
  const members = ref<MembershipRow[]>([]);
  const settings = ref<MembershipSettings | null>(null);
  const today = ref('');
  const loading = ref(false);
  const error = ref('');
  const action = ref('');
  let loadVersion = 0;

  const target = computed(() => selectedTarget.value);
  const eligible = computed(() => Boolean(target.value?.owned === true && /^\d+$/.test(target.value.websocket_chat_id ?? '')));
  const sortedMembers = computed(() => sortMembers(members.value, settings.value?.exempt_user_ids ?? []));

  function message(cause: unknown): string {
    return cause instanceof Error ? cause.message : String(cause);
  }

  async function load() {
    const current = target.value;
    if (!current || !eligible.value) {
      loadVersion += 1;
      members.value = [];
      settings.value = null;
      today.value = '';
      error.value = '';
      loading.value = false;
      return false;
    }
    const version = ++loadVersion;
    loading.value = true;
    error.value = '';
    try {
      const [memberResponse, settingsResponse] = await Promise.all([membershipsApi.list(current.chat_id), membershipsApi.settings(current.chat_id)]);
      if (version !== loadVersion || selectedTarget.value?.chat_id !== current.chat_id) return false;
      members.value = memberResponse.members;
      settings.value = settingsResponse.settings;
      today.value = memberResponse.today ?? '';
      return true;
    } catch (cause) {
      if (version === loadVersion) error.value = message(cause);
      return false;
    } finally {
      if (version === loadVersion) loading.value = false;
    }
  }

  async function run<T>(name: string, request: () => Promise<T>): Promise<T | null> {
    action.value = name;
    error.value = '';
    try { return await request(); }
    catch (cause) { error.value = message(cause); return null; }
    finally { action.value = ''; }
  }

  async function sync() {
    if (!target.value || !eligible.value) return false;
    const result = await run('sync', () => membershipsApi.sync(target.value!.chat_id));
    if (!result) return false;
    await load();
    return true;
  }

  async function saveSettings(value: { enabled: boolean; default_term_months: number; remind_text: string }) {
    if (!target.value || !eligible.value || !settings.value) return false;
    const body = { ...value, exempt_user_ids: settings.value.exempt_user_ids };
    const result = await run('save-settings', () => membershipsApi.updateSettings(target.value!.chat_id, body));
    if (!result) return false;
    settings.value = result.settings;
    return true;
  }

  async function preview() {
    if (!target.value || !eligible.value) return null;
    const result = await run('preview', () => membershipsApi.review(target.value!.chat_id, true));
    return result?.members ?? null;
  }

  async function review() {
    if (!target.value || !eligible.value) return false;
    const result = await run('review', () => membershipsApi.review(target.value!.chat_id, false));
    if (!result) return false;
    await load();
    return true;
  }

  async function saveMember(userId: string, value: MembershipEdit) {
    if (!target.value || !eligible.value || !settings.value) return false;
    const chatId = target.value.chat_id;
    const member = await run('save-member', () => membershipsApi.updateMember(chatId, userId, { started_on: value.started_on, term_months: value.term_months, note: value.note }));
    if (!member) return false;
    const exemptIds = new Set(settings.value.exempt_user_ids);
    if (value.exempt) exemptIds.add(userId); else exemptIds.delete(userId);
    const setting = await run('save-exempt', () => membershipsApi.updateSettings(chatId, { enabled: settings.value!.enabled, default_term_months: settings.value!.default_term_months, remind_text: settings.value!.remind_text, exempt_user_ids: [...exemptIds] }));
    if (!setting) return false;
    await load();
    return true;
  }

  async function remind(userId: string) {
    if (!target.value || !eligible.value) return false;
    const result = await run('remind', () => membershipsApi.remind(target.value!.chat_id, userId));
    if (!result) return false;
    await load();
    return true;
  }

  watch(selectedTarget, () => { void load(); }, { immediate: true });

  return { target, eligible, members, sortedMembers, settings, today, loading, error, action, load, sync, saveSettings, preview, review, saveMember, remind };
}

export { sortMembers };
