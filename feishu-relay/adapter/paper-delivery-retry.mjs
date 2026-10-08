const TRANSIENT_HTTP_STATUS = new Set([408, 425, 429, 500, 502, 503, 504]);
const PERMANENT_HTTP_STATUS = new Set([400, 401, 403, 404, 409, 422]);

function statusFromError(error) {
	const candidates = [error?.status, error?.statusCode, error?.response?.status, error?.response?.statusCode];
	for (const value of candidates) {
		const status = Number(value);
		if (Number.isInteger(status) && status > 0) return status;
	}
	return 0;
}

export function isRetryableDeliveryError(error) {
	if (error?.name === 'AbortError') return true;
	const status = statusFromError(error);
	if (TRANSIENT_HTTP_STATUS.has(status)) return true;
	if (PERMANENT_HTTP_STATUS.has(status)) return false;
	const message = String(error?.message ?? error ?? '').toLowerCase();
	return /econnreset|econnrefused|etimedout|eai_again|enotfound|socket|timeout|temporar|rate.?limit|network|fetch failed|aborted/.test(message);
}

export function safeDeliveryError(error, limit = 500) {
	const status = statusFromError(error);
	let message = String(error?.message ?? error ?? 'delivery failed')
		.replace(/https?:\/\/[^\s]+/gi, '[url]')
		.replace(/(token|secret|authorization|cookie)\s*[=:]\s*[^\s,;]+/gi, '$1=[redacted]')
		.replace(/[\r\n\t]+/g, ' ')
		.trim();
	if (status && !/^\d{3}\b/.test(message)) message = `HTTP ${status}: ${message}`;
	return message.slice(0, Math.max(80, Number(limit) || 500));
}

export async function retryDelivery(operation, {
	maxAttempts = 3,
	delaysMs = [300, 1000],
	sleep = (milliseconds) => new Promise((resolve) => setTimeout(resolve, milliseconds)),
	shouldRetry = isRetryableDeliveryError,
} = {}) {
	const attempts = Math.max(1, Math.min(5, Number(maxAttempts) || 3));
	let lastError;
	for (let attempt = 1; attempt <= attempts; attempt += 1) {
		try {
			return await operation(attempt);
		} catch (error) {
			lastError = error;
			if (attempt >= attempts || !shouldRetry(error)) throw error;
			await sleep(Math.max(0, Number(delaysMs[attempt - 1] ?? delaysMs.at(-1) ?? 1000) || 0));
		}
	}
	throw lastError ?? new Error('delivery failed');
}

export async function postWebhookWithRetry(url, payload, {
	fetchImpl = fetch,
	timeoutMs = 15_000,
	...retryOptions
} = {}) {
	return retryDelivery(async () => {
		const controller = new AbortController();
		const timer = setTimeout(() => controller.abort(), Math.max(1_000, Number(timeoutMs) || 15_000));
		try {
			const response = await fetchImpl(url, {
				method: 'POST',
				headers: { 'content-type': 'application/json' },
				body: JSON.stringify(payload),
				signal: controller.signal,
			});
			if (!response?.ok) {
				const error = new Error(`webhook responded ${response?.status ?? 'unknown'}`);
				error.status = Number(response?.status) || 0;
				throw error;
			}
			return response.status;
		} finally {
			clearTimeout(timer);
		}
	}, retryOptions);
}
