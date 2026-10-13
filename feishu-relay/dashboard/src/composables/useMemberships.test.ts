import { afterEach, describe, expect, it, vi } from 'vitest';
import { nextTick, ref } from 'vue';
import { useMemberships } from './useMemberships';
import type { TargetRegistryRow } from '../api/target-registry';

const target = (overrides: Partial<TargetRegistryRow> = {}): TargetRegistryRow => ({
  chat_id: 'oc_target', name: '目标群', keyword: '转发', origin: 'dashboard', usable: true, managed: true,
  websocket_chat_id: '7696053062053530578', bot_id: 'bot-1', bot_name: '转发机器人', owned: true, ...overrides,
});
const member = (overrides: Record<string, unknown> = {}) => ({
  target_chat_id: 'oc_target', user_id: 'u1', display_name: '会员一', started_on: '2026-01-01', term_months: 1,
  expires_on: '2026-02-01', note: null, reminded_on: null, remind_claimed_at: null, left_at: null,
  created_at: '2026-01-01T00:00:00.000Z', updated_at: '2026-01-01T00:00:00.000Z', status: '已到期', ...overrides,
});
const response = (value: unknown, status = 200) => new Response(JSON.stringify(value), { status, headers: { 'content-type': 'application/json' } });
const settings = { target_chat_id: 'oc_target', enabled: true, default_term_months: 1, remind_text: '请续费', exempt_user_ids: [], last_review_on: null };

afterEach(() => vi.unstubAllGlobals());

describe('useMemberships', () => {
  it('loads the selected target and sorts expired members first without converting dates', async () => {
    const fetchMock = vi.fn()
      .mockResolvedValueOnce(response({ members: [member({ user_id: 'u2', display_name: '将到期', expires_on: '2026-03-03', status: '7 天内到期' }), member()], today: '2026-03-01' }))
      .mockResolvedValueOnce(response({ settings }));
    vi.stubGlobal('fetch', fetchMock);
    const selected = ref<TargetRegistryRow | null>(target());
    const memberships = useMemberships(selected);
    await vi.waitFor(() => expect(memberships.loading.value).toBe(false));

    expect(memberships.sortedMembers.value.map((row) => row.user_id)).toEqual(['u1', 'u2']);
    expect(memberships.members.value[0]?.started_on).toBe('2026-01-01');
    expect(fetchMock).toHaveBeenNthCalledWith(1, '/api/group-relay/target-groups/oc_target/memberships', expect.any(Object));
  });

  it('runs sync, saves settings, edits exemption, and previews through the typed endpoints', async () => {
    const fetchMock = vi.fn(async (url: string, init?: RequestInit) => {
      if (url.endsWith('/memberships/sync')) return response({ members: [member()], today: '2026-03-01' });
      if (url.endsWith('/membership-settings') && (init?.method ?? 'GET') === 'GET') return response({ settings });
      if (url.endsWith('/membership-settings') && init?.method === 'PUT') return response({ settings: { ...settings, default_term_months: 3, exempt_user_ids: ['u1'] } });
      if (url.endsWith('/memberships/review')) return response({ members: [member()], dry_run: true });
      if (url.endsWith('/memberships/u1') && init?.method === 'PUT') return response({ member: member({ display_name: '更新后' }) });
      if (url.endsWith('/memberships') && (init?.method ?? 'GET') === 'GET') return response({ members: [member({ display_name: '更新后' })], today: '2026-03-01' });
      return response({ members: [member()], today: '2026-03-01' });
    });
    vi.stubGlobal('fetch', fetchMock);
    const selected = ref<TargetRegistryRow | null>(target());
    const memberships = useMemberships(selected);
    await vi.waitFor(() => expect(memberships.loading.value).toBe(false));
    expect(await memberships.sync()).toBe(true);
    expect(await memberships.saveSettings({ enabled: true, default_term_months: 3, remind_text: '请续费' })).toBe(true);
    expect(await memberships.saveMember('u1', { started_on: '2026-01-01', term_months: 3, expires_on: null, note: null, exempt: true })).toBe(true);
    expect(await memberships.preview()).toHaveLength(1);
    expect(fetchMock.mock.calls.some(([url, init]) => url.endsWith('/memberships/sync') && init?.method === 'POST')).toBe(true);
    expect(fetchMock.mock.calls.some(([url, init]) => url.endsWith('/membership-settings') && init?.method === 'PUT' && JSON.parse(String(init.body)).default_term_months === 3)).toBe(true);
  });

  it('keeps existing data and reports a refused mutation', async () => {
    const fetchMock = vi.fn()
      .mockResolvedValueOnce(response({ members: [member()], today: '2026-03-01' }))
      .mockResolvedValueOnce(response({ settings }))
      .mockResolvedValueOnce(response({ message: '群主权限不足' }, 400));
    vi.stubGlobal('fetch', fetchMock);
    const selected = ref<TargetRegistryRow | null>(target());
    const memberships = useMemberships(selected);
    await vi.waitFor(() => expect(memberships.loading.value).toBe(false));
    expect(await memberships.saveSettings({ enabled: false, default_term_months: 1, remind_text: '不要提醒' })).toBe(false);
    expect(memberships.error.value).toBe('群主权限不足');
    expect(memberships.settings.value).toEqual(settings);
    expect(memberships.members.value).toHaveLength(1);
  });

  it('reloads stored member data after a refused edit while preserving the server message', async () => {
    const serverMember = member({ display_name: '服务器版本' });
    const fetchMock = vi.fn()
      .mockResolvedValueOnce(response({ members: [member()], today: '2026-03-01' }))
      .mockResolvedValueOnce(response({ settings }))
      .mockResolvedValueOnce(response({ message: '会员已被其他人修改' }, 400))
      .mockResolvedValueOnce(response({ members: [serverMember], today: '2026-03-01' }))
      .mockResolvedValueOnce(response({ settings }));
    vi.stubGlobal('fetch', fetchMock);
    const selected = ref<TargetRegistryRow | null>(target());
    const memberships = useMemberships(selected);
    await vi.waitFor(() => expect(memberships.loading.value).toBe(false));
    expect(await memberships.saveMember('u1', { started_on: '2026-01-01', term_months: 2, expires_on: null, note: null, exempt: false })).toBe(false);
    expect(memberships.error.value).toBe('会员已被其他人修改');
    expect(memberships.members.value[0]?.display_name).toBe('服务器版本');
  });

  it('clears the panel state for an unowned or non-numeric target', async () => {
    const selected = ref<TargetRegistryRow | null>(target({ owned: false, websocket_chat_id: null }));
    const memberships = useMemberships(selected);
    await nextTick();
    expect(memberships.eligible.value).toBe(false);
    expect(memberships.members.value).toEqual([]);
  });
});
