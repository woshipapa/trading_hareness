const TARGET_TABLE = 'feishu_group_relay_target_groups';
const TARGET_COLUMNS = 'chat_id,websocket_chat_id,chat_name,bot_id,bot_name,owned,resolved_at';
const START_RETRY_MS = 60_000;
const START_RETRIES = 30;

// The table holds two kinds of row. A relay target is keyed by its target id (`oc_…`, `wh_…`) and says which group and
// bot it resolved to. A group the account owns is keyed by the group's own numeric id (chat_id = websocket_chat_id),
// whether or not it is a relay target, so that it can be managed before it has a bot.
export function createTargetRegistry({ ledger, bridgePost, webhookTargets, now = () => new Date() }) {
	const listeners = new Set();
	const schema = [`CREATE TABLE IF NOT EXISTS ${TARGET_TABLE} (chat_id text PRIMARY KEY, websocket_chat_id text, chat_name text, bot_id text, bot_name text, owned boolean NOT NULL, resolved_at timestamptz NOT NULL)`];

	async function rows() {
		const result = await ledger.query(`SELECT ${TARGET_COLUMNS} FROM ${TARGET_TABLE} ORDER BY chat_id`);
		return result.rows;
	}

	async function changed() {
		await Promise.all([...listeners].map(async (listener) => {
			try { await listener(); }
			catch { console.error('目标群变更监听失败'); }
		}));
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

	async function writeRow(row) {
		await ledger.query(`INSERT INTO ${TARGET_TABLE} (${TARGET_COLUMNS}) VALUES ($1,$2,$3,$4,$5,$6,$7)
			ON CONFLICT(chat_id) DO UPDATE SET websocket_chat_id=EXCLUDED.websocket_chat_id,chat_name=EXCLUDED.chat_name,bot_id=EXCLUDED.bot_id,bot_name=EXCLUDED.bot_name,owned=EXCLUDED.owned,resolved_at=EXCLUDED.resolved_at`,
			[row.chat_id, row.websocket_chat_id, row.chat_name, row.bot_id, row.bot_name, row.owned, row.resolved_at]);
	}

	async function record(target) {
		const row = targetRow(target);
		await writeRow(row);
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
		const joined = response.groups ?? [];
		const resolved = [];
		for (const group of joined) {
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
		const owned = joined.filter((group) => group.owned === true).map((group) => ({
			chat_id: String(group.chat_id), websocket_chat_id: String(group.chat_id), chat_name: String(group.name ?? ''),
			bot_id: null, bot_name: null, owned: true, resolved_at: now(),
		}));
		for (const row of [...resolved, ...owned]) await writeRow(row);
		await ledger.query(`DELETE FROM ${TARGET_TABLE} WHERE chat_id = websocket_chat_id AND NOT (chat_id = ANY($1::text[]))`, [owned.map((row) => row.chat_id)]);
		await changed();
		return groups();
	}

	function targetsFrom(stored) {
		return webhookTargets.list().sort((left, right) => left.chat_id.localeCompare(right.chat_id)).map((target) => {
			const row = stored.get(target.chat_id);
			const groupId = row?.websocket_chat_id ?? target.websocket_chat_id ?? null;
			// A target added a moment ago has no row of its own until the next resolve; its group's row already says who owns it.
			const group = groupId ? stored.get(String(groupId)) : null;
			return {
				...target,
				name: row?.chat_name ?? target.name ?? group?.chat_name ?? null,
				websocket_chat_id: groupId,
				bot_id: row?.bot_id ?? null,
				bot_name: row?.bot_name ?? null,
				owned: row?.owned ?? group?.owned ?? null,
			};
		});
	}

	// Relay targets only: what a message can be forwarded to.
	async function list() {
		return targetsFrom(new Map((await rows()).map((row) => [row.chat_id, row])));
	}

	// Relay targets, then the groups the account owns that are not a target yet (origin `owned`, no bot, not usable for sending).
	async function groups() {
		const all = await rows();
		const targets = targetsFrom(new Map(all.map((row) => [row.chat_id, row])));
		const taken = new Set(targets.map((target) => String(target.websocket_chat_id ?? '')));
		const owned = all.filter((row) => row.chat_id === row.websocket_chat_id && row.owned && !taken.has(row.chat_id))
			.sort((left, right) => String(left.chat_name).localeCompare(String(right.chat_name), 'zh'))
			.map((row) => ({
				chat_id: row.chat_id, name: row.chat_name, keyword: null, origin: 'owned', usable: false, managed: false,
				websocket_chat_id: row.websocket_chat_id, verified_at: null, created_at: null, bot_id: null, bot_name: null, owned: true,
			}));
		return [...targets, ...owned];
	}

	async function get(chatId) {
		return (await groups()).find((target) => target.chat_id === String(chatId)) ?? null;
	}

	async function resolveIfUnknown() {
		try {
			const result = await ledger.query(`SELECT count(*)::int AS count FROM ${TARGET_TABLE} WHERE chat_id = websocket_chat_id`);
			if (Number(result.rows[0].count) === 0) await resolve();
			return true;
		} catch (error) {
			console.error(`目标群识别失败：${error instanceof Error ? error.message : String(error)}`);
			return false;
		}
	}

	// The bridge is often still coming up when the adapter starts; a failed first resolve is tried again once a minute.
	async function start({ retryMs = START_RETRY_MS, retries = START_RETRIES } = {}) {
		if (await resolveIfUnknown()) return;
		let left = retries;
		const timer = setInterval(async () => {
			left -= 1;
			if (await resolveIfUnknown() || left <= 0) clearInterval(timer);
		}, retryMs);
		timer.unref();
	}

	function onChange(listener) {
		listeners.add(listener);
		return () => listeners.delete(listener);
	}

	const routes = [
		{ method: 'GET', path: '/api/group-relay/target-groups', handle: async () => ({ targets: await groups() }) },
		{ method: 'POST', path: '/api/group-relay/target-groups/resolve', handle: async () => ({ targets: await resolve() }) },
	];

	return { schema, routes, start, resolve, record, forget, list, groups, get, onChange };
}
