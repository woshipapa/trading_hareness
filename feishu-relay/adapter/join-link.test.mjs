import assert from 'node:assert/strict';
import test from 'node:test';
import { createJoinLink } from './join-link.mjs';

function setup({ joinResult = { status: 'ok', chat_id: '7696', name: '研究群' }, joinError = null } = {}) {
	const calls = [];
	let refreshes = 0;
	const feature = createJoinLink({
		bridgePost: async (...args) => {
			calls.push(args);
			if (joinError) throw joinError;
			return joinResult;
		},
		refreshGroups: async () => { refreshes += 1; },
	});
	return { feature, calls, refreshes: () => refreshes };
}

test('link-info forwards the link to the bridge', async () => {
	const { feature, calls } = setup();
	const body = { link: 'https://applink.feishu.cn/client/chat/chatter/add_by_link?link_token=redacted' };
	assert.deepEqual(await feature.routes[0].handle({ body }), { status: 'ok', chat_id: '7696', name: '研究群' });
	assert.deepEqual(calls, [['/chats/link-info', body, 60_000]]);
});

test('a failed join does not refresh groups and preserves the bridge error', async () => {
	const error = new Error('邀请链接中的 link_token 无效');
	const { feature, calls, refreshes } = setup({ joinError: error });
	await assert.rejects(() => feature.routes[1].handle({ body: { link: 'bad' } }), error);
	assert.deepEqual(calls, [['/chats/join-by-link', { link: 'bad' }, 60_000]]);
	assert.equal(refreshes(), 0);
});

test('a successful join refreshes groups once', async () => {
	const { feature, calls, refreshes } = setup();
	assert.deepEqual(await feature.routes[1].handle({ body: { link: 'invite' } }), { status: 'ok', chat_id: '7696', name: '研究群' });
	assert.deepEqual(calls, [['/chats/join-by-link', { link: 'invite' }, 60_000]]);
	assert.equal(refreshes(), 1);
});
