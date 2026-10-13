import assert from 'node:assert/strict';
import test from 'node:test';
import { createWebhookTargets, DEFAULT_MANAGED_KEYWORD, parseWebhookUrl, WEBHOOK_ONLY_TARGET_ID } from './webhook-targets.mjs';

const HOOK = 'https://open.feishu.cn/open-apis/bot/v2/hook/11111111-2222-3333-4444-555555555555';
const OTHER_HOOK = 'https://open.feishu.cn/open-apis/bot/v2/hook/aaaaaaaa-bbbb-cccc-dddd-eeeeeeeeeeee';
const ENV_HOOK = 'https://open.feishu.cn/open-apis/bot/v2/hook/99999999-0000-0000-0000-000000000000';

function fakeLedger(rows = []) {
	const table = new Map(rows.map((row) => [row.chat_id, row]));
	return {
		table,
		async webhookTargets() { return [...table.values()]; },
		async saveWebhookTarget({ chatId, chatName, webhookCiphertext, keyword, websocketChatId, botId = null, managed = false, verifiedAt }) {
			const row = { chat_id: chatId, chat_name: chatName, webhook_ciphertext: webhookCiphertext, keyword, websocket_chat_id: websocketChatId, bot_id: botId, managed, verified_at: verifiedAt, created_at: table.get(chatId)?.created_at ?? new Date('2026-10-13T06:00:00Z') };
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

// The bridge side of a relay-made bot: one bot per group, remembered across calls.
function fakeBots({ hook = HOOK, fail = null, removeFails = false } = {}) {
	const calls = [];
	const made = new Map();
	return {
		calls, made,
		async ensure({ chatId, keyword }) {
			calls.push(['ensure', chatId, keyword]);
			if (fail) throw new Error(fail);
			const created = !made.has(chatId);
			if (created) made.set(chatId, `76959355374417${made.size + 10}`);
			return { status: 'ok', chat_id: chatId, bot_id: made.get(chatId), name: '转发机器人', keyword, webhook: hook, created };
		},
		async remove({ chatId, botId }) {
			calls.push(['remove', chatId, botId]);
			if (removeFails) throw new Error('尚未登录(needs_login),无法配置群机器人');
			made.delete(chatId);
			return { chat_id: chatId, bot_id: botId, status: 'removed' };
		},
	};
}

function setup({ rows = [], answer, status, secret = 'app-secret', bots = null } = {}) {
	const ledger = fakeLedger(rows);
	const { calls, fetchImpl } = fakeFetch(answer, status);
	const webhooks = new Map([['oc_env', ENV_HOOK]]);
	const keywords = new Map([['oc_env', '汇总']]);
	let next = 0;
	const targets = createWebhookTargets({ ledger, secret, webhooks, keywords, bots, fetchImpl, now: () => new Date('2026-10-13T06:10:00Z'), newId: () => `wh_${String(++next).padStart(24, '0')}` });
	return { ledger, calls, webhooks, keywords, targets };
}

test('a new target is tested, stored encrypted and usable at once', async () => {
	const { ledger, calls, webhooks, keywords, targets } = setup();

	const target = await targets.add({ name: '三叔外部同步群', webhookUrl: ` ${HOOK} `, keyword: ' sanshu ', websocketChatId: '7690000000000000001' });

	assert.match(target.chat_id, WEBHOOK_ONLY_TARGET_ID);
	assert.deepEqual(target, { chat_id: 'wh_000000000000000000000001', name: '三叔外部同步群', keyword: 'sanshu', origin: 'dashboard', usable: true, managed: false, websocket_chat_id: '7690000000000000001', verified_at: '2026-10-13T06:10:00.000Z', created_at: '2026-10-13T06:00:00.000Z' });
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
	assert.deepEqual(second.targets.list().find((item) => item.chat_id === added.chat_id), { chat_id: added.chat_id, name: '三叔外部同步群', keyword: null, origin: 'dashboard', usable: false, managed: false, websocket_chat_id: null, verified_at: null, created_at: null });

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

	assert.deepEqual(await targets.remove(added.chat_id, { routes: [archived] }), { removed: true, bot_removed: null });
	assert.equal(ledger.table.size, 0);
	assert.equal(webhooks.has(added.chat_id), false);
	assert.equal(keywords.has(added.chat_id), false);
	assert.deepEqual(targets.list().map((item) => item.chat_id), ['oc_env']);
});

test('a target with no typed address gets the relay bot of its group, and is tried like any other', async () => {
	const bots = fakeBots();
	const { ledger, calls, webhooks, keywords, targets } = setup({ bots });

	const target = await targets.addManaged({ name: '三叔外部同步群', websocketChatId: '7690000000000000001' });

	assert.deepEqual(bots.calls, [['ensure', '7690000000000000001', DEFAULT_MANAGED_KEYWORD]]);
	assert.equal(target.managed, true);
	assert.equal(target.bot_created, true);
	assert.equal(target.keyword, '转发');
	assert.equal(webhooks.get(target.chat_id), HOOK);
	assert.equal(keywords.get(target.chat_id), '转发');
	// The test message goes through the address the bot handed back, with its keyword.
	assert.equal(calls.length, 1);
	assert.ok(calls[0].body.content.text.startsWith('转发 '));
	const row = ledger.table.get(target.chat_id);
	assert.equal(row.bot_id, '7695935537441710');
	assert.equal(row.managed, true);
	assert.ok(!JSON.stringify(target).includes('hook/'));

	// The same group again: the bot is found, the target is the same one.
	const again = await targets.addManaged({ name: '三叔外部同步群', websocketChatId: '7690000000000000001', keyword: ' sanshu ' });
	assert.equal(again.chat_id, target.chat_id);
	assert.equal(again.bot_created, false);
	assert.equal(keywords.get(target.chat_id), 'sanshu');
	assert.equal(ledger.table.size, 1);
});

test('a relay-made bot needs a group from the joined list and a bridge that can make it', async () => {
	const without = setup();
	await assert.rejects(() => without.targets.addManaged({ name: '新群', websocketChatId: '7690000000000000001' }), /没有接上 LarkAgentX/);
	const bots = fakeBots();
	const { targets } = setup({ bots });
	await assert.rejects(() => targets.addManaged({ name: '新群', websocketChatId: '' }), /从已加入的群里选目标群/);
	await assert.rejects(() => targets.addManaged({ name: '', websocketChatId: '7690000000000000001' }), /目标群名称/);
	assert.deepEqual(bots.calls, []);
	// What the bridge refuses is passed on as it is, and nothing is stored.
	const refused = setup({ bots: fakeBots({ fail: '这个群不能添加自定义机器人（群设置不允许，或账号没有权限）' }) });
	await assert.rejects(() => refused.targets.addManaged({ name: '新群', websocketChatId: '7690000000000000001' }), /不能添加自定义机器人/);
	assert.equal(refused.ledger.table.size, 0);
	const odd = setup({ bots: fakeBots({ hook: 'https://example.com/hook' }) });
	await assert.rejects(() => odd.targets.addManaged({ name: '新群', websocketChatId: '7690000000000000001' }), /webhook 地址/);
	assert.equal(odd.calls.length, 0);
});

test('removing a relay-made target removes its bot; a hand-made bot is left in its group', async () => {
	const bots = fakeBots();
	const { targets } = setup({ bots });
	const managed = await targets.addManaged({ name: '三叔外部同步群', websocketChatId: '7690000000000000001' });
	const typed = await targets.add({ name: '手动配的群', webhookUrl: OTHER_HOOK, keyword: 'x', websocketChatId: '7690000000000000002' });

	assert.deepEqual(await targets.remove(typed.chat_id), { removed: true, bot_removed: null });
	assert.deepEqual(await targets.remove(managed.chat_id), { removed: true, bot_removed: true });
	assert.deepEqual(bots.calls.filter(([kind]) => kind === 'remove'), [['remove', '7690000000000000001', '7695935537441710']]);

	// A bot that cannot be removed does not keep the target: the relay stops sending either way.
	const stuck = setup({ bots: fakeBots({ removeFails: true }) });
	const target = await stuck.targets.addManaged({ name: '三叔外部同步群', websocketChatId: '7690000000000000001' });
	assert.deepEqual(await stuck.targets.remove(target.chat_id), { removed: true, bot_removed: false });
	assert.equal(stuck.targets.has(target.chat_id), false);
});

test('a relay-made target survives a restart with its bot remembered', async () => {
	const first = setup({ bots: fakeBots() });
	const target = await first.targets.addManaged({ name: '三叔外部同步群', websocketChatId: '7690000000000000001' });
	const bots = fakeBots();
	const second = setup({ rows: [...first.ledger.table.values()], bots });
	await second.targets.load();
	assert.equal(second.targets.list().find((item) => item.chat_id === target.chat_id).managed, true);
	assert.deepEqual(await second.targets.remove(target.chat_id), { removed: true, bot_removed: true });
	assert.deepEqual(bots.calls, [['remove', '7690000000000000001', '7695935537441710']]);
});
