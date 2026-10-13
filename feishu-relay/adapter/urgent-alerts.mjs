const SETTING_KEY = 'default';
const DEFAULT_SETTINGS = { enabled: false, chat_id: '', user_id: '', name: '' };
const DECIMAL_ID = /^\d+$/;
const MAX_NAME_LENGTH = 120;

function settingsValue(value) {
	return { ...DEFAULT_SETTINGS, ...(value && typeof value === 'object' ? value : {}) };
}

function validateSettings(body) {
	if (!body || typeof body !== 'object' || Array.isArray(body)) throw new Error('加急设置格式无效');
	if (typeof body.enabled !== 'boolean') throw new Error('enabled 必须是布尔值');
	for (const key of ['chat_id', 'user_id', 'name']) if (typeof body[key] !== 'string') throw new Error(`${key} 必须是字符串`);
	const chatId = body.chat_id;
	const userId = body.user_id;
	const name = body.name.trim();
	if ((chatId === '') !== (userId === '')) throw new Error('chat_id 和 user_id 必须同时填写或同时留空');
	if (chatId && (!DECIMAL_ID.test(chatId) || !DECIMAL_ID.test(userId))) throw new Error('接收会话 id 必须是纯数字');
	if (chatId && !name) throw new Error('接收人名称不能为空');
	if (name.length > MAX_NAME_LENGTH) throw new Error('接收人名称过长');
	return { enabled: body.enabled, chat_id: chatId, user_id: userId, name };
}

export function createUrgentAlerts({ ledger, bridgePost, now = () => new Date() }) {
	const schema = ['CREATE TABLE IF NOT EXISTS feishu_group_relay_alert_settings(setting_key text PRIMARY KEY, value jsonb NOT NULL)'];

	async function readSettings() {
		const result = await ledger.query('SELECT value FROM feishu_group_relay_alert_settings WHERE setting_key=$1', [SETTING_KEY]);
		return settingsValue(result.rows[0]?.value);
	}

	async function saveSettings(body) {
		const settings = validateSettings(body);
		if (settings.chat_id) {
			const response = await bridgePost('/alerts/recipients', {}, 30_000);
			const matched = (response.recipients ?? []).some((recipient) => String(recipient.chat_id) === settings.chat_id && String(recipient.user_id) === settings.user_id);
			if (!matched) throw new Error('接收人不在中继可用的两人会话列表中');
		}
		await ledger.query(`INSERT INTO feishu_group_relay_alert_settings(setting_key,value) VALUES($1,$2)
			ON CONFLICT(setting_key) DO UPDATE SET value=EXCLUDED.value`, [SETTING_KEY, settings]);
		return settings;
	}

	async function send(settings, text) {
		return bridgePost('/alerts/urgent', { chat_id: settings.chat_id, user_id: settings.user_id, text }, 30_000);
	}

	async function notify(key, text) {
		const settings = await readSettings();
		if (!settings.enabled || !settings.chat_id || !settings.user_id) return false;
		try {
			await send(settings, `[relay 运维] ${text}`);
			return true;
		} catch (error) {
			// A broken relay must not throw into the health watch.
			console.error(`加急告警失败 key=${key}：${error instanceof Error ? error.message : String(error)}`);
			return false;
		}
	}

	const routes = [
		{ method: 'GET', path: '/api/group-relay/alerts/settings', handle: async () => ({ settings: await readSettings() }) },
		{ method: 'PUT', path: '/api/group-relay/alerts/settings', handle: async ({ body }) => ({ settings: await saveSettings(body) }) },
		{ method: 'GET', path: '/api/group-relay/alerts/recipients', handle: async () => bridgePost('/alerts/recipients', {}, 30_000) },
		{ method: 'POST', path: '/api/group-relay/alerts/test', handle: async () => {
			const settings = await readSettings();
			if (!settings.chat_id || !settings.user_id) throw new Error('请先选择加急接收人');
			return send(settings, `[relay 运维] 加急告警测试 ${now().toISOString()}`);
		} },
	];

	return { schema, routes, notify };
}
