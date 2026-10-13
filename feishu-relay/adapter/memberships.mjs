const MEMBERSHIP_TABLE = 'feishu_group_relay_memberships';
const SETTINGS_TABLE = 'feishu_group_relay_membership_settings';
const DEFAULT_TERM_MONTHS = 1;
const DEFAULT_REMIND_TEXT = '会员已到期，请联系群主续费';
const SHANGHAI = 'Asia/Shanghai';
const MEMBER_COLUMNS = 'target_chat_id,user_id,display_name,started_on,term_months,expires_on,note,reminded_on,remind_claimed_at,left_at,created_at,updated_at,alias,expiry_fixed';

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

// node-pg returns a `date` column as a Date at local midnight. Read it back with the local getters, the exact
// inverse, so the module compares and answers with calendar dates (`YYYY-MM-DD`) whatever the process time zone is.
function dateText(value) {
	if (!(value instanceof Date)) return value ?? null;
	return `${String(value.getFullYear()).padStart(4, '0')}-${String(value.getMonth() + 1).padStart(2, '0')}-${String(value.getDate()).padStart(2, '0')}`;
}

function memberRow(row) {
	return { ...row, started_on: dateText(row.started_on), expires_on: dateText(row.expires_on), reminded_on: dateText(row.reminded_on), alias: row.alias ?? '', expiry_fixed: row.expiry_fixed === true };
}

const NUMERALS = { 一: 1, 二: 2, 两: 2, 三: 3, 四: 4, 五: 5, 六: 6, 七: 7, 八: 8, 九: 9, 十: 10 };
const count = (text) => NUMERALS[text] ?? Number(text);
const MONTHS_AHEAD = /^\s*(?:(半年)|(一年|1年)|((?:[一1]个?)?季度?)|([一二两三四五六七八九十]|1[0-2]|[1-9])\s*(?:个月|个|月)|([一二两三四五六])(?![\u4e00-\u9fff]))/;
const WEEKS_AHEAD = /^\s*([一二两三]|[1-3])\s*周/;
const MONTHS_BEHIND = /(?:(半年)|(一年|1年)|((?:[一1]个?)?季度?)|([一二两三四五六七八九十])\s*(?:个月|个|月))\s*$/;

function monthsOf(match) {
	if (!match) return 0;
	if (match[1]) return 6;
	if (match[2]) return 12;
	if (match[3]) return match[3].includes('季') ? 3 : 0;
	return count(match[4] ?? match[5]);
}

// The date a run of digits stands for when someone writes month and day without padding (94, 108, 1012): the reading
// that falls nearest to today, in last year, this year or the next.
function nearestDate(digits, today) {
	const readings = digits.length === 2 ? [[digits[0], digits[1]]] : digits.length === 3 ? [[digits[0], digits.slice(1)], [digits.slice(0, 2), digits[2]]] : [[digits.slice(0, 2), digits.slice(2)]];
	const base = new Date(`${today}T00:00:00Z`).getTime();
	let best = null;
	for (const [monthText, dayText] of readings) {
		const month = Number(monthText);
		const day = Number(dayText);
		for (const year of [-1, 0, 1].map((shift) => Number(today.slice(0, 4)) + shift)) {
			const date = new Date(Date.UTC(year, month - 1, day));
			if (month < 1 || month > 12 || day < 1 || date.getUTCMonth() !== month - 1 || date.getUTCDate() !== day) continue;
			const distance = Math.abs(date.getTime() - base);
			if (!best || distance < best.distance) best = { distance, date: date.toISOString().slice(0, 10) };
		}
	}
	return best?.date ?? null;
}

// What an alias such as "阿明 108三个月" or "到12月底" says about a membership: a start date, a term, an expiry date.
// The alias is free text and may carry several products' dates, so these are offers for a person to pick from, never
// applied by themselves. A two-digit number counts as a date only with a term beside it.
export function aliasSuggestions(alias, today) {
	const text = String(alias ?? '');
	const found = [];
	const offer = (item) => { if (!found.some((other) => other.label === item.label)) found.push(item); };
	const short = (date) => date.slice(5);
	let dated = false;
	let used = 0;
	for (const match of text.matchAll(/到?\s*(1[0-2]|[1-9])\s*月底|(?<![0-9A-Za-z])(\d{2,4})(?![0-9A-Za-z月])/g)) {
		if (match[1]) {
			const month = Number(match[1]);
			const ends = [-1, 0, 1].map((shift) => new Date(Date.UTC(Number(today.slice(0, 4)) + shift, month, 0)).toISOString().slice(0, 10));
			const expiresOn = ends.reduce((best, date) => (Math.abs(new Date(date) - new Date(today)) < Math.abs(new Date(best) - new Date(today)) ? date : best));
			offer({ started_on: null, term_months: null, expires_on: expiresOn, label: `到 ${short(expiresOn)}` });
			continue;
		}
		const startedOn = nearestDate(match[2], today);
		if (!startedOn) continue;
		const end = match.index + match[0].length;
		const after = text.slice(end);
		const ahead = monthsOf(MONTHS_AHEAD.exec(after)) ? MONTHS_AHEAD.exec(after) : null;
		// A term written before the date ("三个月826") counts only if an earlier date has not taken it already.
		const months = monthsOf(ahead) || monthsOf(MONTHS_BEHIND.exec(text.slice(used, match.index)));
		const weekMatch = WEEKS_AHEAD.exec(ahead ? after.slice(ahead[0].length) : after);
		const weeks = count(weekMatch?.[1] ?? 0);
		if (match[2].length === 2 && !months && !weeks) continue;
		used = end + (ahead?.[0].length ?? 0) + (weekMatch?.[0].length ?? 0);
		dated = true;
		const expiresOn = weeks ? addDays(months ? addMonths(startedOn, months) : startedOn, weeks * 7) : null;
		const term = months && weeks ? ` ${months} 个月加 ${weeks} 周（到 ${short(expiresOn)}）` : months ? ` ${months} 个月` : weeks ? ` ${weeks} 周（到 ${short(expiresOn)}）` : '';
		offer({ started_on: startedOn, term_months: months || null, expires_on: expiresOn, label: `${short(startedOn)} 起${term}` });
	}
	if (!dated) {
		const months = monthsOf(/(?:(半年)|(一年|1年)|((?:[一1]个?)?季度)|([一二两三四五六七八九十])\s*个月)/.exec(text));
		if (months) offer({ started_on: null, term_months: months, expires_on: null, label: `${months} 个月` });
	}
	return found;
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
		// `alias` is the name the account gave the member in Feishu, as of the last sync. `expiry_fixed` marks an expiry date
		// that was typed instead of counted from the term (a term to the end of a month, holidays added on).
		`ALTER TABLE ${MEMBERSHIP_TABLE} ADD COLUMN IF NOT EXISTS alias text`,
		`ALTER TABLE ${MEMBERSHIP_TABLE} ADD COLUMN IF NOT EXISTS expiry_fixed boolean NOT NULL DEFAULT false`,
		`CREATE TABLE IF NOT EXISTS ${SETTINGS_TABLE} (target_chat_id text PRIMARY KEY, enabled boolean NOT NULL, default_term_months integer NOT NULL, remind_text text NOT NULL, exempt_user_ids jsonb NOT NULL DEFAULT '[]'::jsonb, last_review_on date)`,
	];

	// Membership rows belong to the group, so they are keyed by the group's numeric id and stay with it when a relay bot
	// is added to the group or removed. Rows written before 2026-10-14 were keyed by the target id; a group's rows are
	// moved the first time the group is used after a start.
	const adopted = new Set();
	async function adopt(found) {
		const key = targetId(found.websocket_chat_id);
		if (found.chat_id !== key && !adopted.has(found.chat_id)) {
			await ledger.query(`UPDATE ${MEMBERSHIP_TABLE} m SET target_chat_id=$2 WHERE m.target_chat_id=$1 AND NOT EXISTS (SELECT 1 FROM ${MEMBERSHIP_TABLE} x WHERE x.target_chat_id=$2 AND x.user_id=m.user_id)`, [found.chat_id, key]);
			await ledger.query(`UPDATE ${SETTINGS_TABLE} s SET target_chat_id=$2 WHERE s.target_chat_id=$1 AND NOT EXISTS (SELECT 1 FROM ${SETTINGS_TABLE} x WHERE x.target_chat_id=$2)`, [found.chat_id, key]);
			adopted.add(found.chat_id);
		}
		return key;
	}

	async function target(chatId) {
		const found = await targetRegistry.get(String(chatId));
		if (!found) throw new Error('目标群不存在');
		if (!found.owned) throw new Error('目标群群主不是本账号，不能管理会员');
		return { ...found, key: await adopt(found) };
	}

	// A group the account owns can be managed before it has a bot, but a reminder is sent by the group's bot.
	function sendable(found) {
		if (found.usable !== true) throw new Error('这个群还没有可用的机器人，发不了提醒：先在群列表里给它建机器人');
	}

	function settingsRow(row) {
		return {
			target_chat_id: String(row.target_chat_id), enabled: row.enabled === true,
			default_term_months: Number(row.default_term_months), remind_text: String(row.remind_text),
			exempt_user_ids: Array.isArray(row.exempt_user_ids) ? row.exempt_user_ids.map(String) : [],
			last_review_on: dateText(row.last_review_on),
		};
	}

	async function settings(key) {
		const result = await ledger.query(`SELECT target_chat_id,enabled,default_term_months,remind_text,exempt_user_ids,last_review_on FROM ${SETTINGS_TABLE} WHERE target_chat_id=$1`, [key]);
		if (result.rows[0]) return settingsRow(result.rows[0]);
		const defaults = { target_chat_id: key, enabled: false, default_term_months: DEFAULT_TERM_MONTHS, remind_text: DEFAULT_REMIND_TEXT, exempt_user_ids: [], last_review_on: null };
		await ledger.query(`INSERT INTO ${SETTINGS_TABLE} (target_chat_id,enabled,default_term_months,remind_text,exempt_user_ids) VALUES ($1,$2,$3,$4,$5)`, [defaults.target_chat_id, false, DEFAULT_TERM_MONTHS, DEFAULT_REMIND_TEXT, JSON.stringify([])]);
		return defaults;
	}

	async function rows(key) {
		const result = await ledger.query(`SELECT ${MEMBER_COLUMNS} FROM ${MEMBERSHIP_TABLE} WHERE target_chat_id=$1 ORDER BY started_on,user_id`, [key]);
		return result.rows.map(memberRow);
	}

	async function sync(chatId) {
		const { key } = await target(chatId);
		const config = await settings(key);
		const response = await bridgePost('/chat-members/list', { chat_id: key }, 30_000);
		if (response?.status !== 'ok' || !Array.isArray(response.members) || response.members.some((member) => !member || typeof member.is_bot !== 'boolean' || typeof member.is_self !== 'boolean')) throw new Error('成员列表响应格式无效');
		const members = response.members.filter((member) => !member.is_bot && !member.is_self);
		const excludedIds = response.members.filter((member) => member.is_bot || member.is_self).map((member) => String(member.user_id));
		const ids = new Set(members.map((member) => String(member.user_id)));
		const current = await rows(key);
		for (const userId of excludedIds) await ledger.query(`DELETE FROM ${MEMBERSHIP_TABLE} WHERE target_chat_id=$1 AND user_id=$2`, [key, userId]);
		for (const member of members) {
			const userId = String(member.user_id);
			const existing = current.find((row) => String(row.user_id) === userId);
			const startedOn = existing?.started_on ?? shanghaiDate(new Date(Number(member.joined_at) * 1000));
			const expiry = existing?.expires_on ?? addMonths(startedOn, config.default_term_months);
			if (existing) {
				await ledger.query(`UPDATE ${MEMBERSHIP_TABLE} SET display_name=$3,alias=$5,left_at=NULL,updated_at=$4 WHERE target_chat_id=$1 AND user_id=$2`, [key, userId, String(member.name ?? ''), now(), String(member.alias ?? '')]);
			} else {
				await ledger.query(`INSERT INTO ${MEMBERSHIP_TABLE} (target_chat_id,user_id,display_name,started_on,term_months,expires_on,note,created_at,updated_at,alias) VALUES ($1,$2,$3,$4,$5,$6,$7,$8,$8,$9) ON CONFLICT(target_chat_id,user_id) DO NOTHING`, [key, userId, String(member.name ?? ''), startedOn, config.default_term_months, expiry, null, now(), String(member.alias ?? '')]);
			}
		}
		for (const row of current) if (!excludedIds.includes(String(row.user_id)) && !ids.has(String(row.user_id)) && !row.left_at) {
			await ledger.query(`UPDATE ${MEMBERSHIP_TABLE} SET left_at=$3,updated_at=$3 WHERE target_chat_id=$1 AND user_id=$2`, [key, String(row.user_id), now()]);
		}
		return rows(key);
	}

	function publicRows(items, config, today = shanghaiDate(now())) {
		const exempt = new Set(config.exempt_user_ids);
		return items.map((row) => ({ ...row, status: memberStatus(row, today, exempt.has(String(row.user_id))), suggestions: aliasSuggestions(row.alias, today) }));
	}

	async function review(chatId, { dryRun = false } = {}) {
		const found = await target(chatId);
		const { key } = found;
		if (!dryRun) sendable(found);
		const config = await settings(key);
		await sync(chatId);
		const today = shanghaiDate(now());
		const claimAt = now();
		const candidateSql = `target_chat_id=$1 AND expires_on <= $2 AND left_at IS NULL AND reminded_on IS NULL AND (remind_claimed_at IS NULL OR remind_claimed_at < $3::timestamptz - interval '10 minutes') AND NOT EXISTS (SELECT 1 FROM ${SETTINGS_TABLE} s WHERE s.target_chat_id=${MEMBERSHIP_TABLE}.target_chat_id AND ${MEMBERSHIP_TABLE}.user_id = ANY (SELECT jsonb_array_elements_text(s.exempt_user_ids)))`;
		if (dryRun) {
			const result = await ledger.query(`SELECT ${MEMBER_COLUMNS} FROM ${MEMBERSHIP_TABLE} WHERE ${candidateSql} ORDER BY user_id`, [key, today, claimAt]);
			return publicRows(result.rows.map(memberRow), config, today);
		}
		const claimed = await ledger.query(`UPDATE ${MEMBERSHIP_TABLE} SET remind_claimed_at=$3,updated_at=$3 WHERE ${candidateSql} RETURNING ${MEMBER_COLUMNS}`, [key, today, claimAt]);
		const members = claimed.rows.map(memberRow);
		for (let offset = 0; offset < members.length; offset += 20) {
			const batch = members.slice(offset, offset + 20);
			const ids = batch.map((row) => String(row.user_id));
			const text = `${batch.map((row) => `<at user_id="${String(row.user_id)}">${escapedName(row.display_name)}</at>`).join('')} ${config.remind_text}`;
			try {
				await webhookTargets.sendText(found.chat_id, text);
				await ledger.query(`UPDATE ${MEMBERSHIP_TABLE} SET reminded_on=$3,remind_claimed_at=NULL,updated_at=$4 WHERE target_chat_id=$1 AND user_id = ANY($2) AND remind_claimed_at=$4`, [key, ids, today, claimAt]);
			} catch (error) {
				console.error(`会员到期提醒发送失败 target=${chatId}：${error instanceof Error ? error.message : String(error)}`);
				await ledger.query(`UPDATE ${MEMBERSHIP_TABLE} SET remind_claimed_at=NULL,updated_at=$3 WHERE target_chat_id=$1 AND user_id = ANY($2) AND remind_claimed_at=$4`, [key, ids, claimAt, claimAt]);
			}
		}
		return publicRows(members, config, today);
	}

	async function updateMember(chatId, userId, body) {
		const { key } = await target(chatId);
		const startedOn = parseDate(body?.started_on);
		const termMonths = validTerm(body?.term_months);
		// An expiry date that is typed wins over the term; without one the expiry is the start plus the term.
		const fixed = body?.expires_on != null && body.expires_on !== '';
		const expiresOn = fixed ? parseDate(body.expires_on, '到期日必须是 YYYY-MM-DD') : addMonths(startedOn, termMonths);
		if (expiresOn < startedOn) throw new Error('到期日不能早于开始日期');
		const note = body?.note == null ? null : String(body.note);
		if (body?.exempt !== undefined && typeof body.exempt !== 'boolean') throw new Error('exempt 必须是布尔值');
		const config = body?.exempt === undefined ? null : await settings(key);
		const exemptIds = config ? new Set(config.exempt_user_ids) : null;
		if (exemptIds && body.exempt) exemptIds.add(String(userId));
		if (exemptIds && !body.exempt) exemptIds.delete(String(userId));
		if (exemptIds) {
			const result = await ledger.query(`WITH updated_member AS (UPDATE ${MEMBERSHIP_TABLE} SET started_on=$3,term_months=$4,expires_on=$5,note=$6,reminded_on=NULL,remind_claimed_at=NULL,updated_at=$7,expiry_fixed=$9 WHERE target_chat_id=$1 AND user_id=$2 RETURNING ${MEMBER_COLUMNS}), updated_settings AS (UPDATE ${SETTINGS_TABLE} SET exempt_user_ids=$8::jsonb WHERE target_chat_id=$1 AND EXISTS (SELECT 1 FROM updated_member) RETURNING target_chat_id) SELECT updated_member.* FROM updated_member JOIN updated_settings USING (target_chat_id)`, [key, String(userId), startedOn, termMonths, expiresOn, note, now(), JSON.stringify([...exemptIds]), fixed]);
			if (!result.rows[0]) throw new Error('会员不存在，请先同步成员');
			return memberRow(result.rows[0]);
		}
		const result = await ledger.query(`UPDATE ${MEMBERSHIP_TABLE} SET started_on=$3,term_months=$4,expires_on=$5,note=$6,reminded_on=NULL,remind_claimed_at=NULL,updated_at=$7,expiry_fixed=$8 WHERE target_chat_id=$1 AND user_id=$2 RETURNING ${MEMBER_COLUMNS}`, [key, String(userId), startedOn, termMonths, expiresOn, note, now(), fixed]);
		if (!result.rows[0]) throw new Error('会员不存在，请先同步成员');
		return memberRow(result.rows[0]);
	}

	function exemptIds(value) {
		if (value == null) return [];
		if (!Array.isArray(value) || value.some((id) => !String(id).trim())) throw new Error('豁免成员必须是用户 ID 数组');
		return [...new Set(value.map(String))];
	}

	async function updateSettings(chatId, body) {
		const found = await target(chatId);
		const { key } = found;
		const current = await settings(key);
		const enabled = body?.enabled == null ? current.enabled : body.enabled;
		if (typeof enabled !== 'boolean') throw new Error('enabled 必须是布尔值');
		if (enabled && !current.enabled) sendable(found);
		const term = body?.default_term_months == null ? current.default_term_months : validTerm(body.default_term_months, '默认期限');
		const text = body?.remind_text == null ? current.remind_text : validText(body.remind_text, '提醒文字');
		const ids = body?.exempt_user_ids == null ? current.exempt_user_ids : exemptIds(body.exempt_user_ids);
		const result = await ledger.query(`INSERT INTO ${SETTINGS_TABLE} (target_chat_id,enabled,default_term_months,remind_text,exempt_user_ids,last_review_on) VALUES ($1,$2,$3,$4,$5,$6) ON CONFLICT(target_chat_id) DO UPDATE SET enabled=EXCLUDED.enabled,default_term_months=EXCLUDED.default_term_months,remind_text=EXCLUDED.remind_text,exempt_user_ids=EXCLUDED.exempt_user_ids RETURNING target_chat_id,enabled,default_term_months,remind_text,exempt_user_ids,last_review_on`, [key, enabled, term, text, JSON.stringify(ids), current.last_review_on]);
		return settingsRow(result.rows[0] ?? { target_chat_id: key, enabled, default_term_months: term, remind_text: text, exempt_user_ids: ids, last_review_on: current.last_review_on });
	}

	async function remind(chatId, userId) {
		const found = await target(chatId);
		const { key } = found;
		sendable(found);
		const config = await settings(key);
		await sync(chatId);
		const today = shanghaiDate(now());
		const claimAt = now();
		const result = await ledger.query(`UPDATE ${MEMBERSHIP_TABLE} SET remind_claimed_at=$3,updated_at=$3 WHERE target_chat_id=$1 AND user_id=$2 AND expires_on <= $4 AND left_at IS NULL AND (remind_claimed_at IS NULL OR remind_claimed_at < $3::timestamptz - interval '10 minutes') RETURNING ${MEMBER_COLUMNS}`, [key, String(userId), claimAt, today]);
		if (!result.rows[0]) throw new Error('会员未到期、已离群或正在提醒中');
		const row = memberRow(result.rows[0]);
		if (config.exempt_user_ids.includes(String(userId))) {
			await ledger.query(`UPDATE ${MEMBERSHIP_TABLE} SET remind_claimed_at=NULL WHERE target_chat_id=$1 AND user_id=$2`, [key, String(userId)]);
			throw new Error('该会员已豁免提醒');
		}
		const text = `<at user_id="${String(row.user_id)}">${escapedName(row.display_name)}</at> ${config.remind_text}`;
		try {
			await webhookTargets.sendText(found.chat_id, text);
			await ledger.query(`UPDATE ${MEMBERSHIP_TABLE} SET reminded_on=$3,remind_claimed_at=NULL,updated_at=$4 WHERE target_chat_id=$1 AND user_id=$2 AND remind_claimed_at=$4`, [key, String(userId), today, claimAt]);
		} catch (error) {
			await ledger.query(`UPDATE ${MEMBERSHIP_TABLE} SET remind_claimed_at=NULL WHERE target_chat_id=$1 AND user_id=$2 AND remind_claimed_at=$3`, [key, String(userId), claimAt]);
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
			const hour = Number(new Intl.DateTimeFormat('en-US', { timeZone: SHANGHAI, hour: 'numeric', hour12: false }).format(now()));
			if (hour < 9) return;
			const managed = (await targetRegistry.groups()).filter((item) => item.owned === true && /^\d+$/.test(String(item.websocket_chat_id ?? '')));
			const keys = new Map();
			for (const item of managed) keys.set(item.chat_id, await adopt(item));
			const configured = await ledger.query(`SELECT target_chat_id,enabled,last_review_on FROM ${SETTINGS_TABLE} WHERE enabled=true`);
			const enabled = new Map(configured.rows.map((row) => [String(row.target_chat_id), row]));
			for (const item of managed) {
				const key = keys.get(item.chat_id);
				const setting = enabled.get(key);
				if (!setting || dateText(setting.last_review_on) === today) continue;
				if ((retryAfter.get(key)?.getTime() ?? 0) > current.getTime()) continue;
				try {
					await review(item.chat_id);
					await ledger.query(`UPDATE ${SETTINGS_TABLE} SET last_review_on=$2 WHERE target_chat_id=$1`, [key, today]);
					retryAfter.delete(key);
				} catch (error) {
					retryAfter.set(key, new Date(current.getTime() + 60 * 60_000));
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
		{ method: 'GET', path: '/api/group-relay/target-groups/:chatId/memberships', handle: async ({ params }) => { const found = await target(params.chatId); const config = await settings(found.key); return { members: publicRows(await rows(found.key), config), today: shanghaiDate(now()), target_chat_id: found.chat_id }; } },
		{ method: 'PUT', path: '/api/group-relay/target-groups/:chatId/memberships/:userId', handle: async ({ params, body }) => ({ member: await updateMember(params.chatId, params.userId, body) }) },
		{ method: 'GET', path: '/api/group-relay/target-groups/:chatId/membership-settings', handle: async ({ params }) => { const found = await target(params.chatId); return { settings: await settings(found.key) }; } },
		{ method: 'PUT', path: '/api/group-relay/target-groups/:chatId/membership-settings', handle: async ({ params, body }) => ({ settings: await updateSettings(params.chatId, body) }) },
		{ method: 'POST', path: '/api/group-relay/target-groups/:chatId/memberships/sync', handle: async ({ params }) => { const found = await target(params.chatId); const config = await settings(found.key); return { members: publicRows(await sync(params.chatId), config), today: shanghaiDate(now()) }; } },
		{ method: 'POST', path: '/api/group-relay/target-groups/:chatId/memberships/review', handle: async ({ params, body }) => ({ members: await review(params.chatId, { dryRun: body?.dry_run === true }), dry_run: body?.dry_run === true }) },
		{ method: 'POST', path: '/api/group-relay/target-groups/:chatId/memberships/:userId/remind', handle: async ({ params }) => ({ member: await remind(params.chatId, params.userId) }) },
	];

	return { schema, routes, start, sync, review, remind, updateMember, updateSettings, tick };
}

export { addMonths };
