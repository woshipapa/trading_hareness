import { afterEach, describe, expect, it, vi } from 'vitest';
import { useUrgentAlerts } from './useUrgentAlerts';

vi.mock('element-plus', () => ({
  ElMessage: { success: vi.fn(), error: vi.fn() },
  ElMessageBox: { confirm: vi.fn().mockResolvedValue('confirm') },
}));

const jsonResponse = (value: unknown, status = 200) => new Response(JSON.stringify(value), {
  status,
  headers: { 'content-type': 'application/json' },
});

afterEach(() => vi.unstubAllGlobals());

describe('useUrgentAlerts', () => {
  it('loads settings and recipients, and sends only saved settings', async () => {
    const fetchMock = vi.fn()
      .mockResolvedValueOnce(jsonResponse({ settings: { enabled: true, chat_id: 'chat-1', user_id: 'user-1', name: '张三' } }))
      .mockResolvedValueOnce(jsonResponse({ recipients: [{ chat_id: 'chat-1', user_id: 'user-1', name: '张三' }] }))
      .mockResolvedValueOnce(jsonResponse({ settings: { enabled: false, chat_id: 'chat-1', user_id: 'user-1', name: '张三' } }))
      .mockResolvedValueOnce(jsonResponse({ status: 'ok' }));
    vi.stubGlobal('fetch', fetchMock);
    const alerts = useUrgentAlerts();

    await alerts.load();
    expect(alerts.settings.value.enabled).toBe(true);
    expect(alerts.selectedRecipient.value).toBe('chat-1:user-1');

    alerts.settings.value.enabled = false;
    expect(alerts.dirty.value).toBe(true);
    await alerts.sendTest();
    expect(fetchMock).toHaveBeenCalledTimes(2);
    expect(alerts.error.value).toContain('请先保存');

    await alerts.save();
    await alerts.sendTest();
    expect(fetchMock).toHaveBeenNthCalledWith(3, '/api/group-relay/alerts/settings', expect.objectContaining({ method: 'PUT', body: JSON.stringify({ enabled: false, chat_id: 'chat-1', user_id: 'user-1', name: '张三' }) }));
    expect(fetchMock).toHaveBeenNthCalledWith(4, '/api/group-relay/alerts/test', expect.objectContaining({ method: 'POST', body: '{}' }));
  });

  it('keeps the draft when saving is refused and exposes the server message', async () => {
    const fetchMock = vi.fn()
      .mockResolvedValueOnce(jsonResponse({ settings: { enabled: false, chat_id: 'chat-1', user_id: 'user-1', name: '张三' } }))
      .mockResolvedValueOnce(jsonResponse({ recipients: [{ chat_id: 'chat-1', user_id: 'user-1', name: '张三' }] }))
      .mockResolvedValueOnce(jsonResponse({ message: '账号登录已失效' }, 422));
    vi.stubGlobal('fetch', fetchMock);
    const alerts = useUrgentAlerts();
    await alerts.load();
    alerts.settings.value.enabled = true;

    await alerts.save();

    expect(alerts.settings.value.enabled).toBe(true);
    expect(alerts.dirty.value).toBe(true);
    expect(alerts.error.value).toBe('账号登录已失效');
  });
});
