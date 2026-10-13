const CREATE_DESCRIPTION = '消息转发用，由转发看板自动创建';

function messageOf(error) {
	return error instanceof Error ? error.message : String(error);
}

function checkedName(value) {
	const name = String(value ?? '').trim();
	if (!name || name.length > 60) throw new Error('群名必须是 1–60 个字');
	return name;
}

async function step(name, groupName, action) {
	try {
		return await action();
	} catch (error) {
		throw new Error(`同步群「${groupName}」${name}失败：${messageOf(error)}`);
	}
}

export function createTargetGroups({ bridgePost, targetRegistry, webhookTargets, refreshGroups }) {
	async function createGroup({ body }) {
		const name = checkedName(body?.name);
		const keyword = body?.keyword == null ? '' : String(body.keyword).trim();
		const onlyOwnerPosts = body?.only_owner_posts == null ? true : body.only_owner_posts;
		if (typeof onlyOwnerPosts !== 'boolean') throw new Error('only_owner_posts 必须是布尔值');
		const created = await bridgePost('/target-groups/create', { name, description: CREATE_DESCRIPTION }, 60_000);
		const websocketChatId = String(created.chat_id);
		const target = await step('创建转发机器人', name, () => webhookTargets.addManaged({ name, websocketChatId, keyword }));
		if (onlyOwnerPosts) await step('设置发言权限', name, () => bridgePost('/target-groups/post-permission', { chat_id: websocketChatId, only_owner: true }, 60_000));
		const invite = await step('生成邀请链接', name, () => bridgePost('/target-groups/invite', { chat_id: websocketChatId }, 60_000));
		await step('登记目标群', name, () => targetRegistry.record({
			chat_id: target.chat_id,
			websocket_chat_id: websocketChatId,
			chat_name: name,
			owned: true,
		}));
		await step('刷新群列表', name, refreshGroups);
		return { target, invite };
	}

	async function inviteForTarget({ params }) {
		const target = await targetRegistry.get(params.chatId);
		if (!target) throw new Error('目标群不存在');
		if (!/^\d+$/.test(String(target.websocket_chat_id ?? ''))) throw new Error('这个目标群没有数字群 ID，不能生成邀请链接');
		if (target.owned !== true) throw new Error('只有本账号是群主的目标群才能生成邀请链接');
		return { invite: await bridgePost('/target-groups/invite', { chat_id: String(target.websocket_chat_id) }, 60_000) };
	}

	return {
		schema: [],
		routes: [
			{ method: 'POST', path: '/api/group-relay/target-groups', handle: createGroup },
			{ method: 'POST', path: '/api/group-relay/target-groups/:chatId/invite', handle: inviteForTarget },
		],
	};
}
