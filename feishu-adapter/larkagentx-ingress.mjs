const CHAT_TYPE_NAMES = new Map([
	[1, 'p2p'],
	[2, 'group'],
	[3, 'topic_group'],
]);

const DIRECT_RELAY_TYPES = new Set(['TEXT', 'SYSTEM', 'POST']);

// LarkAgentX's POST summary is produced by the protobuf decoder and can be
// lossy: anchor URLs may only survive in content_data, while an image CDN URL
// can be rendered beside the human text.  Keep external links, but never
// expose the private Feishu resource URL in a relay message.
const URL_RE = /https?:\/\/[^\s<>'"\u3000<>「」『』（）()\[\]{}]+/giu;
const LARK_IMAGE_CDN_RE = /^https?:\/\/s1-imfile\.feishucdn\.com\/static-resource\//iu;

function cleanUrl(value) {
	let url = String(value ?? '').trim();
	while (/[.,!?;:，。！？；：、）)】》」』]$/u.test(url)) url = url.slice(0, -1);
	return url && !LARK_IMAGE_CDN_RE.test(url) ? url : '';
}

function urlsFromValue(value, found = []) {
	if (typeof value === 'string') {
		for (const match of value.matchAll(URL_RE)) {
			const url = cleanUrl(match[0]);
			if (url && !found.includes(url)) found.push(url);
		}
		return found;
	}
	if (Array.isArray(value)) { value.forEach((item) => urlsFromValue(item, found)); return found; }
	if (!value || typeof value !== 'object') return found;
	Object.values(value).forEach((item) => urlsFromValue(item, found));
	return found;
}

function stripRelayNoise(value) {
	return String(value ?? '')
		.replace(URL_RE, (url) => cleanUrl(url) || '')
		.replace(/[ \t]+\n/gu, '\n')
		.replace(/\n{3,}/gu, '\n\n')
		.trim();
}

function removeRepeatedPost(text) {
	const normalized = stripRelayNoise(text).replace(/\r\n?/gu, '\n');
	// Some client versions expose the same POST summary twice.  Collapse only
	// an exact repeated block separated by blank lines; repeated paragraphs that
	// are genuinely part of the message remain untouched.
	const repeated = normalized.match(/^([\s\S]{20,}?)(?:\n{1,3})\1$/u);
	return repeated ? repeated[1].trim() : normalized;
}

function portablePostText(input) {
	const candidates = [input?.content, input?.content_data?.richText?.innerText, input?.content_data?.rich_text?.innerText];
	const text = candidates.map((value) => stripRelayNoise(value)
		.replace(/\[(?:post|image|card|interactive|富文本|图片|卡片)\]/giu, '')
		.trim()).find(Boolean) ?? '';
	const links = urlsFromValue(input?.content);
	urlsFromValue(input?.content_data, links);
	const withLinks = links.reduce((current, url) => current.includes(url) ? current : `${current}\n${url}`.trim(), text);
	return removeRepeatedPost(withLinks);
}

function portablePostRows(text, links = []) {
	const rows = [];
	const urlPattern = new RegExp(URL_RE.source, 'giu');
	for (const line of String(text ?? '').split('\n')) {
		const children = [];
		let offset = 0;
		for (const match of line.matchAll(urlPattern)) {
			const url = cleanUrl(match[0]);
			if (!url) continue;
			if (match.index > offset) children.push({ tag: 'text', text: line.slice(offset, match.index) });
			children.push({ tag: 'a', href: url, text: url });
			offset = match.index + match[0].length;
		}
		if (offset < line.length) children.push({ tag: 'text', text: line.slice(offset) });
		if (children.length) rows.push(children);
		else if (line) rows.push([{ tag: 'text', text: line }]);
		else if (rows.length) rows.push([{ tag: 'text', text: '' }]);
	}
	for (const url of links) {
		if (!String(text).includes(url)) rows.push([{ tag: 'a', href: url, text: url }]);
	}
	return rows;
}

function cardContentFromLarkAgentX(input) {
	const data = input?.content_data;
	const candidates = [
		data?.jsonCard,
		data?.json_card,
		data?.card?.jsonCard,
		data?.card?.json_card,
		data?.openCardContent,
		data?.open_card_content,
	];
	for (const candidate of candidates) {
		if (candidate && typeof candidate === 'object') return candidate;
		if (typeof candidate !== 'string' || !candidate.trim()) continue;
		try {
			const parsed = JSON.parse(candidate);
			if (parsed && typeof parsed === 'object') return parsed;
		} catch {
			// LarkAgentX also exposes a human summary for cards; it is not a
			// portable card payload, so keep looking for jsonCard.
		}
	}
	return null;
}

function imageKeyFromLarkAgentX(input) {
	const data = input?.content_data;
	const v2 = data?.imageV2 ?? data?.image_v2;
	if (typeof v2?.imageKey === 'string' && v2.imageKey.trim()) return v2.imageKey.trim();
	if (typeof v2?.image_key === 'string' && v2.image_key.trim()) return v2.image_key.trim();
	const image = data?.image;
	const origin = image?.origin ?? image?.originImage ?? {};
	for (const value of [origin?.key, origin?.imageKey, origin?.image_key, image?.key, image?.imageKey, image?.image_key]) {
		if (typeof value === 'string' && value.trim()) return value.trim();
	}
	return '';
}

function requiredString(value, name, maxLength = 256) {
	const result = String(value ?? '').trim();
	if (!result) throw new Error(`LarkAgentX 消息缺少 ${name}`);
	if (result.length > maxLength) throw new Error(`LarkAgentX ${name} 超过长度限制`);
	return result;
}

export function larkAgentXMessageType(input) {
	return String(input?.msg_type_name ?? input?.msg_type ?? 'TEXT').trim().toUpperCase() || 'TEXT';
}

function isEncryptedImageSummary(input) {
	const content = String(input?.content ?? '').trim();
	return /imageKey\s*=|已加密/.test(content) && /图片|imageKey\s*=/.test(content);
}

function hasUsableImageResource(resource) {
	return Boolean(
		resource && typeof resource === 'object'
		&& typeof resource.image_id === 'string' && resource.image_id.trim()
		&& typeof resource.key_hex === 'string' && /^[0-9a-f]{64}$/i.test(resource.key_hex)
		&& typeof resource.iv_hex === 'string' && /^[0-9a-f]{24}$/i.test(resource.iv_hex),
	);
}

function imageResourceMatchesId(resource, id) {
	const normalized = String(id ?? '').trim();
	return Boolean(normalized && resource && [resource.image_id, resource.source_id, resource.element_id].some((value) => String(value ?? '').trim() === normalized));
}

function hasPortablePostContent(input) {
	const data = input?.content_data;
	const richText = data?.richText ?? data?.rich_text;
	const text = portablePostText(input).replace(/\[(?:post|image|card|interactive|富文本|图片|卡片)\]/giu, '').trim();
	if (!richText || typeof richText !== 'object') return Boolean(text || urlsFromValue(input?.content_data).length);
	// LarkAgentX's protobuf decoder can expose the image ID list while still
	// omitting the rich-text element properties.  That is enough to identify an
	// OAuth candidate, but not enough to download/re-upload the resource.  Any
	// resource-bearing POST therefore takes the precise official backfill path.
	const resourceIds = [
		...(Array.isArray(richText.imageIds) ? richText.imageIds : []),
		...(Array.isArray(richText.mediaIds) ? richText.mediaIds : []),
		...(Array.isArray(richText.docsIds) ? richText.docsIds : []),
	].filter((value) => typeof value === 'string' && value.trim());
	if (resourceIds.length) {
		const resources = Array.isArray(input?._larkagentx_images) ? input._larkagentx_images : [];
		return resourceIds.every((id) => resources.some((resource) => imageResourceMatchesId(resource, id) && hasUsableImageResource(resource)));
	}
	return Boolean(text || String(richText.innerText ?? '').trim());
}

export function isDirectLarkAgentXRelayType(input) {
	if (String(input?.content_status ?? '').toLowerCase() === 'unsupported') return false;
	const upstreamType = larkAgentXMessageType(input);
	return ((upstreamType === 'POST' ? hasPortablePostContent(input) : DIRECT_RELAY_TYPES.has(upstreamType)) && !isEncryptedImageSummary(input))
		|| (upstreamType === 'IMAGE' && Boolean(imageKeyFromLarkAgentX(input)) && hasUsableImageResource(input?._larkagentx_image))
		// A private WebSocket may expose only the stable CARD type and the
		// human summary (`[卡片]`) for a card whose JSON body is client-only.
		// Keep that event on the real-time path so an exhausted OAuth quota
		// cannot turn the durable spool into an endless retry queue. Complete
		// cards still retain their native interactive payload below; incomplete
		// cards are downgraded to a tagged text summary.
		|| ['CARD', 'INTERACTIVE'].includes(upstreamType);
}

export function normalizeLarkAgentXRelayMessage(input, { now = Date.now } = {}) {
	const messageId = requiredString(input?.msg_id ?? input?.message_id, 'message_id', 128);
	const content = String(input?.content ?? '').trim();
	const upstreamType = larkAgentXMessageType(input);
	if (['CARD', 'INTERACTIVE'].includes(upstreamType)) {
		const card = cardContentFromLarkAgentX(input);
		if (!card) {
			const summary = String(input?.content ?? '').trim() || `[${upstreamType.toLowerCase()}] 卡片内容未随 WebSocket 提供`;
			return {
				message_id: messageId,
				msg_type: 'text',
				create_time: input?.create_time ?? now(),
				update_time: input?.update_time ?? null,
				body: { content: JSON.stringify({ text: summary }) },
				sender: { sender_id: String(input?.from_id ?? input?.sender_id ?? '') },
			};
		}
		return {
			message_id: messageId,
			msg_type: 'interactive',
			create_time: input?.create_time ?? now(),
			update_time: input?.update_time ?? null,
			body: { content: JSON.stringify(card) },
			sender: { sender_id: String(input?.from_id ?? input?.sender_id ?? '') },
		};
	}
	if (upstreamType === 'IMAGE') {
		const imageKey = imageKeyFromLarkAgentX(input);
		if (!imageKey) throw new Error(`LarkAgentX 类型 ${upstreamType} 缺少 image key，需要通过官方消息接口补读`);
		return {
			message_id: messageId,
			msg_type: 'image',
			create_time: input?.create_time ?? now(),
			update_time: input?.update_time ?? null,
			body: { content: JSON.stringify({
				image_key: imageKey,
				...(input?._larkagentx_image && typeof input._larkagentx_image === 'object' ? { larkagentx_resource: input._larkagentx_image } : {}),
			}) },
			sender: { sender_id: String(input?.from_id ?? input?.sender_id ?? '') },
		};
	}
	if (upstreamType === 'POST') {
		const links = urlsFromValue(input?.content_data);
		const portableText = portablePostText(input);
		const images = Array.isArray(input?._larkagentx_images) ? input._larkagentx_images : [];
		if (!portableText && !links.length && !images.length) throw new Error('LarkAgentX 实时转发 POST 缺少文本内容');
		const imageRows = images.filter((image) => image && typeof image.image_id === 'string' && image.image_id.trim()).map((image) => ({
			tag: 'img', image_key: image.image_id.trim(), ...(image.key_hex && image.iv_hex ? { larkagentx_resource: image } : {}),
		}));
		return {
			message_id: messageId,
			msg_type: 'post',
			create_time: input?.create_time ?? now(),
			update_time: input?.update_time ?? null,
			body: { content: JSON.stringify({ zh_cn: { title: '', content: [...portablePostRows(portableText, links), ...imageRows.map((image) => [image])] } }) },
			sender: { sender_id: String(input?.from_id ?? input?.sender_id ?? '') },
		};
	}
	if (!DIRECT_RELAY_TYPES.has(upstreamType)) throw new Error(`LarkAgentX 类型 ${upstreamType} 需要通过官方消息接口补读`);
	if (!content) throw new Error('LarkAgentX 实时转发消息缺少文本内容');
	const msgType = upstreamType === 'SYSTEM' ? 'system' : 'text';
	const displayText = upstreamType === 'TEXT' ? content : `[${upstreamType.toLowerCase()}]\n${content}`;
	return {
		message_id: messageId,
		msg_type: msgType,
		create_time: input?.create_time ?? now(),
		update_time: input?.update_time ?? null,
		body: { content: JSON.stringify({ text: displayText }) },
		sender: { sender_id: String(input?.from_id ?? input?.sender_id ?? '') },
	};
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

export function normalizeLarkAgentXSummaryMessage(input, { now = Date.now } = {}) {
	if (!input || typeof input !== 'object') throw new Error('LarkAgentX 汇总消息必须是 JSON 对象');
	const relay = normalizeLarkAgentXRelayMessage(input, { now });
	const chatId = requiredString(input.chat_id, 'chat_id', 128);
	const createTime = relay.create_time ?? input.create_time ?? now();
	return {
		event_id: String(input.event_id ?? `larkagentx:summary:${chatId}:${relay.message_id}`).trim().slice(0, 200),
		event_type: 'im.message.receive_v1',
		source: 'larkagentx-summary',
		source_label: String(input.source_label ?? 'LarkAgentX 汇总群').trim().slice(0, 120),
		message: {
			message_id: relay.message_id,
			chat_id: chatId,
			chat_type: String(input.chat_type_name ?? input.chat_type ?? 'group').toLowerCase(),
			message_type: relay.msg_type,
			content: relay.body.content,
			create_time: createTime,
			update_time: relay.update_time ?? input.update_time ?? null,
			deleted: false,
		},
		sender: relay.sender,
	};
}

export function normalizeLarkAgentXUnsupportedMessage(input, { now = Date.now } = {}) {
	if (!input || typeof input !== 'object') throw new Error('LarkAgentX 消息必须是 JSON 对象');
	const messageId = requiredString(input.msg_id ?? input.message_id, 'message_id', 128);
	const upstreamType = larkAgentXMessageType(input).toLowerCase();
	const content = String(input.content ?? '').trim() || `[${upstreamType}] 消息内容未提供可解码正文`;
	return {
		message_id: messageId,
		msg_type: 'text',
		create_time: input.create_time ?? now(),
		update_time: input.update_time ?? null,
		body: { content: JSON.stringify({ text: `[${upstreamType}] ${content}` }) },
		sender: { sender_id: String(input.from_id ?? input.sender_id ?? '') },
	};
}
