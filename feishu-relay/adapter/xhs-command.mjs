const XHS_RE = /^#xhs(?:\s+([\s\S]*))?$/iu;

/** Parse an explicit XHS control command from a normalized Feishu event. */
export function parseXhsCommand(data, allowChatId = '') {
	const chatId = String(data?.message?.chat_id ?? data?.chat_id ?? '').trim();
	const allowed = String(allowChatId ?? '').trim();
	if (allowed && chatId !== allowed) return null;
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
