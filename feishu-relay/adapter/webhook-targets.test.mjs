import assert from 'node:assert/strict';
import test from 'node:test';
import { createWebhookTargets, parseWebhookUrl, WEBHOOK_ONLY_TARGET_ID } from './webhook-targets.mjs';

const HOOK = 'https://open.feishu.cn/open-apis/bot/v2/hook/11111111-2222-3333-4444-555555555555';
const OTHER_HOOK = 'https://open.feishu.cn/open-apis/bot/v2/hook/aaaaaaaa-bbbb-cccc-dddd-eeeeeeeeeeee';
const ENV_HOOK = 'https://open.feishu.cn/open-apis/bot/v2/hook/99999999-0000-0000-0000-000000000000';

function fakeLedger(rows = []) {
	const table = new Map(rows.map((row) => [row.chat_id, row]));
	return {
		table,
		async webhookTargets() { return [...table.values()]; },
		async saveWebhookTarget({ chatId, chatName, webhookCiphertext, keyword, websocketChatId, verifiedAt }) {
			const row = { chat_id: chatId, chat_name: chatName, webhook_ciphertext: webhookCiphertext, keyword, websocket_chat_id: websocketChatId, verified_at: verifiedAt, created_at: table.get(chatId)?.created_at ?? new Date('2026-10-13T06:00:00Z') };
			table.set(chatId, row);
			return row;
		},
		async deleteWebhookTarget(chatId) { return table.delete(chatId); },
	};
}

function fakeFetch(answer = { code: 0 }, status = 200) {
	const calls = [];
	const fetchImpl = async (url, init) => {
		calls.push({ url: String(url), body: JSON.parse(init.body) });
		return { ok: status >= 200 && status < 300, status, json: async () => answer };
	};
	return { calls, fetchImpl };
}

function setup({ rows = [], answer, status, secret = 'app-secret' } = {}) {
	const ledger = fakeLedger(rows);
	const { calls, fetchImpl } = fakeFetch(answer, status);
	const webhooks = new Map([['oc_env', ENV_HOOK]]);
	const keywords = new Map([['oc_env', '汇总']]);
	let next = 0;
	const targets = createWebhookTargets({ ledger, secret, webhooks, keywords, fetchImpl, now: () => new Date('2026-10-13T06:10:00Z'), newId: () => `wh_${String(++next).padStart(24, '0')}` });
	return { ledger, calls, webhooks, keywords, targets };
}

test('a new target is tested, stored encrypted and usable at once', async () => {
	const { ledger, calls, webhooks, keywords, targets } = setup();

	const target = await targets.add({ name: '三叔外部同步群', webhookUrl: ` ${HOOK} `, keyword: ' sanshu ', websocketChatId: '7690000000000000001' });

	assert.match(target.chat_id, WEBHOOK_ONLY_TARGET_ID);
	assert.deepEqual(target, { chat_id: 'wh_000000000000000000000001', name: '三叔外部同步群', keyword: 'sanshu', origin: 'dashboard', usable: true, websocket_chat_id: '7690000000000000001', verified_at: '2026-10-13T06:10:00.000Z', created_at: '2026-10-13T06:00:00.000Z' });
	// The test message goes to the entered address and carries the keyword the bot checks.
	assert.equal(calls.length, 1);
	assert.equal(calls[0].url, HOOK);
	assert.equal(calls[0].body.msg_type, 'text');
	assert.ok(calls[0].body.content.text.startsWith('sanshu '));
	// The relay reads these two maps when it sends.
	assert.equal(webhooks.get(target.chat_id), HOOK);
	assert.equal(keywords.get(target.chat_id), 'sanshu');
	// Nothing readable is stored, and nothing in the public shape carries the address.
	const row = ledger.table.get(target.chat_id);
	assert.match(row.webhook_ciphertext, /^v1\./);
	assert.ok(!row.webhook_ciphertext.includes('11111111'));
	assert.ok(!JSON.stringify(targets.list()).includes('hook/'));
});

test('a webhook that refuses the test message is not stored', async () => {
	const { ledger, webhooks, targets } = setup({ answer: { code: 19024, msg: 'Key Words Not Found' } });
	await assert.rejects(() => targets.add({ name: '新群', webhookUrl: HOOK, keyword: '错的词' }), /关键词和机器人安全设置里的不一致/);
	assert.equal(ledger.table.size, 0);
	assert.equal(webhooks.size, 1);

	const signed = setup({ answer: { code: 19021, msg: 'sign match fail' } });
	await assert.rejects(() => signed.targets.add({ name: '新群', webhookUrl: HOOK, keyword: '词' }), /签名校验/);
	const down = setup({ answer: {}, status: 502 });
	await assert.rejects(() => down.targets.add({ name: '新群', webhookUrl: HOOK, keyword: '词' }), /HTTP 502/);
	// A transport error is reported without the address that was being called.
	const offlineLedger = fakeLedger();
	const offline = createWebhookTargets({ ledger: offlineLedger, secret: 's', webhooks: new Map(), keywords: new Map(), fetchImpl: async (url) => { throw new Error(`connect ECONNREFUSED ${url}`); } });
	await assert.rejects(() => offline.add({ name: '新群', webhookUrl: HOOK, keyword: '词' }), (error) => /连不上飞书/.test(error.message) && !error.message.includes('hook/'));
	assert.equal(offlineLedger.table.size, 0);
});

test('only a Feishu custom-bot address is accepted, and nothing is called for another', async () => {
	for (const bad of ['http://open.feishu.cn/open-apis/bot/v2/hook/11111111-2222', 'https://example.com/open-apis/bot/v2/hook/11111111-2222', 'https://open.feishu.cn/open-apis/bot/v2/hook/', `${HOOK}?x=1`, 'https://open.feishu.cn:8443/open-apis/bot/v2/hook/11111111-2222', 'https://user:pw@open.feishu.cn/open-apis/bot/v2/hook/11111111-2222', '不是网址']) {
		assert.throws(() => parseWebhookUrl(bad), /webhook 地址/, bad);
	}
	assert.equal(parseWebhookUrl(HOOK), HOOK);
	assert.equal(parseWebhookUrl('https://open.larksuite.com/open-apis/bot/v2/hook/abcdefgh'), 'https://open.larksuite.com/open-apis/bot/v2/hook/abcdefgh');
	const { calls, targets } = setup();
	await assert.rejects(() => targets.add({ name: '新群', webhookUrl: 'https://example.com/hook', keyword: '词' }), /webhook 地址/);
	await assert.rejects(() => targets.add({ name: '', webhookUrl: HOOK, keyword: '词' }), /目标群名称/);
	await assert.rejects(() => targets.add({ name: '新群', webhookUrl: HOOK, keyword: ' ' }), /关键词/);
	assert.equal(calls.length, 0);
});

test('stored targets come back after a restart; an environment target keeps its own address', async () => {
	const first = setup();
	const added = await first.targets.add({ name: '三叔外部同步群', webhookUrl: HOOK, keyword: 'sanshu' });
	// A stored row for an id the environment also declares must not replace the environment's address.
	const shadow = { ...first.ledger.table.get(added.chat_id), chat_id: 'oc_env' };

	const second = setup({ rows: [...first.ledger.table.values(), shadow] });
	assert.deepEqual(await second.targets.load(), { loaded: 1, unreadable: 0 });
	assert.equal(second.webhooks.get(added.chat_id), HOOK);
	assert.equal(second.webhooks.get('oc_env'), ENV_HOOK);
	assert.deepEqual(second.targets.list().map((item) => [item.chat_id, item.origin, item.name]), [['oc_env', 'env', null], [added.chat_id, 'dashboard', '三叔外部同步群']]);
});

test('a row that cannot be decrypted is reported, not used, and can be entered again', async () => {
	const first = setup();
	const added = await first.targets.add({ name: '三叔外部同步群', webhookUrl: HOOK, keyword: 'sanshu' });

	const second = setup({ rows: [...first.ledger.table.values()], secret: 'a-different-secret' });
	assert.deepEqual(await second.targets.load(), { loaded: 0, unreadable: 1 });
	assert.equal(second.targets.has(added.chat_id), false);
	assert.deepEqual(second.targets.list().find((item) => item.chat_id === added.chat_id), { chat_id: added.chat_id, name: '三叔外部同步群', keyword: null, origin: 'dashboard', usable: false, websocket_chat_id: null, verified_at: null, created_at: null });

	const again = await second.targets.add({ chatId: added.chat_id, name: '三叔外部同步群', webhookUrl: OTHER_HOOK, keyword: 'sanshu' });
	assert.equal(again.chat_id, added.chat_id);
	assert.equal(again.usable, true);
	assert.equal(second.webhooks.get(added.chat_id), OTHER_HOOK);
});

test('the same group entered again replaces its address; one address serves one target', async () => {
	const { ledger, webhooks, targets } = setup();
	const first = await targets.add({ name: '三叔外部同步群', webhookUrl: HOOK, keyword: 'sanshu', websocketChatId: '7690000000000000001' });
	const replaced = await targets.add({ name: '三叔外部同步群（改名）', webhookUrl: OTHER_HOOK, keyword: 'sanshu2', websocketChatId: '7690000000000000001' });
	assert.equal(replaced.chat_id, first.chat_id);
	assert.equal(ledger.table.size, 1);
	assert.equal(webhooks.get(first.chat_id), OTHER_HOOK);
	assert.equal(replaced.name, '三叔外部同步群（改名）');

	await assert.rejects(() => targets.add({ name: '另一个群', webhookUrl: OTHER_HOOK, keyword: 'x' }), /已经配置给目标群 三叔外部同步群（改名）/);
	await assert.rejects(() => targets.add({ name: '另一个群', webhookUrl: ENV_HOOK, keyword: 'x' }), /已经配置给目标群 oc_env/);
	await assert.rejects(() => targets.add({ chatId: 'oc_env', name: '汇总群', webhookUrl: HOOK, keyword: 'x' }), /由服务器环境变量配置/);
	await assert.rejects(() => targets.add({ chatId: 'wh_ffffffffffffffffffffffff', name: '编的', webhookUrl: HOOK, keyword: 'x' }), /目标群不存在/);
});

test('a target a live route still sends to is not removed; an archived route does not hold it', async () => {
	const { ledger, webhooks, keywords, targets } = setup();
	const added = await targets.add({ name: '三叔外部同步群', webhookUrl: HOOK, keyword: 'sanshu' });
	const live = { tag: 'sanshu', chatName: '45节奏三叔', targetChatIds: [added.chat_id], archivedAt: null };
	const archived = { tag: 'old', chatName: '已退的群', targetChatIds: [added.chat_id], archivedAt: new Date() };

	await assert.rejects(() => targets.remove(added.chat_id, { routes: [live, archived] }), /还有 1 个源群在转发到这个目标群（#sanshu 45节奏三叔）/);
	assert.equal(targets.has(added.chat_id), true);
	await assert.rejects(() => targets.remove('oc_env', { routes: [] }), /由服务器环境变量配置/);
	assert.equal(await targets.remove('wh_ffffffffffffffffffffffff', { routes: [] }), false);

	assert.equal(await targets.remove(added.chat_id, { routes: [archived] }), true);
	assert.equal(ledger.table.size, 0);
	assert.equal(webhooks.has(added.chat_id), false);
	assert.equal(keywords.has(added.chat_id), false);
	assert.deepEqual(targets.list().map((item) => item.chat_id), ['oc_env']);
});
