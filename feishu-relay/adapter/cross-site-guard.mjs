// The dashboard listener has no login: nginx only limits it to the tailnet.
// A web page an operator opens can still make their browser POST to it - for
// example a text/plain form whose body happens to be valid JSON, which
// readJsonBody accepts, forging a back-dated analyst message through
// /manual-relay.  Browsers label such requests: Sec-Fetch-Site says where the
// request came from, and Origin names the page.  Server-to-server callers
// (n8n, the LarkAgentX bridge, the quant alert path, Node fetch) send neither,
// so rejecting only what a browser marks as foreign keeps them working.

const SAFE_METHODS = new Set(['GET', 'HEAD', 'OPTIONS']);

function hostname(value) {
	const host = String(value ?? '').split(',')[0].trim();
	if (!host) return '';
	try {
		return new URL(`http://${host}`).hostname.toLowerCase();
	} catch {
		return '';
	}
}

/**
 * Why a state-changing request must be refused as cross-site, or null.
 * @param {{ method?: string, headers: Record<string, string | string[] | undefined> }} request
 */
export function crossSiteWriteRejection(request) {
	if (SAFE_METHODS.has(String(request.method ?? 'GET').toUpperCase())) return null;
	const headers = request.headers ?? {};
	const fetchSite = String(headers['sec-fetch-site'] ?? '').toLowerCase();
	// same-site covers another port on the same host (e.g. a UI on :5678).
	if (fetchSite === 'cross-site' || fetchSite === 'same-site') return 'cross_site_request';
	const origin = headers.origin;
	if (origin === undefined) return null;
	if (String(origin) === 'null') return 'opaque_origin';
	let originHost;
	try {
		originHost = new URL(String(origin)).hostname.toLowerCase();
	} catch {
		return 'invalid_origin';
	}
	// nginx forwards Host; X-Forwarded-Host wins when a proxy sets it.
	const requestHost = hostname(headers['x-forwarded-host'] ?? headers.host);
	return originHost && originHost === requestHost ? null : 'cross_origin_request';
}
