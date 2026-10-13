import assert from 'node:assert/strict';
import test from 'node:test';
import { createTargetRegistry } from './target-registry.mjs';

const HASH = 'hash-for-env';
const NOW = new Date('2026-10-13T06:10:00Z');

function fakeLedger(initial = []) {
	const table = new Map(initial.map((row) => [row.chat_id, row]));
	return {
		table,
		async query(sql, params = []) {
			if (sql.startsWith('SELECT count')) return { rows: [{ count: table.size }] };
			if (sql.startsWith('SELECT chat_id')) return { rows: [...table.values()] };
			if (sql.startsWith('DELETE FROM') && !params.length) { table.clear(); return { rows: [] }; }
			if (sql.startsWith('DELETE FROM')) { table.delete(String(params[0])); return { rows: [] }; }
			if (sql.startsWith('INSERT')) {
				const [chat_id, websocket_chat_id, chat_name, bot_id, bot_name, owned, resolved_at] = params;
				table.set(chat_id, { chat_id, websocket_chat_id, chat_name, bot_id, bot_name, owned, resolved_at });
				return { rows: [] };
			}
			throw new Error(`unexpected query ${sql}`);
		},
	};
}

function fakeWebhooks() {
	return {
		list: () => [{ chat_id: 'oc_env', name: null, keyword: '汇总', origin: 'env', usable: true, managed: false, websocket_chat_id: null, verified_at: null, created_at: null }],
		webhookHashes: () => [{ chat_id: 'oc_env', webhook_hash: HASH }],
	};
}

function setup({ initial = [], bridgeAnswer = { groups: [] } } = {}) {
	const ledger = fakeLedger(initial);
	const calls = [];
	const bridgePost = async (...args) => { calls.push(args); return bridgeAnswer; };
	const registry = createTargetRegistry({ ledger, bridgePost, webhookTargets: fakeWebhooks(), now: () => NOW });
	return { ledger, calls, registry };
}

test('resolve matches requested hashes, keeps unmatched targets address-free, and names env targets', async () => {
	const { registry, calls } = setup({ bridgeAnswer: { groups: [{ chat_id: '7690000000000000001', name: '目标群', owned: true, bots: [{ bot_id: 'bot-1', name: '转发机器人', webhook_hash: HASH }, { bot_id: 'bot-2', name: '别的机器人' }] }] } });
	const targets = await registry.resolve();
	assert.deepEqual(calls[0][1], { webhook_hashes: [HASH] });
	assert.equal(targets[0].name, '目标群');
	assert.equal(targets[0].websocket_chat_id, '7690000000000000001');
	assert.equal(targets[0].bot_id, 'bot-1');
	assert.equal(JSON.stringify(targets).includes('webhook'), false);
});

test('record, forget, and resolve each notify listeners', async () => {
	const { registry } = setup({ bridgeAnswer: { groups: [{ chat_id: '7690000000000000001', name: '目标群', owned: true, bots: [{ bot_id: 'bot-1', name: '转发机器人', webhook_hash: HASH }] }] } });
	let changes = 0;
	registry.onChange(() => { changes += 1; });
	await registry.record({ chat_id: 'oc_env', websocket_chat_id: '7', chat_name: '群', bot_id: 'b', bot_name: '机器人', owned: true });
	await registry.forget('oc_env');
	await registry.resolve();
	assert.equal(changes, 3);
});

test('start resolves only an empty table and bridge failures are non-fatal', async () => {
	const populated = setup({ initial: [{ chat_id: 'oc_env', websocket_chat_id: '7', chat_name: '群', bot_id: 'b', bot_name: '机器人', owned: true, resolved_at: NOW }] });
	await populated.registry.start();
	assert.equal(populated.calls.length, 0);
	const empty = setup();
	empty.calls.push = Array.prototype.push;
	await empty.registry.start();
	assert.equal(empty.calls.length, 1);
	const failed = setup();
	const original = console.error;
	console.error = () => {};
	try {
		const registry = createTargetRegistry({ ledger: failed.ledger, bridgePost: async () => { throw new Error('bridge unavailable'); }, webhookTargets: fakeWebhooks(), now: () => NOW });
		await assert.doesNotReject(() => registry.start());
	} finally {
		console.error = original;
	}
});
