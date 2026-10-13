import { afterEach, describe, expect, it, vi } from 'vitest';
import { nextTick, ref } from 'vue';
import { useDeliveryCheck } from './useDeliveryCheck';
import type { TargetRegistryRow } from '../api/target-registry';

const jsonResponse = (value: unknown, status = 200) => new Response(JSON.stringify(value), {
  status,
  headers: { 'content-type': 'application/json' },
});

const result = {
  counts: { matched: 2, unmatched: 1, recalled: 1, recall_failed: 0 },
  deliveries: [{
    delivery_id: 1, source_message_id: 'source-1', target_chat_id: 'wh_1', fingerprint: 'hash',
    sent_at: '2026-10-13T02:00:00.000Z', target_message_id: 'target-1', observed_at: '2026-10-13T02:00:01.000Z', recalled_at: null, recall_error: null,
  }],
};

afterEach(() => vi.unstubAllGlobals());

describe('useDeliveryCheck', () => {
  it('loads when a target is selected and refreshes the selected target path', async () => {
    const fetchMock = vi.fn().mockResolvedValue(jsonResponse(result));
    vi.stubGlobal('fetch', fetchMock);
    const selected = ref<TargetRegistryRow | null>(null);
    const check = useDeliveryCheck(selected);

    selected.value = { chat_id: 'wh/one', name: '目标群', keyword: '转发', origin: 'dashboard', usable: true };
    await nextTick();
    await vi.waitFor(() => expect(fetchMock).toHaveBeenCalledTimes(1));
    await vi.waitFor(() => expect(check.deliveries.value).toHaveLength(1));
    expect(fetchMock).toHaveBeenCalledWith('/api/group-relay/target-groups/wh%2Fone/deliveries', expect.any(Object));
    expect(check.counts.value).toEqual(result.counts);
    expect(check.deliveries.value).toEqual(result.deliveries);

    await check.load();
    expect(fetchMock).toHaveBeenCalledTimes(2);
  });

  it('keeps the last list when loading fails and exposes the server message', async () => {
    const fetchMock = vi.fn()
      .mockResolvedValueOnce(jsonResponse(result))
      .mockResolvedValueOnce(jsonResponse({ message: '网关不可用' }, 503));
    vi.stubGlobal('fetch', fetchMock);
    const selected = ref({ chat_id: 'wh_1', name: '目标群', keyword: null, origin: 'dashboard' as const, usable: true });
    const check = useDeliveryCheck(selected);
    await nextTick();
    await vi.waitFor(() => expect(check.deliveries.value).toHaveLength(1));
    const previous = check.deliveries.value;

    await check.load();
    expect(check.deliveries.value).toBe(previous);
    expect(check.error.value).toBe('网关不可用');
  });

  it('ignores a late answer from a target that is no longer selected', async () => {
    let resolveA: ((response: Response) => void) | undefined;
    let resolveB: ((response: Response) => void) | undefined;
    const fetchMock = vi.fn((path: string) => new Promise<Response>((resolve) => {
      if (path.endsWith('/wh_a/deliveries')) resolveA = resolve;
      else resolveB = resolve;
    }));
    vi.stubGlobal('fetch', fetchMock);
    const selected = ref<TargetRegistryRow | null>(null);
    const check = useDeliveryCheck(selected);
    selected.value = { chat_id: 'wh_a', name: '甲群', keyword: null, origin: 'dashboard', usable: true };
    await nextTick();
    selected.value = { chat_id: 'wh_b', name: '乙群', keyword: null, origin: 'dashboard', usable: true };
    await nextTick();

    resolveB?.(jsonResponse({ counts: { matched: 2, unmatched: 0, recalled: 0, recall_failed: 0 }, deliveries: [{ ...result.deliveries[0], target_chat_id: 'wh_b' }] }));
    await vi.waitFor(() => expect(check.deliveries.value[0]?.target_chat_id).toBe('wh_b'));
    resolveA?.(jsonResponse(result));
    await Promise.resolve();
    await Promise.resolve();
    expect(check.deliveries.value[0]?.target_chat_id).toBe('wh_b');
    expect(check.counts.value.matched).toBe(2);
  });
});
