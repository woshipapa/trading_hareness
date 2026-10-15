import { describe, expect, it, vi } from 'vitest';

import { decodeJson, deleteJson, getJson, postJson, putJson } from './http';

describe('frontend HTTP boundary', () => {
  it('returns valid JSON responses', async () => {
    const response = new Response(JSON.stringify({ status: 'ok' }), {
      headers: { 'content-type': 'application/json' },
    });

    await expect(decodeJson<{ status: string }>(response, '/health')).resolves.toEqual({ status: 'ok' });
  });

  it('turns an HTML proxy response into a readable error instead of JSON parse noise', async () => {
    const response = new Response('<!doctype html><title>gateway unavailable</title>', {
      status: 502,
      statusText: 'Bad Gateway',
      headers: { 'content-type': 'text/html' },
    });

    await expect(decodeJson(response, '/api/research/overview')).rejects.toThrow(
      /非 JSON.*gateway unavailable/i,
    );
  });

  it('uses the shared JSON transport for every JSON HTTP method', async () => {
    const fetchMock = vi.fn()
      .mockResolvedValueOnce(new Response(JSON.stringify({ value: 1 }), { headers: { 'content-type': 'application/json' } }))
      .mockResolvedValueOnce(new Response(JSON.stringify({ accepted: true }), { headers: { 'content-type': 'application/json' } }))
      .mockResolvedValueOnce(new Response(JSON.stringify({ updated: true }), { headers: { 'content-type': 'application/json' } }))
      .mockResolvedValueOnce(new Response(JSON.stringify({ deleted: true }), { headers: { 'content-type': 'application/json' } }));
    vi.stubGlobal('fetch', fetchMock);

    await expect(getJson<{ value: number }>('/api/example')).resolves.toEqual({ value: 1 });
    await expect(postJson<{ accepted: boolean }>('/api/example', { mode: 'safe' })).resolves.toEqual({ accepted: true });
    await expect(putJson<{ updated: boolean }>('/api/example', { enabled: true })).resolves.toEqual({ updated: true });
    await expect(deleteJson<{ deleted: boolean }>('/api/example')).resolves.toEqual({ deleted: true });
    expect(fetchMock).toHaveBeenNthCalledWith(1, '/api/example', expect.objectContaining({ headers: expect.objectContaining({ accept: 'application/json' }) }));
    expect(fetchMock).toHaveBeenNthCalledWith(2, '/api/example', expect.objectContaining({ method: 'POST', body: JSON.stringify({ mode: 'safe' }) }));
    expect(fetchMock).toHaveBeenNthCalledWith(3, '/api/example', expect.objectContaining({ method: 'PUT', body: JSON.stringify({ enabled: true }) }));
    expect(fetchMock).toHaveBeenNthCalledWith(4, '/api/example', expect.objectContaining({ method: 'DELETE' }));
  });

  it('forwards an abort signal so inactive dashboard sections can cancel heavy reads', async () => {
    const controller = new AbortController();
    const fetchMock = vi.fn().mockResolvedValue(
      new Response(JSON.stringify({ status: 'ok' }), { headers: { 'content-type': 'application/json' } }),
    );
    vi.stubGlobal('fetch', fetchMock);

    await getJson('/api/research/overview', { signal: controller.signal });

    expect(fetchMock).toHaveBeenCalledWith(
      '/api/research/overview',
      expect.objectContaining({ signal: controller.signal }),
    );
  });

  it('coalesces identical in-flight reads and releases the entry after settling', async () => {
    let release: ((response: Response) => void) | undefined;
    const response = new Response(JSON.stringify({ value: 1 }), { headers: { 'content-type': 'application/json' } });
    const fetchMock = vi.fn()
      .mockImplementationOnce(() => new Promise<Response>((resolve) => { release = resolve; }))
      .mockResolvedValueOnce(response);
    vi.stubGlobal('fetch', fetchMock);

    const first = getJson<{ value: number }>('/api/repeated-status');
    const second = getJson<{ value: number }>('/api/repeated-status');
    expect(fetchMock).toHaveBeenCalledTimes(1);

    release?.(new Response(JSON.stringify({ value: 1 }), { headers: { 'content-type': 'application/json' } }));
    await expect(Promise.all([first, second])).resolves.toEqual([{ value: 1 }, { value: 1 }]);

    await expect(getJson<{ value: number }>('/api/repeated-status')).resolves.toEqual({ value: 1 });
    expect(fetchMock).toHaveBeenCalledTimes(2);
  });
});
