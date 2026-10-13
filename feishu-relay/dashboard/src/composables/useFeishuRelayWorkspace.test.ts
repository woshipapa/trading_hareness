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

  // One fetch stub for the whole page: the status the page shows, and every write it makes.
  function stubRelay(status: Record<string, unknown>) {
    const writes: { url: string; method: string; body: Record<string, unknown> }[] = [];
    vi.stubGlobal('fetch', vi.fn(async (url: string, init?: RequestInit) => {
      const method = init?.method ?? 'GET';
      if (method !== 'GET') writes.push({ url, method, body: JSON.parse(String(init?.body ?? '{}')) });
      if (url === '/api/group-relay/targets') {
        const body = JSON.parse(String(init?.body ?? '{}'));
        // No address in the request: the relay makes the bot itself.
        return jsonResponse({ target: { chat_id: 'wh_000000000000000000000001', name: '三叔外部同步群', keyword: body.keyword ?? '转发', origin: 'dashboard', usable: true, ...(body.webhook_url ? {} : { managed: true, bot_created: true }) } });
      }
      if (url.startsWith('/api/group-relay/targets/')) return jsonResponse({ status: 'removed', bot_removed: true });
      if (url.startsWith('/api/group-relay/routes')) return jsonResponse({ route: { key: 'r1', tag: 'rccs', chat_name: '新群', state: 'healthy' } });
      if (url.startsWith('/api/group-relay/chats')) return jsonResponse({ observed_at: '', live: true, error: null, groups: [], sync: {} });
      return jsonResponse(status);
    }));
    return writes;
  }
  const relayStatus = {
    status: 'healthy', sources: [{ key: 'cat', tag: 'cat', chat_name: '猫先生', state: 'healthy', target_chat_ids: ['oc_summary'] }],
    source_tags: [{ tag: 'liwei', registered: true, enabled: true, origin: 'file', label: '立伟' }, { tag: 'rccs', registered: true, enabled: true, origin: 'dashboard', label: 'RCCS' }],
    webhook_targets: [
      { chat_id: 'oc_summary', name: null, keyword: '汇总', origin: 'env', usable: true },
      { chat_id: 'wh_00000000000000000000000a', name: '锦鲤同步群', keyword: 'jinli', origin: 'dashboard', usable: true },
      { chat_id: 'wh_00000000000000000000000b', name: '要重填的群', keyword: null, origin: 'dashboard', usable: false },
    ],
  };

  it('registers a tag never used before when a source group is saved, unless the box is cleared', async () => {
    const writes = stubRelay(relayStatus);
    const workspace = useFeishuRelayWorkspace();
    await workspace.loadGroupRelayStatus();

    workspace.openCreateGroupRelayRoute();
    workspace.groupRelayRouteForm.value.chat_name = '新群';
    workspace.groupRelayRouteForm.value.tag = '#NewTag';
    expect(workspace.routeIngestLocked.value).toBe(false);
    expect(workspace.routeIngestHint.value).toBe('#newtag 是新标签，保存时自动登记，不需要发版。');
    await workspace.saveGroupRelayRoute();
    expect(writes.at(-1)).toMatchObject({ url: '/api/group-relay/routes', method: 'POST', body: { tag: '#NewTag', ingest: true } });

    // A tag the registry file declares always feeds downstream, whatever the box says.
    workspace.openCreateGroupRelayRoute();
    Object.assign(workspace.groupRelayRouteForm.value, { chat_name: '立伟新群', tag: 'liwei', ingest: false });
    expect(workspace.routeIngestLocked.value).toBe(true);
    await workspace.saveGroupRelayRoute();
    expect(writes.at(-1)?.body.ingest).toBe(true);

    // Editing a relay-only route keeps it relay-only.
    workspace.openEditGroupRelayRoute(workspace.groupRelayStatus.value.sources![0]);
    expect(workspace.groupRelayRouteForm.value.ingest).toBe(false);
    expect(workspace.routeIngestHint.value).toBe('只转发到目标群，不进入下游研究数据。');
    await workspace.saveGroupRelayRoute();
    expect(writes.at(-1)).toMatchObject({ url: '/api/group-relay/routes/cat', method: 'PUT', body: { tag: 'cat', ingest: false } });
    expect(workspace.tagFeedsDownstream('rccs')).toBe(true);
    expect(workspace.tagFeedsDownstream('cat')).toBe(false);
  });

  it('adds a target group from the page and selects it in the open source-group form', async () => {
    const writes = stubRelay(relayStatus);
    const workspace = useFeishuRelayWorkspace();
    await workspace.loadGroupRelayStatus();
    // Only targets whose address is loaded can be picked; a named one is shown by its name.
    expect(workspace.relayTargetOptions.value).toEqual([
      { value: 'oc_summary', label: '汇总（…ummary）' },
      { value: 'wh_00000000000000000000000a', label: '锦鲤同步群（jinli · …00000a）' },
    ]);
    expect(workspace.relayTargetName('wh_00000000000000000000000a')).toBe('锦鲤同步群');
    expect(workspace.relayTargetName('oc_summary')).toBe('oc_summary');

    workspace.openCreateGroupRelayRoute();
    workspace.openTargetDialog();
    // The dialog opens in the automatic mode; a typed address belongs to the manual one.
    expect(workspace.targetForm.value.mode).toBe('auto');
    Object.assign(workspace.targetForm.value, { mode: 'manual', name: ' 三叔外部同步群 ', webhook_url: ' https://open.feishu.cn/open-apis/bot/v2/hook/abcdefgh ', keyword: ' sanshu ' });
    await workspace.saveRelayTarget();

    expect(writes.at(-1)).toEqual({ url: '/api/group-relay/targets', method: 'POST', body: { name: '三叔外部同步群', webhook_url: 'https://open.feishu.cn/open-apis/bot/v2/hook/abcdefgh', keyword: 'sanshu' } });
    expect(workspace.targetDialog.value).toBe(false);
    // The address does not stay in the page after it is saved.
    expect(workspace.targetForm.value.webhook_url).toBe('');
    expect(workspace.groupRelayRouteForm.value.target_chat_ids).toEqual(['wh_000000000000000000000001']);
  });

  it('creates the target group\'s bot itself when a joined group is picked and no address is typed', async () => {
    const writes = stubRelay(relayStatus);
    const workspace = useFeishuRelayWorkspace();
    await workspace.loadGroupRelayStatus();

    // Without a group from the joined list there is no group to add a bot to: nothing is sent.
    workspace.openTargetDialog();
    workspace.targetForm.value.name = '账号不在的群';
    await workspace.saveRelayTarget();
    expect(writes).toEqual([]);
    expect(workspace.targetDialog.value).toBe(true);

    workspace.joinedGroups.value = [{ chat_id: '7690000000000000001', name: '三叔外部同步群', member_count: 6, in_directory: true, source_key: null, route_tag: null, membership: 'member', left_at: null, is_new: false, first_seen_at: null, history: null }];
    workspace.pickTargetGroup('7690000000000000001');
    await workspace.saveRelayTarget();
    // Only the group goes out: no address, and no keyword unless one was typed.
    expect(writes.at(-1)).toEqual({ url: '/api/group-relay/targets', method: 'POST', body: { websocket_chat_id: '7690000000000000001', name: '三叔外部同步群' } });
    expect(workspace.targetDialog.value).toBe(false);

    // Reconfiguring a relay-made target stays automatic; a typed one stays manual.
    workspace.openTargetDialog({ chat_id: 'wh_a', name: '自动的', keyword: '转发', origin: 'dashboard', usable: true, managed: true, websocket_chat_id: '7690000000000000001' });
    expect(workspace.targetForm.value.mode).toBe('auto');
    workspace.openTargetDialog({ chat_id: 'wh_b', name: '手动的', keyword: 'x', origin: 'dashboard', usable: true });
    expect(workspace.targetForm.value.mode).toBe('manual');
  });

  it('reloads joined groups and selects a group joined by link', async () => {
    vi.stubGlobal('fetch', vi.fn().mockResolvedValueOnce(jsonResponse({
      observed_at: '', live: true, error: null,
      groups: [{ chat_id: 'oc_joined', name: '链接群', member_count: 2, in_directory: true, source_key: null, route_tag: null, membership: 'member', left_at: null, is_new: false, first_seen_at: null, history: null }],
      sync: {},
    })));
    const workspace = useFeishuRelayWorkspace();

    await workspace.onJoinedByLink('oc_joined');

    expect(workspace.groupRelayRouteForm.value.websocket_chat_id).toBe('oc_joined');
    expect(workspace.groupRelayRouteForm.value.chat_name).toBe('链接群');
  });
});
