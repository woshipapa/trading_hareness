import { afterEach, describe, expect, it, vi } from 'vitest';

import { useFeishuRelayWorkspace } from './useFeishuRelayWorkspace';

const jsonResponse = (value: unknown) => new Response(JSON.stringify(value), {
  headers: { 'content-type': 'application/json' },
});

afterEach(() => vi.unstubAllGlobals());

describe('useFeishuRelayWorkspace', () => {
  it('owns relay and workbench status loading outside the dashboard shell', async () => {
    const fetchMock = vi.fn()
      .mockResolvedValueOnce(jsonResponse({ status: 'healthy', sources: [{ key: 'anqiang', tag: 'anqiang', chat_name: '马安强', state: 'healthy' }] }))
      .mockResolvedValueOnce(jsonResponse({ target_configured: true, capabilities: [] }))
      .mockResolvedValueOnce(jsonResponse({ items: [{ source_message_id: 'om_1', route_tag: 'anqiang', status: 'sent' }] }));
    vi.stubGlobal('fetch', fetchMock);
    const workspace = useFeishuRelayWorkspace();

    await workspace.loadGroupRelayStatus();
    await workspace.loadFeishuWorkbench();

    expect(workspace.groupRelayStatus.value.sources?.[0]?.tag).toBe('anqiang');
    expect(workspace.feishuWorkbench.value.target_configured).toBe(true);
    expect(workspace.feishuWorkbenchMessages.value[0]?.source_message_id).toBe('om_1');
    expect(fetchMock).toHaveBeenNthCalledWith(1, '/api/group-relay/status', expect.any(Object));
    expect(fetchMock).toHaveBeenNthCalledWith(2, '/api/feishu-workbench/status', expect.any(Object));
    expect(fetchMock).toHaveBeenNthCalledWith(3, '/api/feishu-workbench/messages?limit=80', expect.any(Object));
  });

  it('lists the groups the account has left apart from the ones being listened to', async () => {
    const saved = { history_messages: 568, relayed_messages: 143, first_message_at: '2026-08-02T17:30:00.000Z', last_message_at: '2026-10-09T08:44:01.000Z', chat_ids: ['7671915466855582674'] };
    vi.stubGlobal('fetch', vi.fn().mockResolvedValueOnce(jsonResponse({ status: 'healthy', sources: [
      { key: 'liuzi10', tag: 'liuzi', chat_name: '六边形10', state: 'healthy', membership: 'member' },
      { key: 'paused', tag: 'paused', chat_name: '手动停用的群', state: 'disabled', enabled: false, membership: 'member' },
      { key: 'liuzi', tag: 'liuzi', chat_name: '六边形', state: 'left', membership: 'left', enabled: false, archived: true, left_at: '2026-10-12T08:35:52.227Z', saved },
      { key: 'fresh', tag: 'fresh', chat_name: '刚退的群', state: 'left', membership: 'left', enabled: true, archived: false, websocket_chat_ids: ['7000000000000000002'], saved: { history_messages: 0, relayed_messages: 0, first_message_at: null, last_message_at: null } },
    ] })));
    const workspace = useFeishuRelayWorkspace();
    await workspace.loadGroupRelayStatus();

    expect(workspace.activeSources.value.map((source) => source.key)).toEqual(['liuzi10', 'paused']);
    expect(workspace.leftSources.value.map((source) => source.key)).toEqual(['liuzi', 'fresh']);
    expect([...workspace.leftChatIds.value].sort()).toEqual(['7000000000000000002', '7671915466855582674']);
    const [kept, nothing] = workspace.leftSources.value;
    expect(workspace.savedContentText(kept)).toBe('群历史 568 条 · 已转发 143 条');
    // 17:30 UTC is already the next day in Beijing.
    expect(workspace.savedSpanText(kept)).toBe('2026-08-03 至 2026-10-09');
    expect(workspace.savedHasContent(nothing)).toBe(false);
    expect(workspace.savedContentText(nothing)).toBe('没有保存内容');
  });
});
