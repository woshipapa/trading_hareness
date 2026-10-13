import assert from 'node:assert/strict';
import test from 'node:test';
import { createTargetGroups } from './target-groups.mjs';

function setup({ fail = null, onlyOwnerPosts = true } = {}) {
	const calls = [];
	const records = [];
	const bridgePost = async (path, payload) => {
		calls.push(['bridge', path, payload]);
		if (fail === path) throw new Error(`${path} refused`);
		if (path === '/target-groups/create') return { status: 'ok', chat_id: '7690000000000000001', name: '同步群' };
		return { status: 'ok', chat_id: payload.chat_id, shared_url: 'https://applink.feishu.cn/client/chat/chatter/add_by_link?link_token=token', kou_ling: '口令', expire_time: 7 };
	};
	const webhookTargets = {
		async addManaged(input) {
			calls.push(['bot', input]);
			if (fail === 'bot') throw new Error('机器人拒绝');
			return { chat_id: 'wh_000000000000000000000001', name: input.name, keyword: input.keyword, managed: true };
		},
	};
	const targetRegistry = {
		async record(input) {
			calls.push(['record', input]);
			if (fail === 'record') throw new Error('数据库拒绝');
			records.push(input);
			return input;
		},
		async get(chatId) {
			calls.push(['get', chatId]);
			return chatId === 'wh_000000000000000000000001' ? { chat_id: chatId, websocket_chat_id: '7690000000000000001', owned: true } : null;
		},
	};
	const refreshGroups = async () => {
		calls.push(['refresh']);
		if (fail === 'refresh') throw new Error('刷新失败');
	};
	return { calls, records, targetRegistry, feature: createTargetGroups({ bridgePost, targetRegistry, webhookTargets, refreshGroups }), onlyOwnerPosts };
}

test('creates in order, records the target, and returns the invite', async () => {
	const { feature, calls, records } = setup();
	const route = feature.routes.find((item) => item.path === '/api/group-relay/target-groups');
	const result = await route.handle({ body: { name: '同步群', keyword: '转发', only_owner_posts: true } });
	assert.deepEqual(calls.map(([kind, path]) => kind === 'bridge' ? path : kind), [
		'/target-groups/create', 'bot', '/target-groups/post-permission', '/target-groups/invite', 'record', 'refresh',
	]);
	assert.deepEqual(records, [{ chat_id: 'wh_000000000000000000000001', websocket_chat_id: '7690000000000000001', chat_name: '同步群', owned: true }]);
	assert.deepEqual(result, { target: { chat_id: 'wh_000000000000000000000001', name: '同步群', keyword: '转发', managed: true }, invite: { status: 'ok', chat_id: '7690000000000000001', shared_url: 'https://applink.feishu.cn/client/chat/chatter/add_by_link?link_token=token', kou_ling: '口令', expire_time: 7 } });
});

test('skips the permission step when only_owner_posts is false', async () => {
	const { feature, calls } = setup();
	const route = feature.routes[0];
	await route.handle({ body: { name: '同步群', only_owner_posts: false } });
	assert.equal(calls.some(([, path]) => path === '/target-groups/post-permission'), false);
});

for (const [failure, label] of [['bot', '创建转发机器人'], ['/target-groups/post-permission', '设置发言权限'], ['/target-groups/invite', '生成邀请链接'], ['record', '登记目标群'], ['refresh', '刷新群列表']]) {
	test(`names the ${label} failure and keeps earlier results`, async () => {
		const { feature, calls } = setup({ fail: failure });
		await assert.rejects(() => feature.routes[0].handle({ body: { name: '同步群' } }), (error) => error.message.includes(`同步群「同步群」${label}失败`));
		assert.equal(calls[0][1], '/target-groups/create');
		assert.equal(calls.filter(([kind]) => kind === 'bot').length, 1);
	});
}

test('invite refuses a target without a numeric owned group id', async () => {
	const { feature, targetRegistry } = setup();
	const route = feature.routes.find((item) => item.path.endsWith('/:chatId/invite'));
	targetRegistry.get = async () => ({ chat_id: 'wh_1', websocket_chat_id: 'not-numeric', owned: true });
	await assert.rejects(() => route.handle({ params: { chatId: 'wh_1' } }), /没有数字群 ID/);
	targetRegistry.get = async () => ({ chat_id: 'wh_1', websocket_chat_id: '7690000000000000001', owned: false });
	await assert.rejects(() => route.handle({ params: { chatId: 'wh_1' } }), /只有本账号是群主/);
});
