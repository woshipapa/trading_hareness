import { setTimeout as delay } from 'node:timers/promises';
import { larkAgentXCardIsDegraded } from './larkagentx-ingress.mjs';

function eventMillis(value) {
	const n = Number(value);
	if (!Number.isFinite(n) || n <= 0) throw new Error('补读缺少有效的源消息时间');
	return n < 1e12 ? n * 1000 : n;
}

function normalizeText(value) {
	return String(value ?? '').normalize('NFKC')
		.replace(/\[(?:post|image|card|interactive|富文本|图片|卡片)\]/gi, '')
		.replace(/\s+/gu, '').trim();
}

function features(value) {
	const texts = []; const keys = new Set();
	function visit(node) {
		if (Array.isArray(node)) { node.forEach(visit); return; }
		if (!node || typeof node !== 'object') return;
		for (const [key, item] of Object.entries(node)) {
			// Do not inspect crypto/session fields or duplicate API content_v2.
			if (['crypto', 'cipher', 'content_v2'].includes(key)) continue;
			if (['imageKey', 'image_key', 'image_id', 'imageId', 'source_id', 'sourceId', 'fileKey', 'file_key'].includes(key) && typeof item === 'string') keys.add(item);
			if (['imageIds', 'image_ids'].includes(key) && Array.isArray(item)) item.forEach((value) => { if (typeof value === 'string' && value.trim()) keys.add(value.trim()); });
			if (['text', 'title', 'fileName', 'file_name'].includes(key) && typeof item === 'string') texts.push(item);
			else if (item && typeof item === 'object') visit(item);
		}
	}
	visit(value);
	return { text: normalizeText(texts.join('')), keys };
}

// IDs are intentionally NOT matching features: the two APIs have separate
// namespaces. Require positive content/resource evidence plus a narrow time
// bound. Never use nearest-time or a lone placeholder as proof of identity —
// except for a server-degraded card, which by definition carries no usable
// features (only the "upgrade your client" banner and its fixed artwork):
// there an exactly-one same-type candidate inside the ±2s window is accepted,
// because refusing would lose the message entirely.
export function matchOAuthMessage(input, items, chatId, { degraded = false } = {}) {
	const eventTime = eventMillis(input?.create_time);
	const upstream = String(input?.msg_type_name ?? '').toLowerCase();
	const type = upstream === 'card' ? 'interactive' : upstream;
	const source = features({ content_data: input?.content_data, _larkagentx_images: input?._larkagentx_images ?? [] });
	const summary = normalizeText(input?.content);
	const matches = new Map();
	const windowCandidates = [];
	for (const item of items) {
		if (!item?.message_id || item.deleted || item.msg_type !== type || (item.chat_id && item.chat_id !== chatId)) continue;
		let stamp; try { stamp = eventMillis(item.create_time); } catch { continue; }
		if (Math.abs(stamp - eventTime) > 2000) continue;
		let body; try { body = JSON.parse(item.body?.content ?? '{}'); } catch { continue; }
		windowCandidates.push(item);
		const target = features(body);
		const resourceMatch = [...source.keys].some(key => target.keys.has(key));
		const textMatch = target.text && [source.text, summary].some(text => text && text === target.text);
		if (resourceMatch || textMatch) matches.set(String(item.message_id), item);
	}
	if (matches.size > 1) throw new Error(`OAuth 补读存在多个内容/时间相符候选（${matches.size}），拒绝猜测`);
	const matched = matches.values().next().value ?? null;
	if (!matched && degraded && windowCandidates.length === 1) return windowCandidates[0];
	return matched;
}

export async function readLarkAgentXBackfill(input, source, { sourceApi, sleep = delay, logger = console } = {}) {
	const chatId = String(source?.chatId ?? '').trim();
	if (!chatId.startsWith('oc_')) throw new Error('OAuth 补读缺少已映射的官方群 chatId，禁止使用 WebSocket 数字群 ID');
	const websocketId = String(input?.msg_id ?? input?.message_id ?? '').trim();
	if (!websocketId) throw new Error('补读事件缺少 WebSocket 消息标识');
	const eventTime = eventMillis(input?.create_time);
	let candidateCount = 0;
	for (const pause of [0, 1500, 3500]) {
		if (pause) await sleep(pause);
		let pageToken; const items = [];
		for (let page = 0; page < 5; page++) {
			const result = await sourceApi.messageList({
				container_id_type: 'chat', container_id: chatId,
				start_time: String(Math.max(0, Math.floor((eventTime - 2000) / 1000))),
				end_time: String(Math.ceil((eventTime + 2000) / 1000)),
				sort_type: 'ByCreateTimeAsc', page_size: 50, with_sender_name: true, card_msg_content_type: 'user_card_content',
				...(pageToken ? { page_token: pageToken } : {}),
			});
			if (result.code) throw new Error(`OAuth 补读接口失败：${result.msg ?? result.code}`);
			items.push(...(result.data?.items ?? []));
			if (!result.data?.has_more) break;
			if (!result.data?.page_token || page === 4) throw new Error('OAuth 补读分页不完整，拒绝选择候选');
			pageToken = result.data.page_token;
		}
		candidateCount = items.length;
		const message = matchOAuthMessage(input, items, chatId, { degraded: larkAgentXCardIsDegraded(input) });
		if (message) {
			logger.info(`LarkAgentX OAuth matched chat=${chatId} ws_message=${websocketId} oauth_message=${message.message_id}`);
			// Keep the official ID for media download AND ledger deduplication with
			// OAuth polling. Store the WS identity separately, never overwrite it.
			return { ...message, larkagentx_origin: { message_id: websocketId, chat_id: String(input.chat_id), create_time: eventTime }, oauth_chat_id: chatId };
		}
	}
	const error = new Error(`OAuth 补读无可靠匹配 chat=${chatId} ws_message=${websocketId} candidates=${candidateCount}`);
	error.statusCode = 502;
	throw error;
}
