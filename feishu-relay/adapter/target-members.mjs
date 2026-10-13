function targetFor(targetRegistry, chatId) {
	return targetRegistry.get(chatId).then((target) => {
		if (!target) throw new Error('目标群不存在');
		if (!/^\d+$/.test(String(target.websocket_chat_id ?? ''))) throw new Error('目标群没有可用的数字群 ID');
		if (target.owned !== true) throw new Error('只有本账号是群主的目标群可以管理成员');
		return { target, websocketChatId: String(target.websocket_chat_id) };
	});
}

export function createTargetMembers({ bridgePost, targetRegistry }) {
	async function readChat(params) {
		const { websocketChatId } = await targetFor(targetRegistry, params.chatId);
		return websocketChatId;
	}

	const routes = [
		{ method: 'GET', path: '/api/group-relay/target-groups/:chatId/members', handle: async ({ params }) => bridgePost('/chat-members/list', { chat_id: await readChat(params) }, 60_000) },
		{ method: 'GET', path: '/api/group-relay/target-groups/:chatId/history', handle: async ({ params, query }) => {
			const payload = { chat_id: await readChat(params) };
			const cursor = query.get('cursor');
			if (cursor !== null && cursor !== '') payload.cursor = cursor;
			return bridgePost('/chat-members/history', payload, 60_000);
		} },
		{ method: 'GET', path: '/api/group-relay/target-groups/:chatId/applications', handle: async ({ params }) => bridgePost('/chat-members/applications', { chat_id: await readChat(params) }, 60_000) },
		{ method: 'POST', path: '/api/group-relay/target-groups/:chatId/applications/:id', handle: async ({ params, body }) => bridgePost('/chat-members/applications/decide', { chat_id: await readChat(params), id: params.id, agree: body?.agree === true }, 60_000) },
		{ method: 'DELETE', path: '/api/group-relay/target-groups/:chatId/members/:userId', handle: async ({ params }) => bridgePost('/chat-members/remove', { chat_id: await readChat(params), user_id: params.userId }, 60_000) },
	];

	return { schema: [], routes };
}
