import { stripLeadingMessageTime } from './message-time.mjs';

export function isSystemMessage(message) {
	return String(message?.msg_type ?? message?.message_type ?? '').toLowerCase() === 'system';
}

export function isSystemRelayPlaceholder(messageText, tag) {
	const escapedTag = String(tag ?? '').replace(/[.*+?^${}()|[\]\\]/g, '\\$&');
	if (!escapedTag) return false;
	const remainder = stripLeadingMessageTime(String(messageText ?? '').replace(new RegExp(`^#${escapedTag}(?:\\s+|$)`, 'i'), '').trimStart()).trimStart();
	return /^\[system\](?:\s|　|$)/i.test(remainder);
}
