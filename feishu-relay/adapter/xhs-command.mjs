const XHS_RE = /^#xhs(?:\s+([\s\S]*))?$/iu;

/** Parse an explicit XHS control command from a normalized Feishu event. */
export function parseXhsCommand(data, allowChatId = '') {
	const chatId = String(data?.message?.chat_id ?? data?.chat_id ?? '').trim();
	const allowed = new Set(String(allowChatId ?? '').split(',').map((value) => value.trim()).filter(Boolean));
	// LarkAgentX has its own numeric chat identity. The bridge has already
	// admitted this event through its explicit command lane, so do not reject
	// it merely because the official API uses an oc_ chat id.
	if (allowed.size && !allowed.has(chatId) && data?.larkagentx_command_lane !== true) return null;
	const raw = String(data?.message?.content ?? data?.content ?? '')
		.replace(/@_user_\d+\s*/gu, '')
		.trim();
	const match = XHS_RE.exec(raw);
	if (!match) return null;
	return {
		command: String(match[1] ?? 'status').trim() || 'status',
		chat_id: chatId,
		message_id: String(data?.message?.message_id ?? data?.message?.msg_id ?? data?.event_id ?? '').trim(),
	};
}
