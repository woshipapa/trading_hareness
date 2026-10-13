const MEMBERSHIP_TABLE = 'feishu_group_relay_memberships';
const SETTINGS_TABLE = 'feishu_group_relay_membership_settings';
const DEFAULT_TERM_MONTHS = 1;
const DEFAULT_REMIND_TEXT = '会员已到期，请联系群主续费';
const SHANGHAI = 'Asia/Shanghai';

function shanghaiDate(value) {
	const parts = new Intl.DateTimeFormat('en-US', { timeZone: SHANGHAI, year: 'numeric', month: '2-digit', day: '2-digit' }).formatToParts(value instanceof Date ? value : new Date(value));
	const fields = Object.fromEntries(parts.filter((part) => part.type !== 'literal').map((part) => [part.type, part.value]));
	return `${fields.year}-${fields.month}-${fields.day}`;
}

function parseDate(value, message = '日期必须是 YYYY-MM-DD') {
	const date = String(value ?? '');
	if (!/^\d{4}-\d{2}-\d{2}$/.test(date)) throw new Error(message);
	const [year, month, day] = date.split('-').map(Number);
	const checked = new Date(Date.UTC(year, month - 1, day));
	if (checked.getUTCFullYear() !== year || checked.getUTCMonth() !== month - 1 || checked.getUTCDate() !== day) throw new Error(message);
	return date;
}

function addMonths(value, months) {
	const [year, month, day] = parseDate(value).split('-').map(Number);
	const targetMonth = month - 1 + months;
	const targetYear = year + Math.floor(targetMonth / 12);
	const normalizedMonth = ((targetMonth % 12) + 12) % 12;
	const lastDay = new Date(Date.UTC(targetYear, normalizedMonth + 1, 0)).getUTCDate();
	return `${String(targetYear).padStart(4, '0')}-${String(normalizedMonth + 1).padStart(2, '0')}-${String(Math.min(day, lastDay)).padStart(2, '0')}`;
}

function addDays(value, days) {
	const date = new Date(`${parseDate(value)}T00:00:00Z`);
	date.setUTCDate(date.getUTCDate() + days);
	return date.toISOString().slice(0, 10);
}

function validTerm(value, label = '期限') {
	if (!Number.isInteger(value) || value < 1 || value > 120) throw new Error(`${label}必须是 1–120 的整数`);
	return value;
}

function validText(value, label, max = 500) {
	const text = String(value ?? '').trim();
	if (!text || text.length > max) throw new Error(`${label}不能为空且不能超过 ${max} 个字`);
	return text;
}

function targetId(value) {
	const id = String(value ?? '').trim();
	if (!/^\d+$/.test(id)) throw new Error('目标群没有数字群 ID');
	return id;
}

function escapedName(value) {
	return String(value ?? '').replaceAll('&', '&amp;').replaceAll('<', '&lt;').replaceAll('>', '&gt;').replaceAll('"', '&quot;');
}

function memberStatus(row, today, exempt) {
	if (exempt) return '已豁免';
	if (row.left_at) return '已离群';
	if (row.expires_on <= today) return '已到期';
	if (row.expires_on <= addDays(today, 7)) return '7 天内到期';
	return '正常';
}

export function createMemberships({ ledger, bridgePost, targetRegistry, webhookTargets, now = () => new Date() }) {
	const schema = [
		`CREATE TABLE IF NOT EXISTS ${MEMBERSHIP_TABLE} (target_chat_id text NOT NULL, user_id text NOT NULL, display_name text NOT NULL, started_on date NOT NULL, term_months integer NOT NULL, expires_on date NOT NULL, note text, reminded_on date, remind_claimed_at timestamptz, left_at timestamptz, created_at timestamptz NOT NULL DEFAULT now(), updated_at timestamptz NOT NULL DEFAULT now(), PRIMARY KEY (target_chat_id, user_id))`,
		`CREATE TABLE IF NOT EXISTS ${SETTINGS_TABLE} (target_chat_id text PRIMARY KEY, enabled boolean NOT NULL, default_term_months integer NOT NULL, remind_text text NOT NULL, exempt_user_ids jsonb NOT NULL DEFAULT '[]'::jsonb, last_review_on date)`,
	];

	async function target(chatId) {
		const found = await targetRegistry.get(String(chatId));
		if (!found) throw new Error('目标群不存在');
		if (!found.owned) throw new Error('目标群群主不是本账号，不能管理会员');
		return { ...found, websocket_chat_id: targetId(found.websocket_chat_id) };
	}

	function settingsRow(row) {
		return {
			target_chat_id: String(row.target_chat_id), enabled: row.enabled === true,
			default_term_months: Number(row.default_term_months), remind_text: String(row.remind_text),
			exempt_user_ids: Array.isArray(row.exempt_user_ids) ? row.exempt_user_ids.map(String) : [],
			last_review_on: row.last_review_on ?? null,
		};
	}

	async function settings(chatId) {
		const result = await ledger.query(`SELECT target_chat_id,enabled,default_term_months,remind_text,exempt_user_ids,last_review_on FROM ${SETTINGS_TABLE} WHERE target_chat_id=$1`, [String(chatId)]);
		if (result.rows[0]) return settingsRow(result.rows[0]);
		const defaults = { target_chat_id: String(chatId), enabled: false, default_term_months: DEFAULT_TERM_MONTHS, remind_text: DEFAULT_REMIND_TEXT, exempt_user_ids: [], last_review_on: null };
		await ledger.query(`INSERT INTO ${SETTINGS_TABLE} (target_chat_id,enabled,default_term_months,remind_text,exempt_user_ids) VALUES ($1,$2,$3,$4,$5)`, [defaults.target_chat_id, false, DEFAULT_TERM_MONTHS, DEFAULT_REMIND_TEXT, JSON.stringify([])]);
		return defaults;
	}

	async function rows(chatId) {
		const result = await ledger.query(`SELECT target_chat_id,user_id,display_name,started_on,term_months,expires_on,note,reminded_on,remind_claimed_at,left_at,created_at,updated_at FROM ${MEMBERSHIP_TABLE} WHERE target_chat_id=$1 ORDER BY started_on,user_id`, [String(chatId)]);
		return result.rows;
	}

	async function sync(chatId) {
		const found = await target(chatId);
		const config = await settings(chatId);
		const response = await bridgePost('/chat-members/list', { chat_id: found.websocket_chat_id }, 30_000);
		if (response?.status !== 'ok' || !Array.isArray(response.members)) throw new Error('成员列表响应格式无效');
		const members = response.members;
		const ids = new Set(members.map((member) => String(member.user_id)));
		const current = await rows(chatId);
		for (const member of members) {
			const userId = String(member.user_id);
			const existing = current.find((row) => String(row.user_id) === userId);
			const startedOn = existing?.started_on ?? shanghaiDate(new Date(Number(member.joined_at) * 1000));
			const expiry = existing?.expires_on ?? addMonths(startedOn, config.default_term_months);
			if (existing) {
				await ledger.query(`UPDATE ${MEMBERSHIP_TABLE} SET display_name=$3,left_at=NULL,updated_at=$4 WHERE target_chat_id=$1 AND user_id=$2`, [String(chatId), userId, String(member.name ?? ''), now()]);
			} else {
				await ledger.query(`INSERT INTO ${MEMBERSHIP_TABLE} (target_chat_id,user_id,display_name,started_on,term_months,expires_on,note,created_at,updated_at) VALUES ($1,$2,$3,$4,$5,$6,$7,$8,$8) ON CONFLICT(target_chat_id,user_id) DO NOTHING`, [String(chatId), userId, String(member.name ?? ''), startedOn, config.default_term_months, expiry, null, now()]);
			}
		}
		for (const row of current) if (!ids.has(String(row.user_id)) && !row.left_at) {
			await ledger.query(`UPDATE ${MEMBERSHIP_TABLE} SET left_at=$3,updated_at=$3 WHERE target_chat_id=$1 AND user_id=$2`, [String(chatId), String(row.user_id), now()]);
		}
		return rows(chatId);
	}

	function publicRows(items, config, today = shanghaiDate(now())) {
		const exempt = new Set(config.exempt_user_ids);
		return items.map((row) => ({ ...row, status: memberStatus(row, today, exempt.has(String(row.user_id))) }));
	}

	async function review(chatId, { dryRun = false } = {}) {
		await target(chatId);
		const config = await settings(chatId);
		if (!config.enabled) return [];
		await sync(chatId);
		const today = shanghaiDate(now());
		const claimAt = now();
		const candidateSql = `target_chat_id=$1 AND expires_on <= $2 AND left_at IS NULL AND reminded_on IS NULL AND (remind_claimed_at IS NULL OR remind_claimed_at < $3 - interval '10 minutes') AND NOT EXISTS (SELECT 1 FROM ${SETTINGS_TABLE} s WHERE s.target_chat_id=${MEMBERSHIP_TABLE}.target_chat_id AND ${MEMBERSHIP_TABLE}.user_id = ANY (SELECT jsonb_array_elements_text(s.exempt_user_ids)))`;
		if (dryRun) {
			const result = await ledger.query(`SELECT target_chat_id,user_id,display_name,started_on,term_months,expires_on,note,reminded_on,remind_claimed_at,left_at,created_at,updated_at FROM ${MEMBERSHIP_TABLE} WHERE ${candidateSql} ORDER BY user_id`, [String(chatId), today, claimAt]);
			return publicRows(result.rows, config, today);
		}
		const claimed = await ledger.query(`UPDATE ${MEMBERSHIP_TABLE} SET remind_claimed_at=$3,updated_at=$3 WHERE ${candidateSql} RETURNING target_chat_id,user_id,display_name,started_on,term_months,expires_on,note,reminded_on,remind_claimed_at,left_at,created_at,updated_at`, [String(chatId), today, claimAt]);
		const members = claimed.rows;
		for (let offset = 0; offset < members.length; offset += 20) {
			const batch = members.slice(offset, offset + 20);
			const ids = batch.map((row) => String(row.user_id));
			const text = `${batch.map((row) => `<at user_id="${String(row.user_id)}">${escapedName(row.display_name)}</at>`).join('')} ${config.remind_text}`;
			try {
				await webhookTargets.sendText(String(chatId), text);
				await ledger.query(`UPDATE ${MEMBERSHIP_TABLE} SET reminded_on=$3,remind_claimed_at=NULL,updated_at=$3 WHERE target_chat_id=$1 AND user_id = ANY($2) AND remind_claimed_at=$4`, [String(chatId), ids, today, claimAt]);
			} catch (error) {
				console.error(`会员到期提醒发送失败 target=${chatId}：${error instanceof Error ? error.message : String(error)}`);
				await ledger.query(`UPDATE ${MEMBERSHIP_TABLE} SET remind_claimed_at=NULL,updated_at=$3 WHERE target_chat_id=$1 AND user_id = ANY($2) AND remind_claimed_at=$4`, [String(chatId), ids, claimAt, claimAt]);
			}
		}
		return publicRows(members, config, today);
	}

	async function updateMember(chatId, userId, body) {
		await target(chatId);
		const startedOn = parseDate(body?.started_on);
		const termMonths = validTerm(body?.term_months);
		const note = body?.note == null ? null : String(body.note);
		if (body?.exempt !== undefined && typeof body.exempt !== 'boolean') throw new Error('exempt 必须是布尔值');
		const config = body?.exempt === undefined ? null : await settings(chatId);
		const exemptIds = config ? new Set(config.exempt_user_ids) : null;
		if (exemptIds && body.exempt) exemptIds.add(String(userId));
		if (exemptIds && !body.exempt) exemptIds.delete(String(userId));
		if (exemptIds) {
			const result = await ledger.query(`WITH updated_member AS (UPDATE ${MEMBERSHIP_TABLE} SET started_on=$3,term_months=$4,expires_on=$5,note=$6,reminded_on=NULL,remind_claimed_at=NULL,updated_at=$7 WHERE target_chat_id=$1 AND user_id=$2 RETURNING target_chat_id,user_id,display_name,started_on,term_months,expires_on,note,reminded_on,remind_claimed_at,left_at,created_at,updated_at), updated_settings AS (UPDATE ${SETTINGS_TABLE} SET exempt_user_ids=$8::jsonb WHERE target_chat_id=$1 AND EXISTS (SELECT 1 FROM updated_member) RETURNING target_chat_id) SELECT updated_member.* FROM updated_member JOIN updated_settings USING (target_chat_id)`, [String(chatId), String(userId), startedOn, termMonths, addMonths(startedOn, termMonths), note, now(), JSON.stringify([...exemptIds])]);
			if (!result.rows[0]) throw new Error('会员不存在，请先同步成员');
			return result.rows[0];
		}
		const result = await ledger.query(`UPDATE ${MEMBERSHIP_TABLE} SET started_on=$3,term_months=$4,expires_on=$5,note=$6,reminded_on=NULL,remind_claimed_at=NULL,updated_at=$7 WHERE target_chat_id=$1 AND user_id=$2 RETURNING target_chat_id,user_id,display_name,started_on,term_months,expires_on,note,reminded_on,remind_claimed_at,left_at,created_at,updated_at`, [String(chatId), String(userId), startedOn, termMonths, addMonths(startedOn, termMonths), note, now()]);
		if (!result.rows[0]) throw new Error('会员不存在，请先同步成员');
		return result.rows[0];
	}

	function exemptIds(value) {
		if (value == null) return [];
		if (!Array.isArray(value) || value.some((id) => !String(id).trim())) throw new Error('豁免成员必须是用户 ID 数组');
		return [...new Set(value.map(String))];
	}

	async function updateSettings(chatId, body) {
		await target(chatId);
		const current = await settings(chatId);
		const enabled = body?.enabled == null ? current.enabled : body.enabled;
		if (typeof enabled !== 'boolean') throw new Error('enabled 必须是布尔值');
		const term = body?.default_term_months == null ? current.default_term_months : validTerm(body.default_term_months, '默认期限');
		const text = body?.remind_text == null ? current.remind_text : validText(body.remind_text, '提醒文字');
		const ids = body?.exempt_user_ids == null ? current.exempt_user_ids : exemptIds(body.exempt_user_ids);
		const result = await ledger.query(`INSERT INTO ${SETTINGS_TABLE} (target_chat_id,enabled,default_term_months,remind_text,exempt_user_ids,last_review_on) VALUES ($1,$2,$3,$4,$5,$6) ON CONFLICT(target_chat_id) DO UPDATE SET enabled=EXCLUDED.enabled,default_term_months=EXCLUDED.default_term_months,remind_text=EXCLUDED.remind_text,exempt_user_ids=EXCLUDED.exempt_user_ids RETURNING target_chat_id,enabled,default_term_months,remind_text,exempt_user_ids,last_review_on`, [String(chatId), enabled, term, text, JSON.stringify(ids), current.last_review_on]);
		return settingsRow(result.rows[0] ?? { target_chat_id: chatId, enabled, default_term_months: term, remind_text: text, exempt_user_ids: ids, last_review_on: current.last_review_on });
	}

	async function remind(chatId, userId) {
		await target(chatId);
		const config = await settings(chatId);
		await sync(chatId);
		const today = shanghaiDate(now());
		const claimAt = now();
		const result = await ledger.query(`UPDATE ${MEMBERSHIP_TABLE} SET remind_claimed_at=$3,updated_at=$3 WHERE target_chat_id=$1 AND user_id=$2 AND expires_on <= $4 AND left_at IS NULL AND (remind_claimed_at IS NULL OR remind_claimed_at < $3 - interval '10 minutes') RETURNING target_chat_id,user_id,display_name,started_on,term_months,expires_on,note,reminded_on,remind_claimed_at,left_at,created_at,updated_at`, [String(chatId), String(userId), claimAt, today]);
		if (!result.rows[0]) throw new Error('会员未到期、已离群或正在提醒中');
		const row = result.rows[0];
		if (config.exempt_user_ids.includes(String(userId))) {
			await ledger.query(`UPDATE ${MEMBERSHIP_TABLE} SET remind_claimed_at=NULL WHERE target_chat_id=$1 AND user_id=$2`, [String(chatId), String(userId)]);
			throw new Error('该会员已豁免提醒');
		}
		const text = `<at user_id="${String(row.user_id)}">${escapedName(row.display_name)}</at> ${config.remind_text}`;
		try {
			await webhookTargets.sendText(String(chatId), text);
			await ledger.query(`UPDATE ${MEMBERSHIP_TABLE} SET reminded_on=$3,remind_claimed_at=NULL,updated_at=$3 WHERE target_chat_id=$1 AND user_id=$2 AND remind_claimed_at=$4`, [String(chatId), String(userId), today, claimAt]);
		} catch (error) {
			await ledger.query(`UPDATE ${MEMBERSHIP_TABLE} SET remind_claimed_at=NULL WHERE target_chat_id=$1 AND user_id=$2 AND remind_claimed_at=$3`, [String(chatId), String(userId), claimAt]);
			throw error;
		}
		return row;
	}

	let running = false;
	// Failed target reviews are retried in memory no more than once an hour; a successful review still records the day.
	const retryAfter = new Map();
	async function tick() {
		if (running) return;
		running = true;
		try {
			const current = now();
			const today = shanghaiDate(current);
			const [targets, configured] = await Promise.all([targetRegistry.list(), ledger.query(`SELECT target_chat_id,enabled,last_review_on FROM ${SETTINGS_TABLE} WHERE enabled=true`)]);
			const enabled = new Map(configured.rows.map((row) => [String(row.target_chat_id), row]));
			const hour = Number(new Intl.DateTimeFormat('en-US', { timeZone: SHANGHAI, hour: 'numeric', hour12: false }).format(now()));
			if (hour < 9) return;
			for (const item of targets) {
				const setting = enabled.get(String(item.chat_id));
				if (!setting || setting.last_review_on === today) continue;
				if ((retryAfter.get(String(item.chat_id))?.getTime() ?? 0) > current.getTime()) continue;
				try {
					await review(item.chat_id);
					await ledger.query(`UPDATE ${SETTINGS_TABLE} SET last_review_on=$2 WHERE target_chat_id=$1`, [String(item.chat_id), today]);
					retryAfter.delete(String(item.chat_id));
				} catch (error) {
					retryAfter.set(String(item.chat_id), new Date(current.getTime() + 60 * 60_000));
					console.error(`会员审查失败 target=${item.chat_id}：${error instanceof Error ? error.message : String(error)}`);
				}
			}
		} finally {
			running = false;
		}
	}

	async function start() {
		const timer = setInterval(() => { void tick(); }, 60_000);
		timer.unref();
		await tick();
	}

	const routes = [
		{ method: 'GET', path: '/api/group-relay/target-groups/:chatId/memberships', handle: async ({ params }) => { const found = await target(params.chatId); const config = await settings(params.chatId); return { members: publicRows(await rows(params.chatId), config), today: shanghaiDate(now()), target_chat_id: found.chat_id }; } },
		{ method: 'PUT', path: '/api/group-relay/target-groups/:chatId/memberships/:userId', handle: async ({ params, body }) => ({ member: await updateMember(params.chatId, params.userId, body) }) },
		{ method: 'GET', path: '/api/group-relay/target-groups/:chatId/membership-settings', handle: async ({ params }) => { await target(params.chatId); return { settings: await settings(params.chatId) }; } },
		{ method: 'PUT', path: '/api/group-relay/target-groups/:chatId/membership-settings', handle: async ({ params, body }) => ({ settings: await updateSettings(params.chatId, body) }) },
		{ method: 'POST', path: '/api/group-relay/target-groups/:chatId/memberships/sync', handle: async ({ params }) => { await target(params.chatId); const config = await settings(params.chatId); return { members: publicRows(await sync(params.chatId), config), today: shanghaiDate(now()) }; } },
		{ method: 'POST', path: '/api/group-relay/target-groups/:chatId/memberships/review', handle: async ({ params, body }) => ({ members: await review(params.chatId, { dryRun: body?.dry_run === true }), dry_run: body?.dry_run === true }) },
		{ method: 'POST', path: '/api/group-relay/target-groups/:chatId/memberships/:userId/remind', handle: async ({ params }) => ({ member: await remind(params.chatId, params.userId) }) },
	];

	return { schema, routes, start, sync, review, remind, updateMember, updateSettings, tick };
}

export { addMonths };
