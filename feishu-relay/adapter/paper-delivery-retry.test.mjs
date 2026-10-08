import assert from 'node:assert/strict';
import test from 'node:test';
import { isRetryableDeliveryError, postWebhookWithRetry, retryDelivery, safeDeliveryError } from './paper-delivery-retry.mjs';

test('retries transient delivery failures with the same operation contract', async () => {
	let calls = 0;
	const result = await retryDelivery(async (attempt) => {
		calls += 1;
		assert.equal(attempt, calls);
		if (calls < 3) {
			const error = new Error('rate limited');
			error.status = 429;
			throw error;
		}
		return 'ok';
	}, { sleep: async () => {} });
	assert.equal(result, 'ok');
	assert.equal(calls, 3);
});

test('does not retry permanent Feishu errors', async () => {
	let calls = 0;
	await assert.rejects(
		retryDelivery(async () => {
			calls += 1;
			const error = new Error('permission denied');
			error.status = 403;
			throw error;
		}, { sleep: async () => {} }),
		/permission denied/,
	);
	assert.equal(calls, 1);
	assert.equal(isRetryableDeliveryError(Object.assign(new Error('bad gateway'), { status: 502 })), true);
	assert.equal(isRetryableDeliveryError(Object.assign(new Error('request aborted'), { name: 'AbortError' })), true);
});

test('webhook forwarding retries transient responses and sends JSON', async () => {
	let calls = 0;
	let captured;
	const status = await postWebhookWithRetry('http://example.test/paper-ingest', { ids: '2609.30059v1', source_event_id: 'evt-1' }, {
		fetchImpl: async (_url, options) => {
			calls += 1;
			captured = JSON.parse(options.body);
			if (calls === 1) return { ok: false, status: 502 };
			return { ok: true, status: 200 };
		},
		sleep: async () => {},
	});
	assert.equal(status, 200);
	assert.equal(calls, 2);
	assert.deepEqual(captured, { ids: '2609.30059v1', source_event_id: 'evt-1' });
});

test('safe delivery error strips URLs and credentials', () => {
	const error = new Error('POST https://example.test/hook token=secret-value failed');
	assert.equal(safeDeliveryError(error), 'POST [url] token=[redacted] failed');
});
