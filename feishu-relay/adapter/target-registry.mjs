const TARGET_TABLE = 'feishu_group_relay_target_groups';
const TARGET_COLUMNS = 'chat_id,websocket_chat_id,chat_name,bot_id,bot_name,owned,resolved_at';

export function createTargetRegistry({ ledger, bridgePost, webhookTargets, now = () => new Date() }) {
	const listeners = new Set();
	const schema = [`CREATE TABLE IF NOT EXISTS ${TARGET_TABLE} (chat_id text PRIMARY KEY, websocket_chat_id text, chat_name text, bot_id text, bot_name text, owned boolean NOT NULL, resolved_at timestamptz NOT NULL)`];

	async function rows() {
		const result = await ledger.query(`SELECT ${TARGET_COLUMNS} FROM ${TARGET_TABLE} ORDER BY chat_id`);
		return result.rows;
	}

	async function changed() {
		await Promise.all([...listeners].map((listener) => listener()));
	}

	function targetRow(target) {
		return {
			chat_id: String(target.chat_id),
			websocket_chat_id: target.websocket_chat_id ?? null,
			chat_name: target.chat_name ?? target.name ?? '',
			bot_id: target.bot_id ?? null,
			bot_name: target.bot_name ?? null,
			owned: Boolean(target.owned),
			resolved_at: target.resolved_at ?? now(),
		};
	}

	async function record(target) {
		const row = targetRow(target);
		await ledger.query(`INSERT INTO ${TARGET_TABLE} (${TARGET_COLUMNS}) VALUES ($1,$2,$3,$4,$5,$6,$7)
			ON CONFLICT(chat_id) DO UPDATE SET websocket_chat_id=EXCLUDED.websocket_chat_id,chat_name=EXCLUDED.chat_name,bot_id=EXCLUDED.bot_id,bot_name=EXCLUDED.bot_name,owned=EXCLUDED.owned,resolved_at=EXCLUDED.resolved_at`,
			[row.chat_id, row.websocket_chat_id, row.chat_name, row.bot_id, row.bot_name, row.owned, row.resolved_at]);
		await changed();
		return row;
	}

	async function forget(chatId) {
		await ledger.query(`DELETE FROM ${TARGET_TABLE} WHERE chat_id=$1`, [String(chatId)]);
		await changed();
	}

	async function resolve() {
		const addresses = webhookTargets.webhookHashes();
		const response = await bridgePost('/targets/resolve', { webhook_hashes: addresses.map((item) => item.webhook_hash) }, 60_000);
		const byHash = new Map(addresses.map((item) => [item.webhook_hash, item.chat_id]));
		const resolved = [];
		for (const group of response.groups ?? []) {
			for (const bot of group.bots ?? []) {
				const chatId = byHash.get(bot.webhook_hash);
				if (!chatId) continue;
				resolved.push({
					chat_id: chatId,
					websocket_chat_id: String(group.chat_id),
					chat_name: String(group.name ?? ''),
					bot_id: String(bot.bot_id),
					bot_name: String(bot.name ?? ''),
					owned: Boolean(group.owned),
					resolved_at: now(),
				});
			}
		}
		if (!resolved.length) return list();
		await ledger.query(`DELETE FROM ${TARGET_TABLE}`);
		for (const row of resolved) {
			await ledger.query(`INSERT INTO ${TARGET_TABLE} (${TARGET_COLUMNS}) VALUES ($1,$2,$3,$4,$5,$6,$7)`,
				[row.chat_id, row.websocket_chat_id, row.chat_name, row.bot_id, row.bot_name, row.owned, row.resolved_at]);
		}
		await changed();
		return list();
	}

	async function list() {
		const stored = new Map((await rows()).map((row) => [row.chat_id, row]));
		const targets = new Map(webhookTargets.list().map((target) => [target.chat_id, target]));
		for (const row of stored.values()) {
			if (!targets.has(row.chat_id)) targets.set(row.chat_id, { chat_id: row.chat_id, name: row.chat_name, keyword: null, origin: 'dashboard', usable: false, managed: false });
		}
		return [...targets.values()].sort((left, right) => left.chat_id.localeCompare(right.chat_id)).map((target) => {
			const row = stored.get(target.chat_id);
			return {
				...target,
				name: row?.chat_name ?? target.name ?? null,
				websocket_chat_id: row?.websocket_chat_id ?? target.websocket_chat_id ?? null,
				bot_id: row?.bot_id ?? null,
				bot_name: row?.bot_name ?? null,
				owned: row?.owned ?? null,
			};
		});
	}

	async function get(chatId) {
		return (await list()).find((target) => target.chat_id === String(chatId)) ?? null;
	}

	async function start() {
		try {
			const result = await ledger.query(`SELECT count(*)::int AS count FROM ${TARGET_TABLE}`);
			if (Number(result.rows[0].count) === 0) await resolve();
		} catch (error) {
			console.error(`目标群识别失败：${error instanceof Error ? error.message : String(error)}`);
		}
	}

	function onChange(listener) {
		listeners.add(listener);
		return () => listeners.delete(listener);
	}

	const routes = [
		{ method: 'GET', path: '/api/group-relay/target-groups', handle: async () => ({ targets: await list() }) },
		{ method: 'POST', path: '/api/group-relay/target-groups/resolve', handle: async () => ({ targets: await resolve() }) },
	];

	return { schema, routes, start, resolve, record, forget, list, get, onChange };
}
