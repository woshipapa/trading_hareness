const CHAT_TYPE_NAMES = new Map([
	[1, 'p2p'],
	[2, 'group'],
	[3, 'topic_group'],
]);

function requiredString(value, name, maxLength = 256) {
	const result = String(value ?? '').trim();
	if (!result) throw new Error(`LarkAgentX 消息缺少 ${name}`);
	if (result.length > maxLength) throw new Error(`LarkAgentX ${name} 超过长度限制`);
	return result;
}

export function normalizeLarkAgentXMessage(input, { now = Date.now } = {}) {
	if (!input || typeof input !== 'object') throw new Error('LarkAgentX 消息必须是 JSON 对象');
	const messageId = requiredString(input.msg_id ?? input.message_id, 'message_id', 128);
	const chatId = requiredString(input.chat_id, 'chat_id', 128);
	const senderId = requiredString(input.from_id ?? input.sender_id, 'sender_id', 128);
	const content = String(input.content ?? '').trim();
	if (!content) throw new Error('LarkAgentX 消息没有可转发的文本内容');
	const eventId = String(input.event_id ?? `larkagentx:${messageId}`).trim().slice(0, 200);
	const createTime = Number(input.create_time);
	const sourceLabel = String(input.source_label ?? 'LarkAgentX 个人会话').trim().slice(0, 120);
	const chatType = typeof input.chat_type === 'number'
		? CHAT_TYPE_NAMES.get(input.chat_type) ?? 'unknown'
		: String(input.chat_type_name ?? input.chat_type ?? 'unknown').toLowerCase();
	const messageType = String(input.msg_type_name ?? input.msg_type ?? 'TEXT').toLowerCase();
	return {
		event_id: eventId || `larkagentx:${messageId}`,
		event_type: 'im.message.receive_v1',
		source: 'larkagentx',
		source_label: sourceLabel || 'LarkAgentX 个人会话',
		message: {
			message_id: messageId,
			chat_id: chatId,
			chat_type: chatType,
			message_type: messageType,
			create_time: Number.isFinite(createTime) && createTime > 0 ? String(createTime) : String(now()),
			content: JSON.stringify({ text: content }),
		},
		sender: { sender_id: { open_id: senderId }, sender_type: 'user' },
	};
}
