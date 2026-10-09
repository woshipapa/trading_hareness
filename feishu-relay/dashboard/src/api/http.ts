/** App HTTP boundary: the Feishu dashboard uses the shared transport as-is.
 *
 * The transport implementation is the vendored shared module at
 * ../shared/http.ts (canonical: frontend-shared/src/http.ts); this file only
 * holds the app-specific configuration. The adapter is same-origin and light,
 * so no read-concurrency cap is configured here.
 */
import { createJsonClient, decodeJson } from '../shared/http';

export { decodeJson };

const client = createJsonClient();

export const getJson = client.getJson;
export const postJson = client.postJson;
export const putJson = client.putJson;
export const deleteJson = client.deleteJson;
