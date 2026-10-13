import assert from 'node:assert/strict';
import test from 'node:test';
import { createTargetMembers } from './target-members.mjs';

function setup(target) {
	const calls = [];
	const feature = createTargetMembers({
		targetRegistry: { get: async () => target },
		bridgePost: async (...args) => { calls.push(args); return { status: 'ok' }; },
	});
	return { calls, routes: feature.routes };
}

test('an unknown, id-less, or unowned target is refused before bridge calls', async () => {
	for (const target of [null, { owned: true }, { websocket_chat_id: '7001', owned: false }]) {
		const { calls, routes } = setup(target);
		await assert.rejects(() => routes[0].handle({ params: { chatId: 'target' } }), /目标群/);
		assert.deepEqual(calls, []);
	}
});

test('member routes map target ids to bridge paths and payloads', async () => {
	const { calls, routes } = setup({ websocket_chat_id: '7001', owned: true });
	await routes[0].handle({ params: { chatId: 'target' } });
	await routes[1].handle({ params: { chatId: 'target' }, query: new URLSearchParams('cursor=9') });
	await routes[2].handle({ params: { chatId: 'target' } });
	await routes[3].handle({ params: { chatId: 'target', id: '55' }, body: { agree: true } });
	await routes[4].handle({ params: { chatId: 'target', userId: '200' } });
	assert.deepEqual(calls.map(([path, payload]) => [path, payload]), [
		['/chat-members/list', { chat_id: '7001' }],
		['/chat-members/history', { chat_id: '7001', cursor: '9' }],
		['/chat-members/applications', { chat_id: '7001' }],
		['/chat-members/applications/decide', { chat_id: '7001', id: '55', agree: true }],
		['/chat-members/remove', { chat_id: '7001', user_id: '200' }],
	]);
});
