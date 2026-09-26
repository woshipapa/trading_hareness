import assert from 'node:assert/strict';
import test from 'node:test';
import { shouldRedownloadRetryMedia } from './retry-media.mjs';

const event = { message: { message_id: 'om_source' } };

test('reuses an intact local resource before retrying it', () => {
	assert.equal(shouldRedownloadRetryMedia({ expectedResourceCount: 1, event, localResourcesAvailable: true }), false);
	assert.equal(shouldRedownloadRetryMedia({ expectedResourceCount: 1, event, localResourcesAvailable: false }), true);
});

test('does not try to redownload manual or text-only retries', () => {
	assert.equal(shouldRedownloadRetryMedia({ expectedResourceCount: 1, event: null }), false);
	assert.equal(shouldRedownloadRetryMedia({ expectedResourceCount: 0, event }), false);
});
