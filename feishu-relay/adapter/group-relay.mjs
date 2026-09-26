import { createDecipheriv, createHash } from 'node:crypto';
import { buildRelayCard, cardImageKeys, cardText } from './card-content.mjs';
import { isSystemMessage } from './message-filter.mjs';
import { blockedMessageKeywords, blockedMessageReason } from './content-filter.mjs';

const DEFAULT_HISTORY_LOOKBACK_SECONDS = 5 * 60;
const MAX_HISTORY_PAGES = 20;

// Source groups are independent, but messages within one source must remain
// ordered so its durable cursor can advance monotonically.  Keep the fan-out
// bounded instead of using an unbounded Promise.all: this lowers end-to-end
// latency when several analyst groups are active while respecting Feishu API
// pressure and the small edge host.
async function mapWithConcurrency(items, limit, worker) {
	const values = Array.from(items);
	if (!values.length) return [];
	const results = new Array(values.length);
	let nextIndex = 0;
	const workerCount = Math.min(Math.max(1, Number(limit) || 1), values.length);
	async function run() {
		while (true) {
			const index = nextIndex++;
			if (index >= values.length) return;
			results[index] = await worker(values[index], index);
		}
	}
	await Promise.all(Array.from({ length: workerCount }, () => run()));
	return results;
}
const MAX_SOURCE_FILE_BYTES = 30 * 1024 * 1024;
const MAX_SOURCE_IMAGE_BYTES = 10 * 1024 * 1024;

export class RelayUnsupportedError extends Error {}

// A custom-bot webhook send has no application-owned message_id, so it can
// never be patched or recalled through im/v1/messages. This sentinel is
// stored in place of a real id: truthy (so a completed webhook fan-out is
// never re-attempted on retry, unlike a genuine failure) but recognizably
// not a Feishu id, so the edit/recall paths know to skip it instead of
// calling the tenant API with a bogus id.
export const WEBHOOK_SENT_PREFIX = 'webhook-sent:';
export function isWebhookSentinel(id) {
	return typeof id === 'string' && id.startsWith(WEBHOOK_SENT_PREFIX);
}
// Message types a custom-bot webhook can carry. "file" has no webhook
// equivalent (a custom bot cannot receive an uploaded file_key the way a
// tenant-API message can), so a target configured with a webhook still
// falls back to the tenant API for that one message.
const WEBHOOK_CAPABLE_MSG_TYPES = new Set(['text', 'post', 'interactive']);
const XIANYU_BYPASS_SOURCES = new Set(['relay_132c18eb3486455b8d63f3012ba0c720', 'relay_4595b5c48596444bbd552a56052ac5d4']);
const ANQIANG_BLOCK_KEYWORDS = ['般若星登山的川柏'];

function sourceFilterOptions(source) {
	const sourceKey = String(source?.key ?? '').trim();
	const sourceName = String(source?.chatName ?? source?.chat_name ?? '').trim();
	const keywords = sourceKey === 'anqiang' || sourceName.includes('安强')
		? [...blockedMessageKeywords(), ...ANQIANG_BLOCK_KEYWORDS]
		: blockedMessageKeywords();
	return {
		keywords: [...new Set(keywords)].join(','),
		skipKeywords: XIANYU_BYPASS_SOURCES.has(sourceKey) ? ['咸鱼'] : [],
	};
}

function webhookContentWithKeyword(msgType, content, keyword) {
	const normalizedKeyword = String(keyword ?? '').trim();
	if (!normalizedKeyword) return content;
	const output = cloneJson(content);
	if (msgType === 'text') {
		if (typeof output?.text === 'string' && output.text.includes(normalizedKeyword)) return output;
		if (output && typeof output === 'object') output.text = `${String(output.text ?? '').trim()}\n${normalizedKeyword}`.trim();
		return output;
	}
	if (msgType === 'post') {
		const locales = Object.values(output ?? {}).filter((value) => value && typeof value === 'object' && Array.isArray(value.content));
		for (const locale of locales) {
			const alreadyPresent = locale.content.some((line) => Array.isArray(line) && line.some((item) => String(item?.text ?? '').includes(normalizedKeyword)));
			if (!alreadyPresent) locale.content.push([{ tag: 'text', text: normalizedKeyword }]);
		}
		return output;
	}
	if (msgType === 'interactive') {
		const elements = output?.body?.elements ?? output?.elements;
		if (Array.isArray(elements) && !JSON.stringify(elements).includes(normalizedKeyword)) {
			elements.push({ tag: 'div', text: { tag: 'plain_text', content: normalizedKeyword } });
		}
		return output;
	}
	return output;
}

function interactiveWebhookPostContent(content, keyword) {
	const text = cardText(content);
	const lines = [];
	const normalizedKeyword = String(keyword ?? '').trim();
	if (text && text !== normalizedKeyword) lines.push([{ tag: 'text', text }]);
	for (const imageKey of cardImageKeys(content)) lines.push([{ tag: 'img', image_key: imageKey }]);
	if (normalizedKeyword) lines.push([{ tag: 'text', text: normalizedKeyword }]);
	return { zh_cn: { title: '', content: lines } };
}

function allTargetsUseWebhooks(source, config) {
	const targets = [...new Set((source?.targetChatIds ?? []).map((value) => String(value ?? '').trim()).filter(Boolean))];
	return targets.length > 0 && targets.every((targetChatId) => config.webhooksByChatId?.has(targetChatId));
}

async function postViaWebhook(url, msgType, content) {
	const body = msgType === 'interactive'
		? { msg_type: 'interactive', card: content }
		: msgType === 'post'
			? { msg_type: 'post', content: { post: content } }
			: { msg_type: 'text', content };
	const response = await fetch(url, { method: 'POST', headers: { 'content-type': 'application/json' }, body: JSON.stringify(body) });
	const result = await response.json().catch(() => ({}));
	if (!response.ok || (result?.code && result.code !== 0)) throw new Error(`飞书 webhook 发送失败：${result?.msg ?? result?.code ?? response.status}`);
}

function parseJson(value, fallback = {}) {
	try { return JSON.parse(value ?? '{}'); } catch { return fallback; }
}

function isPlaceholderCardRelay(message) {
	if (String(message?.msg_type ?? '').trim().toLowerCase() !== 'text') return false;
	const body = parseJson(message?.body?.content, {});
	const text = String(body?.text ?? '').trim();
	return /^\[(?:card|interactive)\]\s+(?:\[卡片\]|卡片内容未随 WebSocket 提供)(?:\s+.*)?$/iu.test(text);
}

function cloneJson(value) {
	return JSON.parse(JSON.stringify(value ?? {}));
}

function asCreateTimeMs(value, fallback = Date.now()) {
	const parsed = Number(value);
	if (!Number.isFinite(parsed) || parsed <= 0) return fallback;
	return parsed < 100_000_000_000 ? parsed * 1000 : parsed;
}

// Feishu keeps reporting updated=true for an edited message on every later
// read.  Once an update time is on record, only a newer one is a new edit;
// otherwise every overlap/reconcile poll would re-patch the delivered copy.
export function sourceEdited(message, sourceUpdateTime, existing) {
	const stored = Number(existing?.source_update_time ?? existing?.sourceUpdateTime) || null;
	if (stored && sourceUpdateTime) return sourceUpdateTime > stored;
	return message?.updated === true;
}

function asEpochSeconds(value) {
	return String(Math.max(0, Math.floor(asCreateTimeMs(value) / 1000)));
}

function deterministicUuid(messageId, component, targetChatId) {
	const value = createHash('sha256').update(`feishu-group-relay:${messageId}:${component}:${targetChatId}`).digest('hex');
	return `${value.slice(0, 8)}-${value.slice(8, 12)}-4${value.slice(13, 16)}-a${value.slice(17, 20)}-${value.slice(20, 32)}`;
}

function messageText(content) {
	const value = parseJson(content, { raw: String(content ?? '') });
	return typeof value.text === 'string' ? value.text : typeof value.raw === 'string' ? value.raw : '';
}

function taggedText(tag, text = '') {
	const prefix = `#${tag}`;
	const normalized = String(text ?? '');
	return normalized === prefix || normalized.startsWith(`${prefix}\n`) ? normalized : `${prefix}${normalized ? `\n${normalized}` : ''}`;
}

function taggedFilename(tag, filename = 'file') {
	return `#${tag} ${String(filename || 'file')}`;
}

function headerValue(headers, name) {
	return headers?.[name] ?? headers?.[name.toLowerCase()] ?? headers?.[name.toUpperCase()] ?? '';
}

function filenameFromHeaders(headers, fallback) {
	const contentDisposition = String(headerValue(headers, 'content-disposition'));
	const match = contentDisposition.match(/filename\*?=(?:UTF-8''|\")?([^;\"]+)/i);
	return match ? decodeURIComponent(match[1].replace(/\"/g, '')).replace(/[^\w.\-()\u4e00-\u9fff]+/g, '_') : fallback;
}

function fileType(filename, contentType) {
	const value = `${filename} ${contentType}`.toLowerCase();
	if (value.includes('.opus') || value.includes('audio/opus')) return 'opus';
	if (value.includes('.mp4') || value.includes('video/mp4')) return 'mp4';
	if (value.includes('.pdf') || value.includes('application/pdf')) return 'pdf';
	if (/\.(doc|docx)\b/.test(value) || value.includes('word')) return 'doc';
	if (/\.(xls|xlsx)\b/.test(value) || value.includes('spreadsheet')) return 'xls';
	if (/\.(ppt|pptx)\b/.test(value) || value.includes('presentation')) return 'ppt';
	return 'stream';
}

async function readableToBuffer(readable, maxBytes) {
	const chunks = [];
	let bytes = 0;
	for await (const chunk of readable) {
		const value = Buffer.from(chunk);
		bytes += value.length;
		if (bytes > maxBytes) throw new RelayUnsupportedError(`消息资源超过 ${Math.floor(maxBytes / 1024 / 1024)} MiB 转发上限`);
		chunks.push(value);
	}
	if (!bytes) throw new RelayUnsupportedError('消息资源为空，无法转发');
	return Buffer.concat(chunks, bytes);
}

function looksLikeImage(bytes) {
	return bytes.subarray(0, 3).equals(Buffer.from([0xff, 0xd8, 0xff]))
		|| bytes.subarray(0, 8).equals(Buffer.from('\x89PNG\r\n\x1a\n', 'binary'))
		|| (bytes.length >= 12 && bytes.subarray(0, 4).toString() === 'RIFF' && bytes.subarray(8, 12).toString() === 'WEBP')
		|| bytes.subarray(0, 6).toString() === 'GIF87a'
		|| bytes.subarray(0, 6).toString() === 'GIF89a';
}

function decryptLarkAgentXImage(bytes, resource) {
	if (looksLikeImage(bytes)) return bytes;
	const key = Buffer.from(String(resource?.key_hex ?? ''), 'hex');
	const iv = Buffer.from(String(resource?.iv_hex ?? ''), 'hex');
	if (key.length !== 32 || iv.length !== 12 || bytes.length < 16) throw new RelayUnsupportedError('LarkAgentX 图片缺少有效 AES-GCM 参数');
	const decipher = createDecipheriv('aes-256-gcm', key, iv);
	decipher.setAuthTag(bytes.subarray(-16));
	return Buffer.concat([decipher.update(bytes.subarray(0, -16)), decipher.final()]);
}

function collectPostResources(value, found = []) {
	if (Array.isArray(value)) {
		for (const item of value) collectPostResources(item, found);
		return found;
	}
	if (!value || typeof value !== 'object') return found;
	function addResource(resource) {
		const existing = found.find((item) => item.key === resource.key && item.kind === resource.kind);
		if (!existing) {
			found.push(resource);
			return;
		}
		// A normalized post may contain the same image key once in the visible
		// content and once in a resource walk. Keep the LarkAgentX descriptor if
		// either occurrence has it, otherwise the relay falls back to OAuth.
		if (!existing.larkagentx && resource.larkagentx) existing.larkagentx = resource.larkagentx;
	}
	const larkagentx = value.larkagentx_resource && typeof value.larkagentx_resource === 'object'
		? value.larkagentx_resource
		: null;
	if (typeof value.image_key === 'string') addResource({ key: value.image_key, kind: 'image', larkagentx });
	// Card JSON 2.0 names the same resource img_key.
	if (typeof value.img_key === 'string') addResource({ key: value.img_key, kind: 'image', larkagentx });
	if (typeof value.file_key === 'string') addResource({ key: value.file_key, kind: 'file' });
	for (const child of Object.values(value)) collectPostResources(child, found);
	return found;
}

function rewritePostResourceKeys(value, replacements) {
	if (Array.isArray(value)) return value.map((item) => rewritePostResourceKeys(item, replacements));
	if (!value || typeof value !== 'object') return value;
	const output = {};
	for (const [key, child] of Object.entries(value)) {
		if ((key === 'image_key' || key === 'img_key') && replacements.image.has(child)) output[key] = replacements.image.get(child);
		else if (key === 'file_key' && replacements.file.has(child)) output[key] = replacements.file.get(child);
		else output[key] = rewritePostResourceKeys(child, replacements);
	}
	return output;
}

function prependTagToPost(content, tag) {
	const output = cloneJson(content);
	const line = [{ tag: 'text', text: `#${tag}` }];
	if (output.zh_cn && typeof output.zh_cn === 'object' && Array.isArray(output.zh_cn.content)) {
		output.zh_cn.content.unshift(line);
		return output;
	}
	if (output.en_us && typeof output.en_us === 'object' && Array.isArray(output.en_us.content)) {
		output.en_us.content.unshift(line);
		return output;
	}
	if (Array.isArray(output.content)) {
		return { zh_cn: { title: typeof output.title === 'string' ? output.title : '', content: [line, ...output.content] } };
	}
	if (Array.isArray(output.content_v2)) {
		return { zh_cn: { title: typeof output.title === 'string' ? output.title : '', content: [line, ...output.content_v2] } };
	}
	for (const localized of Object.values(output)) {
		if (localized && typeof localized === 'object' && Array.isArray(localized.content)) {
			localized.content.unshift(line);
			return output;
		}
	}
	return { zh_cn: { title: '', content: [line, [{ tag: 'text', text: JSON.stringify(output) }]] } };
}

function resourceFromDirectMessage(message) {
	const content = parseJson(message?.body?.content);
	if (typeof content.image_key === 'string') return { key: content.image_key, kind: 'image', larkagentx: content.larkagentx_resource ?? null };
	if (typeof content.file_key === 'string') return { key: content.file_key, kind: 'file' };
	return null;
}

function sourceFromRecord(record) {
	return typeof record.message === 'string' ? parseJson(record.message) : record.message;
}

function normalizedComparableText(value) {
	return String(value ?? '').replace(/\s+/gu, ' ').trim();
}

function relayContentFingerprint(message) {
	const type = String(message?.msg_type ?? '').trim().toLowerCase();
	const body = parseJson(message?.body?.content, { raw: String(message?.body?.content ?? '') });
	if (type === 'interactive') {
		return JSON.stringify({ type, text: normalizedComparableText(cardText(body)), images: cardImageKeys(body).sort() });
	}
	if (type === 'text' || type === 'system') return JSON.stringify({ type, text: normalizedComparableText(messageText(message?.body?.content)) });
	if (type === 'image') return JSON.stringify({ type, image: body?.image_key ?? body?.imageKey ?? '' });
	if (type === 'post') {
		// The WebSocket rich-text decoder adds larkagentx AES metadata while the
		// official message API adds dimensions/content_v2.  Those transport
		// fields are different for the same source post, so compare only the
		// portable text and resource keys.
		return JSON.stringify({ type, text: normalizedComparableText(cardText(body)), images: cardImageKeys(body).sort() });
	}
	return JSON.stringify({ type, body });
}

function relayMessagesEquivalent(left, right) {
	const leftTime = asCreateTimeMs(left?.source_create_time ?? left?.sourceCreateTime ?? left?.create_time, 0);
	const rightTime = asCreateTimeMs(right?.source_create_time ?? right?.sourceCreateTime ?? right?.create_time, 0);
	if (!leftTime || !rightTime || Math.abs(leftTime - rightTime) > 3_000) return false;
	return relayContentFingerprint(left?.message ?? left) === relayContentFingerprint(right?.message ?? right);
}

// Axios hides the Feishu error body behind "status code 400"; the code and
// message are what an operator needs, never the request or its headers.
function feishuErrorMessage(error) {
	const payload = error?.response?.data;
	const detail = String(payload?.msg ?? error?.message ?? error ?? '未知错误');
	return payload?.code ? `${detail}（飞书错误 ${payload.code}）` : detail;
}

function uploadErrorMessage(resource, error) {
	const payload = error?.response?.data;
	const apiMessage = String(payload?.msg ?? '');
	// Do not persist the platform's authorization URL: it contains application
	// metadata and does not help an operator fix the relay.  Keep the actionable
	// permission name instead.
	if (apiMessage.includes('im:resource:upload') || apiMessage.includes('im:resource')) {
		return `${resource}失败：机器人应用缺少 im:resource:upload（或 im:resource）应用身份权限`;
	}
	const detail = apiMessage || String(error?.message ?? '未知错误');
	const code = payload?.code ? `（飞书错误 ${payload.code}）` : '';
	return `${resource}失败${code}：${detail}`;
}

export function createGroupRelay({ larkClient, sourceApi, ledger, workbench = null, config, canWrite = null, logger = console }) {
	let running = false;
	const sourceConcurrency = Math.max(1, Math.min(8, Number(config.sourceConcurrency) || 3));
	let lastUnavailableLogAt = 0;
	let lastTickStartedAt = null;
	let lastTickCompletedAt = null;
	let lastTickError = null;
	let writerState = canWrite ? 'starting' : 'not_configured';
	const reconcileEveryMs = Math.max(60 * 60_000, Number(config.reconcileEverySeconds ?? 6 * 3600) * 1000);
	const reconcileLookbackMs = Math.max(5 * 60_000, Number(config.reconcileLookbackSeconds ?? 24 * 3600) * 1000);
	const sourceRuntime = new Map(config.sources.map((source) => [source.key, { state: 'starting', last_success_at: null, last_error: null, last_reconciled_at: null }]));
	const sourceDefinitions = new Map(config.sources.map((source) => [source.key, source]));

	function rememberSources(sources) {
		const sourceKeys = new Set(sources.map((source) => source.key));
		for (const key of sourceDefinitions.keys()) {
			if (!sourceKeys.has(key)) {
				sourceDefinitions.delete(key);
				sourceRuntime.delete(key);
			}
		}
		for (const source of sources) {
			sourceDefinitions.set(source.key, source);
			if (!sourceRuntime.has(source.key)) sourceRuntime.set(source.key, { state: source.enabled === false ? 'disabled' : 'starting', last_success_at: null, last_error: null, last_reconciled_at: null });
		}
	}

	async function configuredSources() {
		const sources = config.sourcesProvider ? await config.sourcesProvider() : config.sources;
		rememberSources(sources);
		return sources;
	}

	// Outbound card JSON 2.0.  The tag leads the card text, so the summary
	// ingestion routes a relayed card by the same "#tag" it reads off a text
	// bubble; the component name changes so the deterministic uuid does too,
	// and nothing already delivered is re-sent.
	// An edit must keep the shape of the bubble it edits: a card cannot
	// patch a text or rich-text message, nor the reverse.  Builders take the
	// decision from the options so an edit to a pre-card target still builds
	// the legacy text/post payload.
	function wantsCard(options) {
		return options?.card ?? config.outboundCard !== false;
	}

	function cardPayloadFor(source, text, imageKeys = []) {
		return { component: 'card-v2', msgType: 'interactive', content: buildRelayCard({ tag: source.tag, text, imageKeys }) };
	}

	async function sendMessage({ targetChatId, messageId, component, msgType, content }) {
		const webhookUrl = config.webhooksByChatId?.get(targetChatId);
		if (webhookUrl && WEBHOOK_CAPABLE_MSG_TYPES.has(msgType)) {
			const webhookKeyword = config.webhookKeywordsByChatId?.get(targetChatId);
			// Feishu custom-bot keyword validation does not inspect Card 2.0
			// `plain_text` elements. When a keyword is configured, send the
			// portable card text as a keyword-prefixed text bubble so the bot
			// accepts it. Targets without keyword validation keep the rich card.
			const interactiveImages = msgType === 'interactive' ? cardImageKeys(content) : [];
			// A keyword-validated bot cannot inspect Card 2.0 plain_text nodes.
			// When the card also contains images, use a rich-text post so the
			// keyword remains visible and the uploaded image keys remain usable.
			const webhookMsgType = msgType === 'interactive' && webhookKeyword
				? (interactiveImages.length ? 'post' : 'text')
				: msgType;
			const webhookContent = webhookMsgType === 'post' && msgType === 'interactive'
				? interactiveWebhookPostContent(content, webhookKeyword)
				: webhookMsgType === 'text' && msgType === 'interactive'
					? { text: cardText(content) || '[interactive] 卡片未提供可转发文字内容。' }
					: content;
			await postViaWebhook(webhookUrl, webhookMsgType, webhookContentWithKeyword(webhookMsgType, webhookContent, webhookKeyword));
			// No uuid/idempotency on this path (webhooks take no such field): a
			// retried send after a timed-out response can duplicate. Acceptable
			// for a destination that opted out of edit/recall sync for quota.
			return `${WEBHOOK_SENT_PREFIX}${targetChatId}:${Date.now()}`;
		}
		const result = await larkClient.im.v1.message.create({
			params: { receive_id_type: 'chat_id' },
			data: { receive_id: targetChatId, msg_type: msgType, content: JSON.stringify(content), uuid: deterministicUuid(messageId, component, targetChatId) },
		});
		if (result.code && result.code !== 0) throw new Error(`飞书发送失败：${result.msg ?? result.code}`);
		if (!result.data?.message_id) throw new Error('飞书发送未返回 message_id');
		return result.data.message_id;
	}

	async function downloadAndUpload(message, descriptor) {
		let response;
		try {
			if (descriptor.larkagentx && config.larkAgentXResourceUrl) {
				const url = new URL(config.larkAgentXResourceUrl);
				for (const [key, value] of Object.entries(descriptor.larkagentx)) {
					if (value !== null && value !== undefined && String(value).trim()) url.searchParams.set(key, String(value));
				}
				const fetched = await fetch(url, { headers: { 'x-larkagentx-token': String(config.larkAgentXToken ?? '') } });
				if (!fetched.ok || !fetched.body) throw new Error(`LarkAgentX 图片资源 HTTP ${fetched.status}`);
				response = {
					headers: {
						'content-type': fetched.headers.get('content-type') ?? 'application/octet-stream',
						'content-length': fetched.headers.get('content-length') ?? '',
						'content-disposition': fetched.headers.get('content-disposition') ?? '',
					},
					getReadableStream: () => fetched.body,
				};
			} else {
				response = await sourceApi.messageResourceGet({ messageId: message.message_id, fileKey: descriptor.key, type: descriptor.kind });
			}
		} catch (error) {
			throw new Error(`无法读取源消息资源 ${descriptor.key}：${error?.response?.status ?? error?.message ?? 'unknown'}`);
		}
		const contentType = String(headerValue(response.headers, 'content-type')).split(';')[0];
		const filename = filenameFromHeaders(response.headers, `${descriptor.kind}-${descriptor.key}`);
		const declaredBytes = Number(headerValue(response.headers, 'content-length'));
		if (Number.isFinite(declaredBytes) && declaredBytes > MAX_SOURCE_FILE_BYTES) {
			if (!workbench?.uploadToCloud) throw new RelayUnsupportedError(`消息资源超过 ${Math.floor(MAX_SOURCE_FILE_BYTES / 1024 / 1024)} MiB 转发上限；未配置云空间归档`);
			const archived = await workbench.uploadToCloud({ readable: response.getReadableStream(), fileName: filename, size: declaredBytes });
			return { ...archived, contentType };
		}
		let bytes = await readableToBuffer(response.getReadableStream(), MAX_SOURCE_FILE_BYTES);
		if (descriptor.larkagentx && descriptor.kind === 'image') bytes = decryptLarkAgentXImage(bytes, descriptor.larkagentx);
		if (descriptor.kind === 'image' && bytes.length <= MAX_SOURCE_IMAGE_BYTES) {
			let uploaded;
			try {
				uploaded = await larkClient.im.v1.image.create({ data: { image_type: 'message', image: bytes } });
			} catch (error) {
				throw new Error(uploadErrorMessage('飞书图片上传', error));
			}
			const imageKey = uploaded?.image_key ?? uploaded?.data?.image_key;
			if (!imageKey) throw new Error('飞书图片上传未返回 image_key');
			return { kind: 'image', key: imageKey, filename };
		}
		let uploaded;
		try {
			uploaded = await larkClient.im.v1.file.create({
				data: { file_type: fileType(filename, contentType), file_name: filename, file: bytes },
			});
		} catch (error) {
			throw new Error(uploadErrorMessage('飞书文件上传', error));
		}
		const fileKey = uploaded?.file_key ?? uploaded?.data?.file_key;
		if (!fileKey) throw new Error('飞书文件上传未返回 file_key');
		return { kind: 'file', key: fileKey, filename };
	}

	async function relayPostContent(message, source, options) {
		const sourceContent = parseJson(message?.body?.content);
		const resources = collectPostResources(sourceContent);
		const replacements = { image: new Map(), file: new Map() };
		for (const resource of resources) {
			if (replacements[resource.kind].has(resource.key)) continue;
			// Rich-text images carry the same source image_key that a custom bot
			// webhook can render. Keep it in place when every target is webhook
			// backed, so an exhausted tenant upload quota does not drop the post.
			if (resource.kind === 'image' && allTargetsUseWebhooks(source, config)) {
				replacements.image.set(resource.key, resource.key);
				continue;
			}
			const uploaded = await downloadAndUpload(message, resource);
			if (resource.kind === 'image' && uploaded.kind !== 'image') {
				throw new RelayUnsupportedError('超过 10 MiB 的富文本图片不能保留为富文本图片');
			}
			replacements[resource.kind].set(resource.key, uploaded.key);
		}
		if (wantsCard(options) && resources.every((resource) => resource.kind === 'image')) {
			return cardPayloadFor(source, cardText(sourceContent), [...replacements.image.values()]);
		}
		const content = prependTagToPost(rewritePostResourceKeys(sourceContent, replacements), source.tag);
		return { component: 'post', msgType: 'post', content };
	}

	async function relayDirectResourceContent(message, source, descriptor, options) {
		// A source image key is already addressable by a custom bot webhook. When
		// every target is webhook-backed, keep that key in the outgoing post and
		// avoid the tenant image-upload API entirely. This is required when the
		// monthly im:resource:upload quota is exhausted; tenant-API targets still
		// use the decrypt-download-upload path below.
		if (descriptor.kind === 'image' && allTargetsUseWebhooks(source, config)) {
			if (wantsCard(options)) return cardPayloadFor(source, '', [descriptor.key]);
			return { component: 'image-webhook-source-key', msgType: 'post', content: { zh_cn: { title: '', content: [[{ tag: 'text', text: `#${source.tag}` }], [{ tag: 'img', image_key: descriptor.key }]] } } };
		}
		const uploaded = await downloadAndUpload(message, descriptor);
		if (uploaded.kind === 'drive') {
			return { component: 'drive-archive', msgType: 'text', content: { text: taggedText(source.tag, `大文件已归档至配置的飞书云空间文件夹：${uploaded.filename}\n文件 token：${uploaded.fileToken}`) } };
		}
		if (uploaded.kind === 'baidu_pan') {
			return { component: 'baidu-pan-archive', msgType: 'text', content: { text: taggedText(source.tag, `大文件已归档至百度网盘：${uploaded.path}\n文件 ID：${uploaded.fsId ?? 'rapid-upload'}`) } };
		}
		if (message.msg_type !== 'media' || uploaded.kind !== 'file') {
			if (uploaded.kind === 'image') {
				if (wantsCard(options)) return cardPayloadFor(source, '', [uploaded.key]);
				return { component: 'image-post', msgType: 'post', content: { zh_cn: { title: '', content: [[{ tag: 'text', text: `#${source.tag}` }], [{ tag: 'img', image_key: uploaded.key }]] } } };
			}
			return { component: 'file', msgType: 'file', content: { file_key: uploaded.key, file_name: taggedFilename(source.tag, uploaded.filename) } };
		}
		return {
			component: 'resource-post', msgType: 'post',
			content: { zh_cn: { title: '', content: [[{ tag: 'text', text: `#${source.tag}` }], [{ tag: 'media', file_key: uploaded.key }]] } },
		};
	}

	async function relayInteractiveContent(message, source, options) {
		const card = parseJson(message?.body?.content, { raw: String(message?.body?.content ?? '') });
		const summary = cardText(card).slice(0, 3_000);
		const sourceImageKeys = cardImageKeys(card);
		// Custom-bot webhooks can render the source image key directly. Keep
		// interactive-card images on that path when every destination is a
		// webhook, so a tenant upload quota cannot strip images from the card.
		if (sourceImageKeys.length && allTargetsUseWebhooks(source, config)) {
			return cardPayloadFor(source, summary, sourceImageKeys);
		}
		// A card can carry its real content as an image, which the text walk
		// cannot see.  Relay those the way rich text already does, so the
		// content arrives instead of a caption describing it.
		const images = [];
		let unreachable = 0;
		for (const resource of sourceImageKeys.map((key) => ({ key, kind: 'image' }))) {
			if (images.some((item) => item.key === resource.key)) continue;
			try {
				const uploaded = await downloadAndUpload(message, resource);
				if (uploaded.kind === 'image') images.push({ key: uploaded.key });
			} catch (error) {
				// A card image is routinely a stale key the sender's own client
				// renders locally; losing it must never cost the card's text.
				unreachable += 1;
				logger.warn(`互动卡片图片无法转发：${source.key} ${message.message_id} ${resource.key}：${error instanceof Error ? error.message : String(error)}`);
			}
		}
		if (wantsCard(options) && (images.length || summary)) {
			return cardPayloadFor(source, summary, images.map((image) => image.key));
		}
		if (images.length) {
			const lines = summary ? summary.split('\n').filter((line) => line.trim()).map((line) => [{ tag: 'text', text: line }]) : [];
			return {
				component: 'interactive-card-post',
				msgType: 'post',
				content: { zh_cn: { title: '', content: [[{ tag: 'text', text: `#${source.tag}` }], ...lines, ...images.map((image) => [{ tag: 'img', image_key: image.key }])] } },
			};
		}
		// Nothing survived: say so plainly rather than forwarding the banner.
		// The reader needs to know a card arrived and where to read it, which a
		// bare "no text" note does not tell them.
		const body = summary
			|| (unreachable
				? `卡片内容无法通过接口获取（${unreachable} 张图片资源已失效），请在源群查看原卡片。`
				: '卡片未提供可转发文字内容。');
		return { component: 'interactive-text-summary-v1', msgType: 'text', content: { text: taggedText(source.tag, `[interactive]\n${body}`) } };
	}

	async function relayPayload(message, source, options) {
		if (!message?.message_id) throw new RelayUnsupportedError('源消息没有 message_id');
		if (message.msg_type === 'post') return relayPostContent(message, source, options);
		const directResource = resourceFromDirectMessage(message);
		if (directResource) return relayDirectResourceContent(message, source, directResource, options);
		if (message.msg_type === 'text') {
			if (wantsCard(options)) return cardPayloadFor(source, messageText(message?.body?.content));
			return { component: 'text', msgType: 'text', content: { text: taggedText(source.tag, messageText(message?.body?.content)) } };
		}
		// Card components and callback values are tenant-bound, but their human
		// readable text and outbound URLs are portable.  Preserve those in a
		// normal text message so every configured source group has the same relay
		// behavior without copying unusable actions or resource keys.
		if (message.msg_type === 'interactive') return relayInteractiveContent(message, source, options);
		if (['sticker', 'share_chat', 'share_user', 'merge_forward', 'audio', 'system'].includes(message.msg_type)) {
			return { component: 'portable-summary', msgType: 'text', content: { text: taggedText(source.tag, `[${message.msg_type}]　${messageText(message?.body?.content).slice(0, 3_000) || '此消息类型无法跨租户保持原组件。'}`) } };
		}
		throw new RelayUnsupportedError(`暂不支持的飞书消息类型：${message.msg_type ?? 'unknown'}`);
	}

	function priorTargetMessages(record) {
		const values = Array.isArray(record?.target_message_ids)
			? record.target_message_ids
			: (Array.isArray(record?.targetMessageIds) ? record.targetMessageIds : []);
		return values.map((entry) => {
			if (typeof entry === 'string') return { targetChatId: null, messageId: entry, msgType: null };
			return { targetChatId: entry?.targetChatId ?? entry?.target_chat_id ?? null, messageId: entry?.messageId ?? entry?.message_id ?? null, msgType: entry?.msgType ?? entry?.msg_type ?? null };
		}).filter((entry) => entry.messageId);
	}

	async function relayOne(message, source, record = null) {
		const payload = await relayPayload(message, source);
		const completed = priorTargetMessages(record);
		const completedTargets = new Set(completed.map((entry) => entry.targetChatId).filter(Boolean));
		const pendingTargets = source.targetChatIds.filter((targetChatId) => !completedTargets.has(targetChatId));
		const results = await Promise.allSettled(pendingTargets.map(async (targetChatId) => ({
			targetChatId,
			messageId: await sendMessage({ targetChatId, messageId: message.message_id, ...payload }),
			msgType: payload.msgType,
		})));
		const targetMessageIds = [
			...completed,
			...results.filter((result) => result.status === 'fulfilled').map((result) => result.value),
		];
		const failures = results.map((result, index) => result.status === 'rejected' ? ({
			targetChatId: pendingTargets[index],
			errorMessage: result.reason instanceof Error ? result.reason.message : String(result.reason),
		}) : null).filter(Boolean);
		return { targetMessageIds, failures };
	}

	async function updateRelayedMessage(message, source, record) {
		const targetMessages = priorTargetMessages(record);
		if (!targetMessages.length) return false;
		// Targets delivered before the card upgrade have no recorded msg_type
		// and were text or rich text; only a target recorded as a card is
		// patched as a card.  Both payloads are built at most once.
		const payloads = new Map();
		const payloadFor = async (card) => {
			if (!payloads.has(card)) payloads.set(card, relayPayload(message, source, { card }));
			return payloads.get(card);
		};
		// One target that can no longer be edited (recalled in its group, for
		// instance) must not stop the edit reaching the others, so every target
		// is attempted and only a total failure is raised.
		const results = await Promise.allSettled(targetMessages.map(async ({ messageId: targetMessageId, msgType }) => {
			// A webhook-delivered copy has no application message_id to patch;
			// treat it the same as an unsupported msg_type: a no-op, not a
			// failure, so it never blocks the edit from reaching real targets.
			if (isWebhookSentinel(targetMessageId)) return false;
			const payload = await payloadFor(msgType === 'interactive');
			if (!['text', 'post', 'interactive'].includes(payload.msgType)) return false;
			const result = payload.msgType === 'interactive'
				? await larkClient.im.v1.message.patch({ path: { message_id: targetMessageId }, data: { content: JSON.stringify(payload.content) } })
				: await larkClient.im.v1.message.update({ path: { message_id: targetMessageId }, data: { msg_type: payload.msgType, content: JSON.stringify(payload.content) } });
			if (result?.code && result.code !== 0) throw new Error(`更新目标群消息失败：${result.msg ?? result.code}`);
			return true;
		}));
		const failures = results.map((result, index) => result.status === 'rejected' ? `${targetMessages[index].messageId}：${feishuErrorMessage(result.reason)}` : null).filter(Boolean);
		if (failures.length === results.length) throw new Error(failures.join('; '));
		for (const failure of failures) logger.warn(`目标消息编辑未同步：${source.key} ${message.message_id} ${failure}`);
		return results.some((result) => result.status === 'fulfilled' && result.value === true);
	}

	async function processClaimed(message, source, claimedRecord = null) {
		try {
			const delivery = await relayOne(message, source, claimedRecord);
			if (delivery.failures.length) {
				const errorMessage = delivery.failures.map((failure) => `${failure.targetChatId}: ${failure.errorMessage}`).join('; ');
				// A custom-bot keyword rejection is configuration state, not a
				// transient delivery failure. Retrying it on every poll (and again
				// after a recall) created the papa-bot duplicate loop.
				const permanent = /Key Words Not Found/i.test(errorMessage);
				await ledger.markRelayMessage(message.message_id, { status: permanent ? 'filtered_system' : 'failed', targetMessageIds: delivery.targetMessageIds, errorMessage });
				logger.error(`群消息部分转发失败：${source.key} ${message.message_id}：${errorMessage}`);
				return;
			}
			await ledger.markRelayMessage(message.message_id, { status: 'sent', targetMessageIds: delivery.targetMessageIds, errorMessage: null });
			if (message.msg_type === 'interactive') await ledger.markPortableSummaryVersion?.(message.message_id, 'interactive-text-summary-v1');
			// The relay contract is one source message -> one summary bubble.  An
			// action card is useful, but is deliberately opt-in so it never splits
			// the tagged original message by default.
			if (workbench && config.actionCardsEnabled === true) {
				const record = await ledger.getRelayMessage(message.message_id);
				await workbench.publishActionCard(record, source).catch((error) => logger.warn(`行动卡片创建失败：${source.key} ${message.message_id}：${error.message}`));
			}
			logger.info(`群消息已转发：${source.key} ${message.message_id}`);
		} catch (error) {
			const unsupported = error instanceof RelayUnsupportedError;
			await ledger.markRelayMessage(message.message_id, {
				status: unsupported ? 'unsupported' : 'failed', targetMessageIds: [], errorMessage: error instanceof Error ? error.message : String(error),
			});
			logger.error(`群消息转发${unsupported ? '不支持' : '失败'}：${source.key} ${message.message_id}：${error instanceof Error ? error.message : String(error)}`);
		}
	}

	async function processInbound(message, source, { replacePlaceholder = false } = {}) {
		if (!message?.message_id) throw new RelayUnsupportedError('实时源消息没有 message_id');
		if (!source?.key || !source?.resolvedChatId) throw new Error('实时源消息缺少已映射的 source 或 chat_id');
		if (source.targetChatIds.includes(source.resolvedChatId)) {
			throw new Error(`源群 ${source.key} 与目标群相同，拒绝实时转发以避免循环`);
		}
		const now = Date.now();
		const sourceCreateTime = asCreateTimeMs(message.create_time, now);
		const record = {
			sourceMessageId: message.message_id,
			sourceKey: source.key,
			sourceChatId: source.resolvedChatId,
			sourceCreateTime,
			sourceUpdateTime: message.update_time ? asCreateTimeMs(message.update_time, sourceCreateTime) : null,
			targetChatId: source.targetChatId,
			targetChatIds: source.targetChatIds,
			routeTag: source.tag,
			message,
		};
		if (isSystemMessage(message)) {
			await ledger.filterRelayMessage(record, '系统消息已过滤（LarkAgentX 实时入口）');
			return { status: 'filtered', message_id: message.message_id };
		}
		const blockedReason = blockedMessageReason(message, sourceFilterOptions(source));
		if (blockedReason) {
			await ledger.filterRelayMessage(record, blockedReason);
			return { status: 'filtered', message_id: message.message_id };
		}
		const existing = await ledger.getRelayMessage(message.message_id);
		const replaceExistingPlaceholder = Boolean(replacePlaceholder && existing?.status === 'sent' && isPlaceholderCardRelay(existing.message));
		if ((existing?.status === 'sent' || existing?.status === 'skipped_bootstrap' || existing?.status === 'filtered_system') && !replaceExistingPlaceholder) {
			return { status: 'duplicate', message_id: message.message_id };
		}
		if (replaceExistingPlaceholder && (!ledger.resetRelayMessageForRetry || !(await ledger.resetRelayMessageForRetry(message.message_id, 'LarkAgentX 历史卡片回放替换占位内容')))) {
			return { status: 'duplicate', message_id: message.message_id };
		}
		if (ledger.relayMessagesBySourceWindow) {
			const equivalents = await ledger.relayMessagesBySourceWindow(source.key, sourceCreateTime - 3_000, sourceCreateTime + 3_000);
			const equivalent = equivalents.find((row) => row.source_message_id !== message.message_id
				&& ['sent', 'skipped_bootstrap', 'filtered_system'].includes(row.status)
				&& relayMessagesEquivalent(row, record));
			if (equivalent) return { status: 'duplicate', message_id: message.message_id, equivalent_message_id: equivalent.source_message_id };
		}
		const claimed = await ledger.claimRelayMessage(record);
		if (!claimed) return { status: 'in_flight', message_id: message.message_id };
		await processClaimed(message, source, claimed);
		const saved = await ledger.getRelayMessage(message.message_id);
		return { status: saved?.status ?? 'processing', message_id: message.message_id, target_message_ids: saved?.target_message_ids ?? [] };
	}

	async function repairFromOfficial({ fromCreateTime, toCreateTime, sourceKeys = [], forcePlaceholderCards = false } = {}) {
		const from = asCreateTimeMs(fromCreateTime, Date.now() - 120_000);
		const to = Math.max(from, asCreateTimeMs(toCreateTime, Date.now()));
		if (to - from > 24 * 60 * 60_000) throw new Error('补读窗口不能超过 24 小时');
		const requested = new Set((Array.isArray(sourceKeys) ? sourceKeys : []).map((value) => String(value ?? '').trim()).filter(Boolean));
		const configured = await configuredSources();
		const selected = configured.filter((source) => source.enabled !== false && (!requested.size || requested.has(source.key)));
		const totals = { from, to, sources: [], fetched: 0, sent: 0, replaced_placeholders: 0, deduplicated: 0, filtered: 0, failed: 0 };
		await mapWithConcurrency(selected, sourceConcurrency, async (configuredSource) => {
			const source = await resolveSource(configuredSource);
			if (!source) {
				totals.sources.push({ key: configuredSource.key, state: 'unavailable', fetched: 0, sent: 0, deduplicated: 0, filtered: 0, failed: 1 });
				totals.failed += 1;
				return;
			}
			const stats = { key: source.key, state: 'ok', fetched: 0, sent: 0, replaced_placeholders: 0, deduplicated: 0, filtered: 0, failed: 0, message_ids: [] };
			try {
				// Do not advance the requested repair window to the latest sent row.
				// A WebSocket gap can occur before a later message that did arrive;
				// advancing past that later row would permanently hide the missed
				// message. Per-message IDs plus the bounded source-window equivalence
				// check below provide idempotency without losing earlier history.
				const effectiveFrom = from;
				let pageToken;
				for (let page = 0; page < MAX_HISTORY_PAGES; page++) {
					const result = await sourceApi.messageList({
						container_id_type: 'chat', container_id: source.resolvedChatId,
						start_time: asEpochSeconds(effectiveFrom), end_time: asEpochSeconds(to),
						sort_type: 'ByCreateTimeAsc', page_size: 50, with_sender_name: true,
						card_msg_content_type: 'user_card_content', ...(pageToken ? { page_token: pageToken } : {}),
					});
					if (result.code && result.code !== 0) throw new Error(`读取源群 ${source.key} 历史消息失败：${result.msg ?? result.code}`);
					for (const message of result.data?.items ?? []) {
						if (!message?.message_id) continue;
						const createTime = asCreateTimeMs(message.create_time, 0);
						if (createTime < effectiveFrom || createTime > to) continue;
						stats.fetched += 1;
						const record = {
							sourceMessageId: message.message_id, sourceKey: source.key, sourceChatId: source.resolvedChatId,
							sourceCreateTime: createTime, sourceUpdateTime: message.update_time ? asCreateTimeMs(message.update_time, createTime) : null,
							targetChatId: source.targetChatId, targetChatIds: source.targetChatIds, routeTag: source.tag, message,
						};
						if (isSystemMessage(message)) { stats.filtered += 1; await ledger.filterRelayMessage(record, '系统消息已过滤（官方补读回测）'); continue; }
						const blockedReason = blockedMessageReason(message, sourceFilterOptions(source));
						if (blockedReason) { stats.filtered += 1; await ledger.filterRelayMessage(record, blockedReason); continue; }
						const existing = await ledger.getRelayMessage(message.message_id);
						const replacePlaceholder = Boolean(forcePlaceholderCards && existing?.status === 'sent' && isPlaceholderCardRelay(existing.message));
						if ((existing?.status === 'sent' || existing?.status === 'skipped_bootstrap' || existing?.status === 'filtered_system') && !replacePlaceholder) { stats.deduplicated += 1; continue; }
						const equivalents = ledger.relayMessagesBySourceWindow
							? await ledger.relayMessagesBySourceWindow(source.key, createTime - 3_000, createTime + 3_000) : [];
						if (!replacePlaceholder && equivalents.some((row) => row.source_message_id !== message.message_id
							&& ['sent', 'skipped_bootstrap', 'filtered_system'].includes(row.status)
							&& relayMessagesEquivalent(row, record))) { stats.deduplicated += 1; continue; }
						if (replacePlaceholder) {
							if (!ledger.resetRelayMessageForRetry || !(await ledger.resetRelayMessageForRetry(message.message_id, '卡片占位内容改用官方完整卡片重发'))) {
								stats.failed += 1;
								continue;
							}
							stats.replaced_placeholders += 1;
						}
						const claimed = await ledger.claimRelayMessage(record);
						if (!claimed) { stats.deduplicated += 1; continue; }
						await processClaimed(message, source, claimed);
						const saved = await ledger.getRelayMessage(message.message_id);
						if (saved?.status === 'sent') stats.sent += 1;
						else stats.failed += 1;
						stats.message_ids.push({ id: message.message_id, status: saved?.status ?? 'unknown' });
					}
					if (!result.data?.has_more || !result.data?.page_token) break;
					pageToken = result.data.page_token;
				}
			} catch (error) {
				stats.state = 'error'; stats.failed += 1;
				logger.error(`群消息官方补读失败：${source.key}：${error instanceof Error ? error.message : String(error)}`);
			}
			totals.sources.push(stats);
			totals.fetched += stats.fetched; totals.sent += stats.sent; totals.replaced_placeholders += stats.replaced_placeholders; totals.deduplicated += stats.deduplicated; totals.filtered += stats.filtered; totals.failed += stats.failed;
		});
		return totals;
	}

	async function resolveSource(source) {
		// Every route retains the main summary group; route-specific targets are
		// additional fan-out destinations (for example, the dedicated #liwei group).
		const requestedTargets = [config.targetChatId, ...(Array.isArray(source.targetChatIds) ? source.targetChatIds : []), source.targetChatId].filter(Boolean);
		const targetChatIds = [...new Set(requestedTargets.map((value) => String(value ?? '').trim()).filter(Boolean))];
		const resolvedSource = { ...source, targetChatIds, targetChatId: targetChatIds[0] };
		if (resolvedSource.chatId) return { ...resolvedSource, resolvedChatId: resolvedSource.chatId };
		if (!resolvedSource.chatName) return null;
		const result = await sourceApi.chatSearch(resolvedSource.chatName);
		if (result.code && result.code !== 0) throw new Error(`无法搜索源群 ${resolvedSource.chatName}：${result.msg ?? result.code}`);
		const chat = (result.data?.items ?? []).find((item) => item.name === resolvedSource.chatName);
		return chat?.chat_id ? { ...resolvedSource, resolvedChatId: chat.chat_id } : null;
	}

	async function retryFailed(sourcesByKey, sourceKeys = []) {
		let considered = 0;
		let retried = 0;
		let sent = 0;
		let failed = 0;
		for (const record of await ledger.relayRetryQueue(sourceKeys.length ? 100 : 20, sourceKeys)) {
			considered += 1;
			const source = sourcesByKey.get(record.source_key);
			if (!source || !source.resolvedChatId) continue;
			const configuredIds = new Set([String(source.resolvedChatId), String(source.chatId ?? '')].filter(Boolean));
			// LarkAgentX stores its private numeric chat_id in the relay ledger,
			// while the route catalog stores the official oc_ id.  The source key
			// is already selected explicitly here; allow that stable numeric form
			// for retry instead of silently skipping the failed image.
			const numericDynamicId = /^\d+$/.test(String(record.source_chat_id ?? '')) && [...configuredIds].some((value) => value.startsWith('oc_'));
			if (!configuredIds.has(String(record.source_chat_id ?? '')) && !numericDynamicId) continue;
			const claimed = await ledger.claimRelayMessage({
				...record, sourceChatId: record.source_chat_id ?? source.resolvedChatId, targetChatId: source.targetChatId, targetChatIds: source.targetChatIds,
				routeTag: source.tag, message: sourceFromRecord(record),
			});
			if (claimed) {
				retried += 1;
				await processClaimed(sourceFromRecord(record), source, claimed);
				const saved = await ledger.getRelayMessage(record.source_message_id);
				if (saved?.status === 'sent') sent += 1;
				else if (saved?.status === 'failed' || saved?.status === 'unsupported') failed += 1;
			}
		}
		return { considered, retried, sent, failed };
	}

	async function retryFailedNow(sourceKeys = []) {
		const requested = new Set((Array.isArray(sourceKeys) ? sourceKeys : []).map((value) => String(value ?? '').trim()).filter(Boolean));
		const configured = await configuredSources();
		const selected = configured.filter((source) => source.enabled !== false && (!requested.size || requested.has(source.key)));
		const resolved = await Promise.all(selected.map((source) => resolveSource(source)));
		const sourcesByKey = new Map(resolved.filter(Boolean).map((source) => [source.key, source]));
		const result = await retryFailed(sourcesByKey, [...requested]);
		return { ...result, source_keys: [...sourcesByKey.keys()] };
	}

	async function upgradePortableInteractiveSummaries(sourcesByKey) {
		if (!ledger.portableInteractiveSummaryUpgradeQueue || !ledger.markPortableSummaryVersion) return;
		for (const record of await ledger.portableInteractiveSummaryUpgradeQueue(20)) {
			const source = sourcesByKey.get(record.source_key);
			if (!source || source.resolvedChatId !== record.source_chat_id) continue;
			try {
				const updated = await updateRelayedMessage(sourceFromRecord(record), source, record);
				if (!updated) throw new Error('原转发消息缺少可更新的文本目标');
				await ledger.markPortableSummaryVersion(record.source_message_id, 'interactive-text-summary-v1');
				logger.info(`互动卡片摘要已升级：${source.key} ${record.source_message_id}`);
			} catch (error) {
				logger.warn(`互动卡片摘要升级失败：${source.key} ${record.source_message_id}：${error instanceof Error ? error.message : String(error)}`);
			}
		}
	}

	async function pollSource(source) {
		if (source.targetChatIds.includes(source.resolvedChatId)) {
			logger.error(`群消息转发已跳过：源群 ${source.key} 与汇集群相同，避免循环`);
			return;
		}
		const state = await ledger.relaySourceState(source.key);
		const now = Date.now();
		const bootstrap = !state || state.chat_id !== source.resolvedChatId;
		const normalFrom = bootstrap
			? now - config.historyLookbackSeconds * 1000
			: Math.max(0, Number(state.cursor_create_time) - config.overlapSeconds * 1000);
		const runtime = sourceRuntime.get(source.key);
		const reconciling = !bootstrap && (!runtime?.last_reconciled_at || Date.now() - Date.parse(runtime.last_reconciled_at) >= reconcileEveryMs);
		// A reconcile window only ever widens the read.  After an outage longer
		// than the lookback, the persisted cursor is older than now - lookback,
		// and starting at the later bound would skip every message in between.
		const from = reconciling ? Math.max(0, Math.min(normalFrom, now - reconcileLookbackMs)) : normalFrom;
		const previousCursor = Number(state?.cursor_create_time) || 0;
		let pageToken;
		let newestCreateTime = now;
		let newestSeenCreateTime = 0;
		let truncated = false;
		for (let page = 0; page < MAX_HISTORY_PAGES; page++) {
			// Without card_msg_content_type the API renders a card into its legacy
			// 1.0 shape, and a card JSON 2.0 message - which it cannot downgrade -
			// comes back as a fixed "upgrade your client" banner plus a dead image
			// key.  user_card_content returns the JSON the sender actually posted,
			// which is the only form that carries a 2.0 card's text at all.
			const result = await sourceApi.messageList({
				container_id_type: 'chat', container_id: source.resolvedChatId, start_time: asEpochSeconds(from),
				sort_type: 'ByCreateTimeAsc', page_size: 50, with_sender_name: true, card_msg_content_type: 'user_card_content',
				...(pageToken ? { page_token: pageToken } : {}),
			});
			if (result.code && result.code !== 0) throw new Error(`读取源群 ${source.key} 历史消息失败：${result.msg ?? result.code}`);
			const pageMessages = result.data?.items ?? [];
			const existingById = new Map();
			if (ledger.getRelayMessages) {
				for (const row of await ledger.getRelayMessages(pageMessages.map((message) => message?.message_id))) {
					const rowId = row?.source_message_id ?? row?.sourceMessageId;
					if (rowId) existingById.set(String(rowId), row);
				}
			}
			for (const message of pageMessages) {
				if (!message?.message_id) continue;
				const createTime = asCreateTimeMs(message.create_time, now);
				newestCreateTime = Math.max(newestCreateTime, createTime);
				newestSeenCreateTime = Math.max(newestSeenCreateTime, createTime);
				const sourceUpdateTime = message.update_time ? asCreateTimeMs(message.update_time, createTime) : null;
				const record = {
					sourceMessageId: message.message_id, sourceKey: source.key, sourceChatId: source.resolvedChatId,
					sourceCreateTime: createTime, sourceUpdateTime, targetChatId: source.targetChatId, targetChatIds: source.targetChatIds, routeTag: source.tag, message,
				};
				// Group membership, join/leave and other system notices have no
				// analyst content. Never convert them into tagged placeholder text.
				if (isSystemMessage(message)) {
					await ledger.filterRelayMessage(record, '系统消息已过滤（如入群、退群或群设置变更）');
					continue;
				}
				const blockedReason = blockedMessageReason(message, sourceFilterOptions(source));
				if (blockedReason) {
					await ledger.filterRelayMessage(record, blockedReason);
					continue;
				}
				const existing = ledger.getRelayMessages
					? existingById.get(String(message.message_id)) ?? null
					: await ledger.getRelayMessage(message.message_id);
				if (bootstrap && config.bootstrapMode === 'skip_existing') {
					await ledger.skipRelayMessage(record);
					continue;
				}
				if (message.deleted) {
					if (existing && !existing.source_deleted) {
						const changed = await ledger.updateRelaySourceMessage(message.message_id, { message, sourceUpdateTime, sourceDeleted: true });
						await workbench?.syncSourceChange(changed, { deleted: true });
					} else if (!existing) await ledger.skipRelayMessage(record);
					continue;
				}
				if (existing && sourceEdited(message, sourceUpdateTime, existing)) {
					let originalSynced = false;
					try { originalSynced = await updateRelayedMessage(message, source, existing); }
					catch (error) { logger.warn(`同步源消息编辑失败：${source.key} ${message.message_id}：${error instanceof Error ? error.message : String(error)}`); }
					const changed = await ledger.updateRelaySourceMessage(message.message_id, { message, sourceUpdateTime, sourceDeleted: false });
					await workbench?.syncSourceChange(changed, { deleted: false, originalSynced });
					continue;
				}
				if (reconciling && !existing && createTime < normalFrom) {
					// Reconciliation must not backfill a day of old traffic after a
					// restart. It establishes durable history only for detecting later
					// edits and recalls to messages that were already forwarded.
					await ledger.skipRelayMessage(record);
					continue;
				}
				const claimed = await ledger.claimRelayMessage(record);
				if (claimed) await processClaimed(message, source, claimed);
			}
			if (!result.data?.has_more) break;
			pageToken = result.data?.page_token;
			if (!pageToken) break;
			if (page === MAX_HISTORY_PAGES - 1) truncated = true;
		}
		// A page cap with more history still unread must not jump the cursor to
		// now: the unread tail would never be requested again.  Resume from the
		// newest message actually seen, never moving the cursor backwards.
		const cursorCreateTime = truncated ? Math.max(previousCursor, newestSeenCreateTime) : newestCreateTime;
		if (truncated) logger.warn(`源群 ${source.key} 单轮读取达到 ${MAX_HISTORY_PAGES} 页上限，下一轮从已读最新消息继续`);
		await ledger.saveRelaySourceCursor({ sourceKey: source.key, chatId: source.resolvedChatId, cursorCreateTime });
		if (reconciling) {
			const previous = sourceRuntime.get(source.key) ?? {};
			sourceRuntime.set(source.key, { ...previous, last_reconciled_at: new Date().toISOString() });
		}
	}

	async function tick() {
		if (!config.enabled || running) return;
		if (canWrite) {
			try {
				const fence = await canWrite();
				writerState = fence?.allowed ? 'writer' : 'fenced';
				if (!fence?.allowed) {
					lastTickError = `relay 写入权归属 ${fence?.writer_id ?? '未知'}，当前实例仅观察`;
					lastTickCompletedAt = new Date().toISOString();
					return;
				}
			} catch (error) {
				writerState = 'error';
				lastTickError = `relay 写入围栏校验失败：${error instanceof Error ? error.message : String(error)}`;
				lastTickCompletedAt = new Date().toISOString();
				logger.error(lastTickError);
				return;
			}
		}
		if (!config.targetChatId) {
			lastTickError = '缺少 FEISHU_GROUP_RELAY_TARGET_CHAT_ID';
			logger.error('群消息转发未启动：缺少 FEISHU_GROUP_RELAY_TARGET_CHAT_ID');
			return;
		}
		running = true;
		lastTickStartedAt = new Date().toISOString();
		lastTickError = null;
		try {
			const sources = await configuredSources();
			const enabledSources = sources.filter((source) => source.enabled !== false);
			for (const source of sources) {
				if (source.enabled === false) sourceRuntime.set(source.key, { state: 'disabled', last_success_at: sourceRuntime.get(source.key)?.last_success_at ?? null, last_error: null, last_reconciled_at: sourceRuntime.get(source.key)?.last_reconciled_at ?? null });
			}
			const resolved = await Promise.all(enabledSources.map((source) => resolveSource(source)));
			const available = resolved.filter(Boolean);
			if (available.length !== enabledSources.length && Date.now() - lastUnavailableLogAt > 5 * 60 * 1000) {
				const missing = enabledSources.filter((source) => !available.some((item) => item.key === source.key)).map((source) => source.chatName ?? source.key);
				logger.warn(`群消息转发等待源群对机器人可见：${missing.join('、')}`);
				lastUnavailableLogAt = Date.now();
			}
			for (const source of enabledSources) {
				if (!available.some((item) => item.key === source.key)) sourceRuntime.set(source.key, { state: 'unavailable', last_success_at: null, last_error: '未找到或不可读取源群', last_reconciled_at: sourceRuntime.get(source.key)?.last_reconciled_at ?? null });
			}
			const sourcesByKey = new Map(available.map((source) => [source.key, source]));
			// These queues touch disjoint ledger records. Run their bounded work in
			// parallel so a portable-card upgrade cannot delay a fresh source poll.
			await Promise.all([
				retryFailed(sourcesByKey),
				upgradePortableInteractiveSummaries(sourcesByKey),
			]);
			await mapWithConcurrency(available, sourceConcurrency, async (source) => {
				try {
					await pollSource(source);
					sourceRuntime.set(source.key, { ...sourceRuntime.get(source.key), state: 'healthy', last_success_at: new Date().toISOString(), last_error: null });
				} catch (error) {
					const message = error instanceof Error ? error.message : String(error);
					sourceRuntime.set(source.key, { ...sourceRuntime.get(source.key), state: 'error', last_success_at: sourceRuntime.get(source.key)?.last_success_at ?? null, last_error: message });
					logger.error(`群消息转发轮询失败：${source.key}：${message}`);
				}
			});
		} catch (error) {
			lastTickError = error instanceof Error ? error.message : String(error);
			logger.error(`群消息转发轮询失败：${lastTickError}`);
		} finally {
			running = false;
			lastTickCompletedAt = new Date().toISOString();
		}
	}

	return {
		tick,
		processInbound,
		retryFailedNow,
		repairFromOfficial,
		status: () => ({
			running, last_tick_started_at: lastTickStartedAt, last_tick_completed_at: lastTickCompletedAt, last_tick_error: lastTickError,
			writer_state: writerState, source_concurrency: sourceConcurrency,
			sources: [...sourceDefinitions.values()].map((source) => ({ key: source.key, tag: source.tag, chat_name: source.chatName ?? source.key, ...(sourceRuntime.get(source.key) ?? { state: 'starting', last_success_at: null, last_error: null }) })),
		}),
	};
}
