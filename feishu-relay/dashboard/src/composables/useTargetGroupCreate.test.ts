import { afterEach, describe, expect, it, vi } from 'vitest';
import { useTargetGroupCreate } from './useTargetGroupCreate';

const jsonResponse = (value: unknown, status = 200) => new Response(JSON.stringify(value), {
  status,
  headers: { 'content-type': 'application/json' },
});

afterEach(() => vi.unstubAllGlobals());

describe('useTargetGroupCreate', () => {
  it('creates a group, reloads the shared registry and exposes its invite', async () => {
    const fetchMock = vi.fn()
      .mockResolvedValueOnce(jsonResponse({ target: { chat_id: 'wh_new', name: '同步群', keyword: '转发', origin: 'dashboard', usable: true }, invite: { status: 'ok', chat_id: '7690000000000000001', shared_url: 'https://applink.feishu.cn/client/chat/chatter/add_by_link?link_token=token', kou_ling: '口令', expire_time: 7 } }))
      .mockResolvedValueOnce(jsonResponse({ targets: [{ chat_id: 'wh_new', name: '同步群', keyword: '转发', origin: 'dashboard', usable: true }] }));
    vi.stubGlobal('fetch', fetchMock);
    const group = useTargetGroupCreate();
    group.name.value = ' 同步群 ';
    group.keyword.value = ' 转发 ';

    await group.createGroup();

    expect(fetchMock).toHaveBeenNthCalledWith(1, '/api/group-relay/target-groups', expect.objectContaining({
      method: 'POST', body: JSON.stringify({ name: '同步群', keyword: '转发', only_owner_posts: true }),
    }));
    expect(fetchMock).toHaveBeenNthCalledWith(2, '/api/group-relay/target-groups', expect.any(Object));
    expect(group.invite.value?.kou_ling).toBe('口令');
    expect(group.error.value).toBe('');
  });

  it('keeps the form and surfaces the server message on failure', async () => {
    vi.stubGlobal('fetch', vi.fn().mockResolvedValueOnce(jsonResponse({ message: '同步群「同步群」创建转发机器人失败：账号未登录' }, 400)));
    const group = useTargetGroupCreate();
    group.name.value = '同步群';

    await group.createGroup();

    expect(group.name.value).toBe('同步群');
    expect(group.error.value).toBe('同步群「同步群」创建转发机器人失败：账号未登录');
    expect(group.invite.value).toBeNull();
  });

  it('regenerates an invite through the target id route', async () => {
    const fetchMock = vi.fn().mockResolvedValueOnce(jsonResponse({ invite: { status: 'ok', chat_id: '7690000000000000001', shared_url: 'https://applink.feishu.cn/client/chat/chatter/add_by_link?link_token=token2', kou_ling: '新口令', expire_time: 8 } }));
    vi.stubGlobal('fetch', fetchMock);
    const group = useTargetGroupCreate();

    await group.regenerateInvite('wh_new', '同步群');

    expect(fetchMock).toHaveBeenCalledWith('/api/group-relay/target-groups/wh_new/invite', expect.objectContaining({ method: 'POST', body: '{}' }));
    expect(group.invite.value?.kou_ling).toBe('新口令');
  });
});
