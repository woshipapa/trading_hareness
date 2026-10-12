import { stripLeadingMessageTime } from './message-time.mjs';

export function hasImportableTaggedPayload(messageText, { hasMedia = false } = {}) {
	if (hasMedia) return true;
	const tagged = String(messageText ?? '').match(/^#[a-z0-9-]+(?=\s|$)\s*/i);
	if (!tagged) return false;
	return stripLeadingMessageTime(String(messageText).slice(tagged[0].length).trimStart()).trim().length > 0;
}
