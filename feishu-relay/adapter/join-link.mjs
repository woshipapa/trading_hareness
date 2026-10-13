export function createJoinLink({ bridgePost, refreshGroups }) {
	const timeoutMs = 60_000;
	return {
		schema: [],
		routes: [
			{ method: 'POST', path: '/api/group-relay/chats/link-info', handle: ({ body }) => bridgePost('/chats/link-info', body, timeoutMs) },
			{ method: 'POST', path: '/api/group-relay/chats/join', handle: async ({ body }) => {
				const result = await bridgePost('/chats/join-by-link', body, timeoutMs);
				await refreshGroups();
				return result;
			} },
		],
	};
}
