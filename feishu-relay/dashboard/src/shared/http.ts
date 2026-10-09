/** Typed JSON transport shared by the console frontends.
 *
 * Canonical source: frontend-shared/src/http.ts. Each app consumes a vendored
 * copy at src/shared/http.ts — edit the canonical file and run
 * `python3 scripts/sync_frontend_shared.py --apply`; never edit the copy.
 *
 * The adapter occasionally returns an HTML error page for a failed proxy
 * request. Decode it here so every feature reports a useful error instead of
 * leaking a raw JSON parser exception into the UI.
 */
export async function decodeJson<T>(response: Response, path: string): Promise<T> {
  const text = await response.text();
  const contentType = response.headers.get('content-type') ?? '';
  let data: unknown;
  try {
    data = text ? JSON.parse(text) : {};
  } catch {
    const preview = text.trim().replace(/\s+/g, ' ').slice(0, 120);
    throw new Error(`${path} 返回了非 JSON 响应（${contentType || '无 content-type'}）：${preview || '空响应'}`);
  }
  if (!response.ok) {
    const payload = data as { detail?: string; message?: string };
    throw new Error(payload.detail ?? payload.message ?? `HTTP ${response.status}`);
  }
  return data as T;
}

export interface JsonClient {
  getJson<T>(path: string, options?: { signal?: AbortSignal }): Promise<T>;
  postJson<T>(path: string, body?: Record<string, unknown>): Promise<T>;
  putJson<T>(path: string, body?: Record<string, unknown>): Promise<T>;
  deleteJson<T>(path: string): Promise<T>;
}

/** Build the app's JSON client.
 *
 * `maxConcurrentReads > 0` queues GET requests so a dashboard burst stays
 * below a backend's async pool; `0` (the default) issues reads immediately.
 */
export function createJsonClient(options: { maxConcurrentReads?: number } = {}): JsonClient {
  const limit = options.maxConcurrentReads ?? 0;
  let activeReads = 0;
  const pendingReads: Array<{
    task: () => Promise<unknown>;
    resolve: (value: unknown) => void;
    reject: (reason?: unknown) => void;
  }> = [];

  function pumpReadQueue(): void {
    while (activeReads < limit && pendingReads.length) {
      const next = pendingReads.shift();
      if (!next) return;
      activeReads += 1;
      void next.task().then(next.resolve, next.reject).finally(() => {
        activeReads -= 1;
        pumpReadQueue();
      });
    }
  }

  function scheduleRead<T>(task: () => Promise<T>): Promise<T> {
    if (limit <= 0) return task();
    return new Promise<T>((resolve, reject) => {
      pendingReads.push({ task, resolve: resolve as (value: unknown) => void, reject });
      pumpReadQueue();
    });
  }

  async function writeJson<T>(method: 'POST' | 'PUT', path: string, body: Record<string, unknown>): Promise<T> {
    return decodeJson<T>(await fetch(path, {
      method, headers: { 'content-type': 'application/json', accept: 'application/json' }, body: JSON.stringify(body),
    }), path);
  }

  return {
    getJson<T>(path: string, requestOptions: { signal?: AbortSignal } = {}): Promise<T> {
      return scheduleRead(async () => decodeJson<T>(await fetch(path, {
        headers: { accept: 'application/json' }, cache: 'no-store', signal: requestOptions.signal,
      }), path));
    },
    postJson<T>(path: string, body: Record<string, unknown> = {}): Promise<T> {
      return writeJson<T>('POST', path, body);
    },
    putJson<T>(path: string, body: Record<string, unknown> = {}): Promise<T> {
      return writeJson<T>('PUT', path, body);
    },
    async deleteJson<T>(path: string): Promise<T> {
      return decodeJson<T>(await fetch(path, { method: 'DELETE', headers: { accept: 'application/json' } }), path);
    },
  };
}
