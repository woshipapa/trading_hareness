/** App HTTP boundary: configures the shared transport for the quant console.
 *
 * The transport implementation is the vendored shared module at
 * ../shared/http.ts (canonical: frontend-shared/src/http.ts); this file only
 * holds the app-specific configuration.
 */
import { createJsonClient, decodeJson } from '../shared/http';

export { decodeJson };

// Keep a small margin below the owner's async pool (max 16) for lease and
// health reads while allowing priority research panels to arrive promptly.
const client = createJsonClient({ maxConcurrentReads: 12 });

export const getJson = client.getJson;
export const postJson = client.postJson;
export const putJson = client.putJson;
export const deleteJson = client.deleteJson;
