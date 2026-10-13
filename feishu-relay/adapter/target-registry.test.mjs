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
			if (sql.startsWith('SELECT count')) return { rows: [{ count: [...table.values()].filter((row) => row.chat_id === row.websocket_chat_id).length }] };
			if (sql.startsWith('SELECT chat_id')) return { rows: [...table.values()] };
			if (sql.startsWith('DELETE FROM') && sql.includes('chat_id = websocket_chat_id')) { for (const row of [...table.values()]) if (row.chat_id === row.websocket_chat_id && !params[0].includes(row.chat_id)) table.delete(row.chat_id); return { rows: [] }; }
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

test('a failing listener is isolated from registry writes', async () => {
	const { registry } = setup({ bridgeAnswer: { groups: [{ chat_id: '7690000000000000001', name: '目标群', owned: true, bots: [{ bot_id: 'bot-1', name: '转发机器人', webhook_hash: HASH }] }] } });
	registry.onChange(async () => { throw new Error('listener failed'); });
	await assert.doesNotReject(() => registry.record({ chat_id: 'oc_env', websocket_chat_id: '7', chat_name: '群', bot_id: 'b', bot_name: '机器人', owned: true }));
	await assert.doesNotReject(() => registry.forget('oc_env'));
	await assert.doesNotReject(() => registry.resolve());
});

test('start resolves only while no owned group is known and bridge failures are non-fatal', async () => {
	const targetRow = { chat_id: 'oc_env', websocket_chat_id: '7', chat_name: '群', bot_id: 'b', bot_name: '机器人', owned: true, resolved_at: NOW };
	const populated = setup({ initial: [targetRow, { chat_id: '7', websocket_chat_id: '7', chat_name: '群', bot_id: null, bot_name: null, owned: true, resolved_at: NOW }] });
	await populated.registry.start();
	assert.equal(populated.calls.length, 0);
	const targetsOnly = setup({ initial: [targetRow] });
	await targetsOnly.registry.start();
	assert.equal(targetsOnly.calls.length, 1);
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

const OWNED = { chat_id: '7680000000000000002', name: '擒龙群', owned: true, bots: [] };
const TARGET_GROUP = { chat_id: '7690000000000000001', name: '目标群', owned: true, bots: [{ bot_id: 'bot-1', name: '转发机器人', webhook_hash: HASH }] };

test('groups lists the targets, then every other group the account owns, and get finds both', async () => {
	const { registry } = setup({ bridgeAnswer: { groups: [OWNED, TARGET_GROUP, { chat_id: '7680000000000000003', name: '别人的群', owned: false, bots: [] }] } });
	const groups = await registry.resolve();
	assert.deepEqual(groups.map((row) => [row.chat_id, row.name, row.origin, row.usable, row.owned]), [
		['oc_env', '目标群', 'env', true, true],
		['7680000000000000002', '擒龙群', 'owned', false, true],
	]);
	assert.deepEqual((await registry.list()).map((row) => row.chat_id), ['oc_env']);
	assert.equal((await registry.get('7680000000000000002')).websocket_chat_id, '7680000000000000002');
	assert.equal(await registry.get('7690000000000000001'), null);
	assert.equal(await registry.get('7680000000000000003'), null);
});

test('a group the account no longer owns leaves the list at the next resolve', async () => {
	const answer = { groups: [OWNED, TARGET_GROUP] };
	const { registry } = setup({ bridgeAnswer: answer });
	await registry.resolve();
	answer.groups = [TARGET_GROUP];
	assert.deepEqual((await registry.resolve()).map((row) => row.chat_id), ['oc_env']);
});

test('a target added to an owned group takes the place of the group row and inherits its owner before the next resolve', async () => {
	const ledger = fakeLedger();
	const webhooks = fakeWebhooks();
	const registry = createTargetRegistry({ ledger, bridgePost: async () => ({ groups: [OWNED] }), webhookTargets: webhooks, now: () => NOW });
	await registry.resolve();
	const base = webhooks.list;
	webhooks.list = () => [...base(), { chat_id: 'wh_new', name: '擒龙群', keyword: '提醒', origin: 'dashboard', usable: true, managed: true, websocket_chat_id: OWNED.chat_id, verified_at: null, created_at: null }];
	const groups = await registry.groups();
	assert.deepEqual(groups.map((row) => row.chat_id), ['oc_env', 'wh_new']);
	assert.equal(groups[1].owned, true);
});

test('start resolves until the owned groups are known, trying again after a failure', async () => {
	let fail = true;
	const calls = [];
	const registry = createTargetRegistry({ ledger: fakeLedger(), bridgePost: async (...args) => { calls.push(args); if (fail) throw new Error('bridge down'); return { groups: [OWNED] }; }, webhookTargets: fakeWebhooks(), now: () => NOW });
	const original = console.error;
	console.error = () => {};
	try {
		await registry.start({ retryMs: 5, retries: 20 });
		assert.equal(calls.length, 1);
		fail = false;
		await new Promise((resolve) => setTimeout(resolve, 40));
	} finally { console.error = original; }
	assert.equal((await registry.groups()).some((row) => row.origin === 'owned'), true);
	const settled = calls.length;
	await new Promise((resolve) => setTimeout(resolve, 30));
	assert.equal(calls.length, settled);
});

test('an owned group lists its existing bots and takes one as its relay bot; a group that is a target already does not', async () => {
	const ledger = fakeLedger();
	const webhooks = fakeWebhooks();
	const calls = [];
	const bridgePost = async (route, body) => { calls.push([route, body]); return route === '/targets/resolve' ? { groups: [OWNED] } : { chat_id: body.chat_id, bots: [{ bot_id: '41', name: 'dragon', key_words: [], usable: true, reason: null }] }; };
	const base = webhooks.list;
	const added = [];
	webhooks.addExisting = async (input) => { added.push(input); webhooks.list = () => [...base(), { chat_id: 'wh_new', name: input.name, keyword: '', origin: 'dashboard', usable: true, managed: false, websocket_chat_id: input.websocketChatId, verified_at: null, created_at: null }]; return { chat_id: 'wh_new', bot_name: 'dragon' }; };
	const registry = createTargetRegistry({ ledger, bridgePost, webhookTargets: webhooks, now: () => NOW });
	await registry.resolve();
	assert.deepEqual(await registry.existingBots(OWNED.chat_id), [{ bot_id: '41', name: 'dragon', key_words: [], usable: true, reason: null }]);
	assert.deepEqual(calls.at(-1), ['/chat-bots/list', { chat_id: OWNED.chat_id }]);
	const target = await registry.useBot(OWNED.chat_id, '41');
	assert.deepEqual(added, [{ name: '擒龙群', websocketChatId: OWNED.chat_id, botId: '41' }]);
	assert.deepEqual([target.chat_id, target.name, target.origin, target.usable, target.owned, target.bot_id, target.bot_name], ['wh_new', '擒龙群', 'dashboard', true, true, '41', 'dragon']);
	assert.deepEqual((await registry.groups()).map((row) => row.chat_id), ['oc_env', 'wh_new']);
	await assert.rejects(() => registry.existingBots('oc_env'), /已经是转发目标/);
	await assert.rejects(() => registry.useBot('wh_new', '41'), /已经是转发目标/);
	await assert.rejects(() => registry.useBot('7600000000000000009', '41'), /群不存在/);
});
