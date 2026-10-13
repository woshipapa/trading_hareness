// Target groups whose custom-bot webhook is entered in the dashboard.
//
// The relay sends to a target through the two maps it was started with (chat
// id → webhook address, chat id → keyword).  Targets from the environment stay
// as they are.  A target added here is checked with one test message, stored
// with its address encrypted, and put into the same maps, so it is usable at
// once and after a restart.  The address never leaves this module.
//
// The address is either typed in (a bot somebody made by hand) or obtained
// from `bots`, which has the account's own session create the group's relay
// bot and hand back its webhook; such a target is `managed`.

import { createCipheriv, createDecipheriv, createHash, randomBytes } from 'node:crypto';

const HOOK_HOSTS = new Set(['open.feishu.cn', 'open.larksuite.com']);
const HOOK_PATH = /^\/open-apis\/bot\/v2\/hook\/[A-Za-z0-9-]{8,80}$/;
// A target the relay knows only by its webhook has no official chat id.
export const WEBHOOK_ONLY_TARGET_ID = /^wh_[a-f0-9]{24}$/;
const OFFICIAL_CHAT_ID = /^oc_[A-Za-z0-9]+$/;
export const DEFAULT_MANAGED_KEYWORD = '转发';
// What a custom bot answers when its security setting refuses the message.
const REFUSALS = new Map([
	[19001, 'webhook 地址无效，机器人可能已被删除'],
	[19021, '这个机器人开了签名校验，转发只支持关键词校验，请在机器人的安全设置里改用关键词'],
	[19022, '这个机器人开了 IP 白名单，没有放行转发服务器'],
	[19024, '关键词和机器人安全设置里的不一致'],
]);

function sanitizeRefusalMessage(value) {
	return String(value ?? '').replace(/(?:(?:https?|ftp):\/\/|www\.|open\.(?:feishu\.cn|larksuite\.com)\/)[^\s<>"']+/gi, '[已隐藏地址]');
}

function deriveKey(secret) {
	return createHash('sha256').update(`feishu-relay-webhook-target:${secret}`).digest();
}

function encrypt(value, key) {
	const iv = randomBytes(12);
	const cipher = createCipheriv('aes-256-gcm', key, iv);
	const ciphertext = Buffer.concat([cipher.update(value, 'utf8'), cipher.final()]);
	return `v1.${iv.toString('base64url')}.${cipher.getAuthTag().toString('base64url')}.${ciphertext.toString('base64url')}`;
}

function decrypt(value, key) {
	const [version, iv, tag, ciphertext] = String(value ?? '').split('.');
	if (version !== 'v1' || !iv || !tag || !ciphertext) throw new Error('保存的 webhook 地址格式无效');
	const decipher = createDecipheriv('aes-256-gcm', key, Buffer.from(iv, 'base64url'));
	decipher.setAuthTag(Buffer.from(tag, 'base64url'));
	return Buffer.concat([decipher.update(Buffer.from(ciphertext, 'base64url')), decipher.final()]).toString('utf8');
}

// Only a Feishu custom-bot address is accepted, so the form cannot be used to
// make the server call an arbitrary URL.
export function parseWebhookUrl(value) {
	let url;
	try { url = new URL(String(value ?? '').trim()); } catch { throw new Error('webhook 地址不是有效的网址'); }
	if (url.protocol !== 'https:' || !HOOK_HOSTS.has(url.hostname) || url.port || url.username || url.password || url.search || url.hash || !HOOK_PATH.test(url.pathname)) {
		throw new Error('webhook 地址必须是飞书自定义机器人的地址：https://open.feishu.cn/open-apis/bot/v2/hook/…');
	}
	return url.toString();
}

function asIso(value) {
	if (!value) return null;
	const date = value instanceof Date ? value : new Date(value);
	return Number.isNaN(date.getTime()) ? null : date.toISOString();
}

export function createWebhookTargets({ ledger, secret, webhooks, keywords, bots = null, fetchImpl = fetch, now = () => new Date(), newId = () => `wh_${randomBytes(12).toString('hex')}` }) {
	if (!secret) throw new Error('缺少用于加密 webhook 地址的应用密钥');
	const key = deriveKey(secret);
	const envIds = new Set(webhooks.keys());
	const stored = new Map();
	const unreadable = new Map();

	function publicTarget(chatId) {
		const meta = stored.get(chatId);
		return {
			chat_id: chatId, name: meta?.name ?? unreadable.get(chatId) ?? null, keyword: keywords.get(chatId) ?? null,
			origin: envIds.has(chatId) ? 'env' : 'dashboard', usable: webhooks.has(chatId), managed: Boolean(meta?.managed),
			websocket_chat_id: meta?.websocketChatId ?? null, verified_at: asIso(meta?.verifiedAt), created_at: asIso(meta?.createdAt),
		};
	}

	function apply(row, url) {
		webhooks.set(row.chat_id, url);
		keywords.set(row.chat_id, row.keyword);
		stored.set(row.chat_id, { name: row.chat_name, websocketChatId: row.websocket_chat_id ?? null, botId: row.bot_id ?? null, managed: row.managed === true, verifiedAt: row.verified_at ?? null, createdAt: row.created_at ?? null });
		unreadable.delete(row.chat_id);
	}

	// A row that cannot be decrypted (the application secret changed) is kept
	// and reported, so the dashboard can ask for the address again.
	async function load() {
		for (const row of await ledger.webhookTargets()) {
			if (envIds.has(row.chat_id)) continue;
			try { apply(row, decrypt(row.webhook_ciphertext, key)); } catch { unreadable.set(row.chat_id, row.chat_name); }
		}
		return { loaded: stored.size, unreadable: unreadable.size };
	}

	async function sendWebhook(url, text, label) {
		let response;
		// The address is in the request, so a transport error is reported without its details.
		try { response = await fetchImpl(url, { method: 'POST', headers: { 'content-type': 'application/json' }, body: JSON.stringify({ msg_type: 'text', content: { text } }), signal: AbortSignal.timeout(15_000) }); }
		catch { throw new Error(`${label}没有发出去：连不上飞书，请稍后再试`); }
		const result = await response.json().catch(() => ({}));
		if (response.ok && (!result?.code || result.code === 0)) return;
		const reason = REFUSALS.get(Number(result?.code)) ?? (sanitizeRefusalMessage(result?.msg) || `HTTP ${response.status}`);
		throw new Error(`${label}没有发出去：${reason}`);
	}

	async function sendTest(url, text) {
		return sendWebhook(url, text, '测试消息');
	}

	function checkedName(name) {
		const chatName = String(name ?? '').trim();
		if (!chatName || chatName.length > 120) throw new Error('请填写 1–120 字的目标群名称');
		return chatName;
	}

	function checkedGroupId(websocketChatId) {
		const wsId = String(websocketChatId ?? '').trim();
		if (wsId && !/^\d{6,24}$/.test(wsId)) throw new Error('websocket_chat_id 必须是数字群 ID');
		return wsId;
	}

	// One address serves one target; the address is tried before it is kept, unless it was read from the group's own
	// bot a moment ago (`verified`), which proves more than a test message does and puts nothing in the group.
	async function store({ id, chatName, word, url, wsId, botId = null, managed = false, verified = false }) {
		for (const [otherId, otherUrl] of webhooks) {
			if (otherUrl === url && otherId !== id) throw new Error(`这个 webhook 已经配置给目标群 ${stored.get(otherId)?.name ?? otherId}`);
		}
		if (!verified) await sendTest(url, `${word} 转发目标群已接入（看板发出的测试消息）`);
		const row = await ledger.saveWebhookTarget({ chatId: id || newId(), chatName, webhookCiphertext: encrypt(url, key), keyword: word, websocketChatId: wsId || null, botId, managed, verifiedAt: now() });
		apply(row, url);
		return publicTarget(row.chat_id);
	}

	// The same group entered again replaces its address instead of becoming a second target.
	const targetOfGroup = (wsId) => (wsId ? [...stored].find(([, meta]) => meta.websocketChatId === wsId)?.[0] ?? '' : '');

	async function add({ name, webhookUrl, keyword, websocketChatId = '', chatId = '' }) {
		const chatName = checkedName(name);
		const word = String(keyword ?? '').trim();
		if (!word || word.length > 40) throw new Error('请填写机器人安全设置里的关键词（1–40 字）');
		const url = parseWebhookUrl(webhookUrl);
		const wsId = checkedGroupId(websocketChatId);
		let id = String(chatId ?? '').trim();
		if (id) {
			if (envIds.has(id)) throw new Error('这个目标群由服务器环境变量配置，不能在看板里修改');
			const known = stored.has(id) || unreadable.has(id);
			if (!known && !OFFICIAL_CHAT_ID.test(id)) throw new Error('目标群不存在');
		} else {
			id = targetOfGroup(wsId);
		}
		return store({ id, chatName, word, url, wsId });
	}

	// No address is typed: the account's own session makes the group's relay bot
	// (or finds the one it made before) and its webhook comes back from there.
	async function addManaged({ name, websocketChatId, keyword = '' }) {
		if (!bots) throw new Error('这台服务没有接上 LarkAgentX，不能自动创建机器人，请手动填写 webhook 地址');
		const chatName = checkedName(name);
		const wsId = checkedGroupId(websocketChatId);
		if (!wsId) throw new Error('自动创建机器人需要从已加入的群里选目标群');
		const word = String(keyword ?? '').trim() || DEFAULT_MANAGED_KEYWORD;
		if (word.length > 40) throw new Error('关键词不能超过 40 个字');
		const bot = await bots.ensure({ chatId: wsId, keyword: word });
		const url = parseWebhookUrl(bot?.webhook);
		return { ...(await store({ id: targetOfGroup(wsId), chatName, word, url, wsId, botId: String(bot.bot_id ?? '') || null, managed: true })), bot_created: bot.created === true };
	}

	// The group already has a custom bot that someone made by hand (a listener posts through it, say). The account's
	// own session reads its address from the group, so nothing is typed and no test message is sent. The bot keeps its
	// settings, and it is not removed with the target (`managed` stays false).
	async function addExisting({ name, websocketChatId, botId }) {
		if (!bots?.adopt) throw new Error('这台服务没有接上 LarkAgentX，不能读取群里已有的机器人');
		const chatName = checkedName(name);
		const wsId = checkedGroupId(websocketChatId);
		if (!wsId) throw new Error('需要从已加入的群里选目标群');
		const bot = await bots.adopt({ chatId: wsId, botId: String(botId ?? '').trim() });
		const url = parseWebhookUrl(bot?.webhook);
		const word = String(bot?.key_words?.[0] ?? '').trim();
		const target = await store({ id: targetOfGroup(wsId), chatName, word, url, wsId, botId: String(bot.bot_id ?? '') || null, managed: false, verified: true });
		return { ...target, bot_name: String(bot.name ?? '') };
	}

	// A target still used by a live route is not removed: its messages would have nowhere to go.
	async function remove(chatId, { routes = [] } = {}) {
		const id = String(chatId ?? '').trim();
		if (envIds.has(id)) throw new Error('这个目标群由服务器环境变量配置，不能在看板里删除');
		if (!stored.has(id) && !unreadable.has(id)) return false;
		const users = routes.filter((route) => !route.archivedAt && (route.targetChatIds ?? []).includes(id));
		if (users.length) throw new Error(`还有 ${users.length} 个源群在转发到这个目标群（${users.map((route) => `#${route.tag} ${route.chatName}`).join('、')}），先在源群里去掉它`);
		// The bot the relay made goes with its target.  A bot that cannot be removed
		// (the account left the group, the session expired) does not keep the target.
		const meta = stored.get(id);
		let botRemoved = null;
		if (meta?.managed && meta.botId && meta.websocketChatId && bots) {
			botRemoved = await bots.remove({ chatId: meta.websocketChatId, botId: meta.botId }).then((result) => result?.status === 'removed', () => false);
		}
		await ledger.deleteWebhookTarget(id);
		webhooks.delete(id);
		keywords.delete(id);
		stored.delete(id);
		unreadable.delete(id);
		return { removed: true, bot_removed: botRemoved };
	}

	function list() {
		return [...new Set([...webhooks.keys(), ...unreadable.keys()])].sort().map(publicTarget);
	}

	async function sendText(chatId, text) {
		const id = String(chatId ?? '').trim();
		const url = webhooks.get(id);
		if (!url) throw new Error('目标群不存在或 webhook 不可用');
		const keyword = String(keywords.get(id) ?? '').trim();
		await sendWebhook(url, keyword ? `${keyword} ${String(text ?? '')}` : String(text ?? ''), '消息');
	}

	function webhookHashes() {
		return [...webhooks].map(([chat_id, url]) => ({ chat_id, webhook_hash: createHash('sha256').update(url, 'utf8').digest('hex') }));
	}

	return { load, add, addManaged, addExisting, remove, list, sendText, webhookHashes, has: (chatId) => webhooks.has(String(chatId ?? '')) };
}
