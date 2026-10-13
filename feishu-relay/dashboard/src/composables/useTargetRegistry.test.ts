import { afterEach, describe, expect, it, vi } from 'vitest';
import { useTargetRegistry } from './useTargetRegistry';

const jsonResponse = (value: unknown, status = 200) => new Response(JSON.stringify(value), {
  status,
  headers: { 'content-type': 'application/json' },
});

afterEach(() => vi.unstubAllGlobals());

describe('useTargetRegistry', () => {
  it('requests the list and resolve endpoints', async () => {
    const fetchMock = vi.fn()
      .mockResolvedValueOnce(jsonResponse({ targets: [{ chat_id: 'oc_1', name: '一号群', keyword: '转发', origin: 'env', usable: true }] }))
      .mockResolvedValueOnce(jsonResponse({ targets: [{ chat_id: 'oc_2', name: '二号群', keyword: null, origin: 'dashboard', usable: true }] }));
    vi.stubGlobal('fetch', fetchMock);
    const registry = useTargetRegistry();

    await registry.load();
    await registry.resolve();

    expect(fetchMock).toHaveBeenNthCalledWith(1, '/api/group-relay/target-groups', expect.any(Object));
    expect(fetchMock).toHaveBeenNthCalledWith(2, '/api/group-relay/target-groups/resolve', expect.objectContaining({
      method: 'POST',
      body: '{}',
    }));
    expect(registry.targets.value[0]?.chat_id).toBe('oc_2');
  });

  it('keeps the list when resolve fails', async () => {
    const fetchMock = vi.fn()
      .mockResolvedValueOnce(jsonResponse({ targets: [{ chat_id: 'oc_1', name: '一号群', keyword: '转发', origin: 'env', usable: true }] }))
      .mockResolvedValueOnce(jsonResponse({ message: '网关不可用' }, 503));
    vi.stubGlobal('fetch', fetchMock);
    const registry = useTargetRegistry();

    await registry.load();
    await registry.resolve();

    expect(registry.targets.value).toEqual([{ chat_id: 'oc_1', name: '一号群', keyword: '转发', origin: 'env', usable: true }]);
    expect(registry.error.value).toBe('网关不可用');
  });
});
