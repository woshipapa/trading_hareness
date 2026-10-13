import { afterEach, describe, expect, it, vi } from 'vitest';
import { useTargetMembers } from './useTargetMembers';

const jsonResponse = (value: unknown, status = 200) => new Response(JSON.stringify(value), {
  status,
  headers: { 'content-type': 'application/json' },
});

afterEach(() => vi.unstubAllGlobals());

describe('useTargetMembers', () => {
  it('loads each tab and sends member actions to the typed endpoints', async () => {
    const fetchMock = vi.fn()
      .mockResolvedValueOnce(jsonResponse({ status: 'ok', chat_id: '7001', members: [{ user_id: '100', name: '甲', joined_at: 1, is_bot: true, is_self: false }] }))
      .mockResolvedValueOnce(jsonResponse({ status: 'ok', chat_id: '7001', rows: [{ user_id: '100', name: '甲', action: 'join', event: 18, chatter_type: 1, at: 2 }], next_cursor: '9' }))
      .mockResolvedValueOnce(jsonResponse({ status: 'ok', chat_id: '7001', applications: [{ id: 3, status: 1, apply_time: 4, message: '请加我', contact_summary: { name: '乙' } }] }))
      .mockResolvedValueOnce(jsonResponse({ status: 'ok' }))
      .mockResolvedValueOnce(jsonResponse({ status: 'ok' }));
    vi.stubGlobal('fetch', fetchMock);
    const state = useTargetMembers();

    await state.loadMembers('7001');
    await state.loadHistory('7001');
    await state.loadApplications('7001');
    expect(state.members.value[0]?.name).toBe('甲');
    expect(state.members.value[0]?.is_bot).toBe(true);
    expect(state.nextHistoryCursor.value).toBe('9');
    expect(state.applications.value[0]?.contact_summary?.name).toBe('乙');
    await state.removeMember('7001', '100');
    await state.decideApplication('7001', 3, true);

    expect(fetchMock).toHaveBeenNthCalledWith(1, '/api/group-relay/target-groups/7001/members', expect.any(Object));
    expect(fetchMock).toHaveBeenNthCalledWith(2, '/api/group-relay/target-groups/7001/history', expect.any(Object));
    expect(fetchMock).toHaveBeenNthCalledWith(3, '/api/group-relay/target-groups/7001/applications', expect.any(Object));
    expect(fetchMock).toHaveBeenNthCalledWith(4, '/api/group-relay/target-groups/7001/members/100', expect.objectContaining({ method: 'DELETE' }));
    expect(fetchMock).toHaveBeenNthCalledWith(5, '/api/group-relay/target-groups/7001/applications/3', expect.objectContaining({ method: 'POST', body: '{"agree":true}' }));
  });

  it('keeps the previous list and exposes the server message when a load fails', async () => {
    const fetchMock = vi.fn()
      .mockResolvedValueOnce(jsonResponse({ status: 'ok', chat_id: '7001', members: [{ user_id: '100', name: '甲', joined_at: 1, is_bot: false, is_self: true }] }))
      .mockResolvedValueOnce(jsonResponse({ message: '目标群不是本账号群主' }, 400));
    vi.stubGlobal('fetch', fetchMock);
    const state = useTargetMembers();

    await state.loadMembers('7001');
    expect(await state.loadMembers('7001')).toBe(false);
    expect(state.members.value).toEqual([{ user_id: '100', name: '甲', joined_at: 1, is_bot: false, is_self: true }]);
    expect(state.error.value).toBe('目标群不是本账号群主');
  });
});
