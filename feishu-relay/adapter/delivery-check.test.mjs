import assert from 'node:assert/strict';
import test from 'node:test';
import { createDeliveryCheck } from './delivery-check.mjs';

const NOW = new Date('2026-10-13T10:00:00.000Z');

class FakeLedger {
	constructor() {
		this.rows = [];
		this.nextId = 1;
	}

	async query(text, params = []) {
		if (text.startsWith('INSERT INTO feishu_group_relay_target_deliveries')) {
			this.rows.push({ delivery_id: this.nextId++, source_message_id: params[0], target_chat_id: params[1], fingerprint: params[2], sent_at: params[3], target_message_id: null, observed_at: null, recalled_at: null, recall_error: null });
			return { rows: [] };
		}
		if (text.includes('WHERE target_message_id=$1 LIMIT 1')) {
			return { rows: this.rows.filter((row) => row.target_message_id === params[0]).slice(0, 1) };
		}
		if (text.includes('target_message_id IS NULL AND sent_at >= $2 AND sent_at <= $3 AND fingerprint=$4')) {
			return { rows: this.rows.filter((row) => row.target_chat_id === params[0] && row.target_message_id === null && row.sent_at >= params[1] && row.sent_at <= params[2] && row.fingerprint === params[3]).map(({ delivery_id }) => ({ delivery_id })) };
		}
		if (text.includes('SET target_message_id=$2,observed_at=$3')) {
			const row = this.rows.find((item) => item.delivery_id === params[0] && item.target_message_id === null);
			if (row) [row.target_message_id, row.observed_at] = [params[1], params[2]];
			return { rows: [] };
		}
		if (text.includes('WHERE source_message_id=$1 AND target_message_id IS NOT NULL')) {
			return { rows: this.rows.filter((row) => row.source_message_id === params[0] && row.target_message_id && !row.recalled_at && !row.recall_error).map((row) => ({ delivery_id: row.delivery_id, target_chat_id: row.target_chat_id, target_message_id: row.target_message_id })) };
		}
		if (text.includes('SET recalled_at=$2')) {
			this.rows.find((row) => row.delivery_id === params[0]).recalled_at = params[1];
			return { rows: [] };
		}
		if (text.includes('SET recall_error=$2')) {
			this.rows.find((row) => row.delivery_id === params[0]).recall_error = params[1];
			return { rows: [] };
		}
		throw new Error(`unexpected query: ${text}`);
	}
}

function registry(targets) {
	let listener = null;
	return {
		list: async () => targets,
		get: async (chatId) => targets.find((target) => target.chat_id === String(chatId)) ?? null,
		onChange(callback) { listener = callback; },
		changed: () => listener(),
	};
}

function setup(targets = [{ chat_id: 'wh_one', websocket_chat_id: '7001', bot_id: 'bot-1', keyword: '自测', owned: true }], bridgePost = async () => ({})) {
	const ledger = new FakeLedger();
	const targetRegistry = registry(targets);
	const feature = createDeliveryCheck({ ledger, bridgePost, targetRegistry, now: () => new Date(NOW) });
	const observed = feature.routes.find((route) => route.path === '/internal/larkagentx/target-observed').handle;
	return { feature, ledger, targetRegistry, observed };
}

test('records only checked webhook forms', async () => {
	const { feature, ledger } = setup();
	await feature.recordWebhookDelivery({ messageId: 'source-post', targetChatId: 'wh_one', msgType: 'post', content: { zh_cn: {} } });
	assert.equal(ledger.rows.length, 0);
	await feature.recordWebhookDelivery({ messageId: 'source-text', targetChatId: 'wh_one', msgType: 'text', content: { text: '正文\n自测' } });
	assert.equal(ledger.rows.length, 1);
});

test('one fingerprint match is written, while zero or two matches are not', async () => {
	const one = setup();
	await one.feature.recordWebhookDelivery({ messageId: 'source-one', targetChatId: 'wh_one', msgType: 'text', content: { text: '正 文\n自测' } });
	assert.deepEqual(await one.observed({ body: { chat_id: '7001', message_id: 'target-one', text: '自测 正文' } }), { status: 'ok', matched: true });
	assert.equal(one.ledger.rows[0].target_message_id, 'target-one');

	const zero = setup();
	await zero.feature.recordWebhookDelivery({ messageId: 'source-zero', targetChatId: 'wh_one', msgType: 'text', content: { text: '别的正文\n自测' } });
	assert.equal((await zero.observed({ body: { chat_id: '7001', message_id: 'target-zero', text: '自测 正文' } })).matched, false);
	assert.equal(zero.ledger.rows[0].target_message_id, null);

	const two = setup();
	await two.feature.recordWebhookDelivery({ messageId: 'source-a', targetChatId: 'wh_one', msgType: 'text', content: { text: '正文\n自测' } });
	await two.feature.recordWebhookDelivery({ messageId: 'source-b', targetChatId: 'wh_one', msgType: 'text', content: { text: '自测 正文' } });
	assert.equal((await two.observed({ body: { chat_id: '7001', message_id: 'target-two', text: '正文 自测' } })).matched, false);
	assert.deepEqual(two.ledger.rows.map((row) => row.target_message_id), [null, null]);
});

test('a replayed target message id changes nothing', async () => {
	const harness = setup();
	await harness.feature.recordWebhookDelivery({ messageId: 'source-one', targetChatId: 'wh_one', msgType: 'text', content: { text: '正文 自测' } });
	await harness.observed({ body: { chat_id: '7001', message_id: 'target-one', text: '正文 自测' } });
	await harness.feature.recordWebhookDelivery({ messageId: 'source-two', targetChatId: 'wh_one', msgType: 'text', content: { text: '正文 自测' } });
	assert.equal((await harness.observed({ body: { chat_id: '7001', message_id: 'target-one', text: '正文 自测' } })).matched, false);
	assert.equal(harness.ledger.rows[1].target_message_id, null);
});

test('source recall touches only matched owned targets and never retries a failure', async () => {
	const targets = [
		{ chat_id: 'owned', websocket_chat_id: '7001', owned: true },
		{ chat_id: 'failed', websocket_chat_id: '7002', owned: true },
		{ chat_id: 'not-owned', websocket_chat_id: '7003', owned: false },
	];
	const calls = [];
	const harness = setup(targets, async (path, body) => {
		calls.push([path, body]);
		if (body.chat_id === '7002') throw new Error('bridge down');
	});
	harness.ledger.rows.push(
		{ delivery_id: 1, source_message_id: 'source', target_chat_id: 'owned', target_message_id: 'target-ok', recalled_at: null, recall_error: null },
		{ delivery_id: 2, source_message_id: 'source', target_chat_id: 'failed', target_message_id: 'target-fail', recalled_at: null, recall_error: null },
		{ delivery_id: 3, source_message_id: 'source', target_chat_id: 'not-owned', target_message_id: 'target-no', recalled_at: null, recall_error: null },
		{ delivery_id: 4, source_message_id: 'source', target_chat_id: 'owned', target_message_id: null, recalled_at: null, recall_error: null },
	);
	await harness.feature.recallDeliveries('source');
	await harness.feature.recallDeliveries('source');
	assert.deepEqual(calls, [
		['/messages/recall-in-group', { chat_id: '7001', message_id: 'target-ok' }],
		['/messages/recall-in-group', { chat_id: '7002', message_id: 'target-fail' }],
	]);
	assert.ok(harness.ledger.rows[0].recalled_at);
	assert.equal(harness.ledger.rows[1].recall_error, 'bridge down');
	assert.equal(harness.ledger.rows[2].recalled_at, null);
});

test('start sends the observed list again on registry changes and bridge failures do not throw', async () => {
	const calls = [];
	const harness = setup([
		{ chat_id: 'one', websocket_chat_id: '7001', bot_id: 'bot-1' },
		{ chat_id: 'no-bot', websocket_chat_id: '7002', bot_id: null },
		{ chat_id: 'official', websocket_chat_id: 'oc_group', bot_id: 'bot-2' },
	], async (...args) => { calls.push(args); });
	await harness.feature.start();
	await harness.targetRegistry.changed();
	assert.deepEqual(calls.map((call) => call.slice(0, 2)), [
		['/targets/observe', { chats: [{ chat_id: '7001', bot_ids: ['bot-1'] }] }],
		['/targets/observe', { chats: [{ chat_id: '7001', bot_ids: ['bot-1'] }] }],
	]);

	const original = console.error;
	console.error = () => {};
	try {
		const failed = setup(undefined, async () => { throw new Error('bridge unavailable'); });
		await assert.doesNotReject(() => failed.feature.start());
		await assert.doesNotReject(() => failed.targetRegistry.changed());
	} finally {
		console.error = original;
	}
});
