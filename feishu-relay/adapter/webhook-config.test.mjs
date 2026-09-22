import assert from 'node:assert/strict';
import test from 'node:test';
import { parseRelayMap, webhookConfigStatus } from './webhook-config.mjs';

test('relay map preserves values containing equals signs', () => {
	const parsed = parseRelayMap('oc_a=https://example.test/hook?a=b;oc_b=汇总');
	assert.deepEqual([...parsed.entries()], [['oc_a', 'https://example.test/hook?a=b'], ['oc_b', '汇总']]);
});

test('webhook status exposes exact target and keyword coverage without URLs', () => {
	const status = webhookConfigStatus(
		new Map([['oc_anqiang', 'https://example.test/a'], ['oc_summary', 'https://example.test/s']]),
		new Map([['oc_summary', '汇总']]),
	);
	assert.deepEqual(status.webhook_chat_ids, ['oc_anqiang', 'oc_summary']);
	assert.deepEqual(status.keyword_entries, [{ chat_id: 'oc_summary', keyword: '汇总' }]);
	assert.deepEqual(status.missing_keyword_chat_ids, ['oc_anqiang']);
	assert.deepEqual(status.orphan_keyword_chat_ids, []);
	assert.equal(status.all_webhook_keywords_loaded, false);
});
