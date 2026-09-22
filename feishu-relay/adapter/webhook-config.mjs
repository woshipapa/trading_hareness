function sortedKeys(map) {
	return [...(map?.keys?.() ?? [])].map((value) => String(value)).sort();
}

export function parseRelayMap(value) {
	return new Map(
		String(value ?? '')
			.split(';')
			.map((pair) => pair.trim())
			.filter(Boolean)
			.map((pair) => {
				const separator = pair.indexOf('=');
				return separator < 0 ? ['', ''] : [pair.slice(0, separator).trim(), pair.slice(separator + 1).trim()];
			})
			.filter(([key, value]) => key && value),
	);
}

export function webhookConfigStatus(webhooks, keywords) {
	const webhookChatIds = sortedKeys(webhooks);
	const keywordChatIds = sortedKeys(keywords);
	const missingKeywordChatIds = webhookChatIds.filter((chatId) => !keywords?.has?.(chatId));
	const orphanKeywordChatIds = keywordChatIds.filter((chatId) => !webhooks?.has?.(chatId));
	return {
		webhook_chat_ids: webhookChatIds,
		keyword_chat_ids: keywordChatIds,
		keyword_entries: keywordChatIds.map((chatId) => ({ chat_id: chatId, keyword: keywords.get(chatId) })),
		missing_keyword_chat_ids: missingKeywordChatIds,
		orphan_keyword_chat_ids: orphanKeywordChatIds,
		all_webhook_keywords_loaded: missingKeywordChatIds.length === 0 && orphanKeywordChatIds.length === 0,
	};
}
