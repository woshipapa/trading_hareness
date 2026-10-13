const SETTING_KEY = 'default';
const DEFAULT_SETTINGS = { enabled: false, chat_id: '', user_id: '', name: '' };

function settingsValue(value) {
	return { ...DEFAULT_SETTINGS, ...(value && typeof value === 'object' ? value : {}) };
}

export function createUrgentAlerts({ ledger, bridgePost, now = () => new Date() }) {
	const schema = ['CREATE TABLE IF NOT EXISTS feishu_group_relay_alert_settings(setting_key text PRIMARY KEY, value jsonb NOT NULL)'];

	async function readSettings() {
		const result = await ledger.query('SELECT value FROM feishu_group_relay_alert_settings WHERE setting_key=$1', [SETTING_KEY]);
		return settingsValue(result.rows[0]?.value);
	}

	async function saveSettings(body) {
		const settings = {
			enabled: Boolean(body?.enabled),
			chat_id: String(body?.chat_id ?? '').trim(),
			user_id: String(body?.user_id ?? '').trim(),
			name: String(body?.name ?? '').trim(),
		};
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
