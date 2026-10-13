import { afterEach, describe, expect, it, vi } from 'vitest';
import { useJoinByLink } from './useJoinByLink';

const jsonResponse = (value: unknown, status = 200) => new Response(JSON.stringify(value), {
  status,
  headers: { 'content-type': 'application/json' },
});

afterEach(() => vi.unstubAllGlobals());

describe('useJoinByLink', () => {
  it('previews a link and joins successfully, clearing the credential state', async () => {
    const fetchMock = vi.fn()
      .mockResolvedValueOnce(jsonResponse({ status: 'ok', chat_id: '7696', name: '研究群', owner_name: '群主甲', member_count: 12, already_in_chat: false, expire_time: 1_800_000_000 }))
      .mockResolvedValueOnce(jsonResponse({ status: 'ok', chat_id: '7696', name: '研究群' }));
    vi.stubGlobal('fetch', fetchMock);
    const joinLink = useJoinByLink();
    joinLink.expand();
    joinLink.link.value = 'https://applink.feishu.cn/client/chat/chatter/add_by_link?link_token=redacted';

    await joinLink.preview();
    expect(fetchMock).toHaveBeenNthCalledWith(1, '/api/group-relay/chats/link-info', expect.objectContaining({ method: 'POST', body: JSON.stringify({ link: joinLink.link.value }) }));
    expect(joinLink.info.value?.name).toBe('研究群');
    expect(joinLink.info.value?.owner_name).toBe('群主甲');

    await expect(joinLink.join()).resolves.toEqual({ status: 'ok', chat_id: '7696', name: '研究群' });
    expect(fetchMock).toHaveBeenNthCalledWith(2, '/api/group-relay/chats/join', expect.objectContaining({ method: 'POST', body: JSON.stringify({ link: 'https://applink.feishu.cn/client/chat/chatter/add_by_link?link_token=redacted' }) }));
    expect(joinLink.expanded.value).toBe(false);
    expect(joinLink.link.value).toBe('');
    expect(joinLink.info.value).toBeNull();
  });

  it('keeps the preview when the server refuses a call', async () => {
    vi.stubGlobal('fetch', vi.fn()
      .mockResolvedValueOnce(jsonResponse({ status: 'ok', chat_id: '7696', name: '研究群', owner_name: null, member_count: 12, already_in_chat: false, expire_time: 1_800_000_000 }))
      .mockResolvedValueOnce(jsonResponse({ message: '邀请链接已失效' }, 400)));
    const joinLink = useJoinByLink();
    joinLink.expand();
    joinLink.link.value = 'invite';
    await joinLink.preview();

    await expect(joinLink.join()).resolves.toBeNull();
    expect(joinLink.info.value?.chat_id).toBe('7696');
    expect(joinLink.link.value).toBe('invite');
    expect(joinLink.error.value).toBe('邀请链接已失效');
    expect(joinLink.expanded.value).toBe(true);
  });
});
