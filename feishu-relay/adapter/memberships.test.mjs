import assert from 'node:assert/strict';
import test from 'node:test';
import { addMonths, aliasSuggestions, createMemberships } from './memberships.mjs';

const CHAT = 'oc_target';
const WS = '7696053062053530578';

function date(value) { return value instanceof Date ? value : new Date(value.includes('T') ? `${value}${value.endsWith('Z') ? '' : 'Z'}` : `${value}T01:00:00Z`); }

function fakeLedger({ members = [], settings = null } = {}) {
	const rows = members.map((row) => ({ target_chat_id: CHAT, created_at: date('2026-01-01'), updated_at: date('2026-01-01'), ...row }));
	const config = settings ?? { target_chat_id: CHAT, enabled: true, default_term_months: 1, remind_text: '请续费', exempt_user_ids: [], last_review_on: null };
	// node-pg returns a `date` column as a Date at local midnight, not as text; the fake answers in the same shape.
	const pgDate = (value) => (typeof value === 'string' ? new Date(Number(value.slice(0, 4)), Number(value.slice(5, 7)) - 1, Number(value.slice(8, 10))) : value);
	const clone = (row) => ({ ...row, started_on: pgDate(row.started_on), expires_on: pgDate(row.expires_on), reminded_on: pgDate(row.reminded_on) });
	const settingsClone = (row) => ({ ...row, last_review_on: pgDate(row.last_review_on) });
	return {
		rows,
		config,
		async query(sql, params = []) {
			if (sql.startsWith('UPDATE feishu_group_relay_memberships m SET target_chat_id')) { for (const row of rows) if (row.target_chat_id === params[0]) row.target_chat_id = params[1]; return { rows: [] }; }
			if (sql.startsWith('UPDATE feishu_group_relay_membership_settings s SET target_chat_id')) { if (config.target_chat_id === params[0]) config.target_chat_id = params[1]; return { rows: [] }; }
			if (sql.startsWith('SELECT target_chat_id,enabled,last_review_on')) return config.enabled ? { rows: [{ target_chat_id: config.target_chat_id, enabled: true, last_review_on: pgDate(config.last_review_on) }] } : { rows: [] };
			if (sql.startsWith('SELECT target_chat_id,enabled,default_term_months')) return { rows: [settingsClone(config)] };
			if (sql.startsWith('INSERT INTO feishu_group_relay_membership_settings')) { Object.assign(config, { target_chat_id: params[0], enabled: params[1], default_term_months: params[2], remind_text: params[3], exempt_user_ids: JSON.parse(params[4]), last_review_on: params[5] ?? config.last_review_on }); return { rows: [settingsClone(config)] }; }
			if (sql.startsWith('UPDATE feishu_group_relay_membership_settings')) { config.last_review_on = params[1]; return { rows: [] }; }
			if (sql.startsWith('SELECT target_chat_id,user_id')) return { rows: rows.filter((row) => row.target_chat_id === String(params[0])).map(clone) };
			if (sql.startsWith('INSERT INTO feishu_group_relay_memberships')) {
				if (!rows.some((row) => row.user_id === params[1])) rows.push({ target_chat_id: params[0], user_id: params[1], display_name: params[2], started_on: params[3], term_months: params[4], expires_on: params[5], note: params[6], reminded_on: null, remind_claimed_at: null, left_at: null, alias: params[8], expiry_fixed: false, created_at: params[7], updated_at: params[7] });
				return { rows: [] };
			}
			if (sql.startsWith('UPDATE feishu_group_relay_memberships SET display_name')) { const row = rows.find((item) => item.user_id === params[1]); Object.assign(row, { display_name: params[2], alias: params[4], left_at: null, updated_at: params[3] }); return { rows: [] }; }
			if (sql.startsWith('UPDATE feishu_group_relay_memberships SET left_at')) { const row = rows.find((item) => item.user_id === params[1]); Object.assign(row, { left_at: params[2], updated_at: params[2] }); return { rows: [] }; }
			if (sql.startsWith('DELETE FROM feishu_group_relay_memberships')) { const index = rows.findIndex((row) => row.target_chat_id === String(params[0]) && row.user_id === String(params[1])); if (index >= 0) rows.splice(index, 1); return { rows: [] }; }
			if (sql.startsWith('UPDATE feishu_group_relay_memberships SET started_on')) { const row = rows.find((item) => item.user_id === params[1]); if (!row) return { rows: [] }; Object.assign(row, { started_on: params[2], term_months: params[3], expires_on: params[4], note: params[5], reminded_on: null, remind_claimed_at: null, updated_at: params[6], expiry_fixed: params[7] }); return { rows: [clone(row)] }; }
			if (sql.startsWith('WITH updated_member AS')) { const row = rows.find((item) => item.user_id === params[1]); if (!row) return { rows: [] }; Object.assign(row, { started_on: params[2], term_months: params[3], expires_on: params[4], note: params[5], reminded_on: null, remind_claimed_at: null, updated_at: params[6], expiry_fixed: params[8] }); config.exempt_user_ids = JSON.parse(params[7]); return { rows: [clone(row)] }; }
			if (sql.startsWith('UPDATE feishu_group_relay_memberships SET remind_claimed_at=$3')) {
				const [chatId, second, third, fourth] = params;
				const manual = sql.includes('user_id=$2');
				const ids = manual ? [String(second)] : null;
				const today = manual ? fourth : second;
				const claim = third;
				const matching = rows.filter((row) => row.target_chat_id === String(chatId) && (!ids || ids.includes(String(row.user_id))) && (!row.left_at) && row.expires_on <= today && (row.reminded_on == null) && (!row.remind_claimed_at || row.remind_claimed_at < new Date(claim.getTime() - 10 * 60_000)) && (!config.exempt_user_ids.includes(String(row.user_id))));
				for (const row of matching) row.remind_claimed_at = claim;
				return { rows: matching.map(clone) };
			}
			if (sql.startsWith('UPDATE feishu_group_relay_memberships SET reminded_on')) { const [chatId, idsOrUser, today, claim] = params; const ids = (Array.isArray(idsOrUser) ? idsOrUser : [idsOrUser]).map(String); const matching = rows.filter((row) => row.target_chat_id === String(chatId) && ids.includes(String(row.user_id)) && row.remind_claimed_at?.getTime() === claim.getTime()); for (const row of matching) Object.assign(row, { reminded_on: today, remind_claimed_at: null, updated_at: today }); return { rows: [] }; }
			if (sql.startsWith('UPDATE feishu_group_relay_memberships SET remind_claimed_at=NULL')) { const [chatId, idsOrUser, third] = params; const ids = (Array.isArray(idsOrUser) ? idsOrUser : [idsOrUser]).map(String); for (const row of rows) if (row.target_chat_id === String(chatId) && ids.includes(String(row.user_id)) && (!third || row.remind_claimed_at?.getTime() === third.getTime())) row.remind_claimed_at = null; return { rows: [] }; }
			throw new Error(`unexpected query: ${sql}`);
		},
	};
}

function setup(options = {}) {
	const ledger = fakeLedger(options);
	const sent = [];
	const members = options.bridgeMembers ?? [{ user_id: 'u1', name: '张<三"', joined_at: Math.floor(date('2026-01-31').getTime() / 1000), is_bot: false, is_self: false }];
	const bridgePost = options.bridgePost ?? (async () => ({ status: 'ok', chat_id: WS, members }));
	const group = { chat_id: CHAT, websocket_chat_id: WS, owned: true, usable: options.usable ?? true };
	const targetRegistry = { get: async () => group, groups: async () => [group] };
	const webhookTargets = { sendText: async (chatId, text) => { sent.push({ chatId, text }); if (options.sendError) throw new Error(options.sendError); } };
	let current = options.now ?? date('2026-03-01');
	const feature = createMemberships({ ledger, bridgePost, targetRegistry, webhookTargets, now: () => current });
	return { feature, ledger, sent, setNow: (value) => { current = date(value); } };
}

test('month ends and leap years clamp to the last day', () => {
	assert.equal(addMonths('2026-01-31', 1), '2026-02-28');
	assert.equal(addMonths('2024-01-31', 1), '2024-02-29');
	assert.equal(addMonths('2024-02-29', 12), '2025-02-28');
});

test('sync registers a new member on the Shanghai join date with the default term', async () => {
	const { feature, ledger } = setup({ settings: { target_chat_id: CHAT, enabled: true, default_term_months: 3, remind_text: '请续费', exempt_user_ids: [], last_review_on: null } });
	await feature.sync(CHAT);
	assert.equal(ledger.rows[0].started_on, '2026-01-31');
	assert.equal(ledger.rows[0].expires_on, '2026-04-30');
	const syncRoute = feature.routes.find((route) => route.path.endsWith('/memberships/sync'));
	const response = await syncRoute.handle({ params: { chatId: CHAT } });
	assert.equal(response.members[0].user_id, 'u1');
});

test('sync rejects malformed bridge answers before changing stored membership rows', async () => {
	const { feature, ledger } = setup({ members: [{ user_id: 'u1', display_name: 'A', started_on: '2026-01-01', term_months: 1, expires_on: '2026-02-01', reminded_on: null, remind_claimed_at: null, left_at: null }] });
	const malformed = createMemberships({ ledger, bridgePost: async () => ({ status: 'error' }), targetRegistry: { get: async () => ({ chat_id: CHAT, websocket_chat_id: WS, owned: true, usable: true }) }, webhookTargets: { sendText: async () => {} }, now: () => date('2026-03-01') });
	await assert.rejects(() => malformed.sync(CHAT), /成员列表响应格式无效/);
	assert.equal(ledger.rows[0].left_at, null);
});

test('sync excludes bots and the relay account from membership rows', async () => {
	const { feature, ledger } = setup({ bridgeMembers: [
		{ user_id: 'u1', name: '会员', joined_at: 1, is_bot: false, is_self: false },
		{ user_id: 'bot-1', name: '机器人', joined_at: 1, is_bot: true, is_self: false },
		{ user_id: 'self-1', name: '中继账号', joined_at: 1, is_bot: false, is_self: true },
	] });
	await feature.sync(CHAT);
	assert.deepEqual(ledger.rows.map((row) => row.user_id), ['u1']);
});

test('sync removes legacy rows for bots and the relay account', async () => {
	const { feature, ledger } = setup({ members: [
		{ user_id: 'bot-1', display_name: '机器人', started_on: '2026-01-01', term_months: 1, expires_on: '2026-02-01', reminded_on: null, remind_claimed_at: null, left_at: null },
		{ user_id: 'self-1', display_name: '中继账号', started_on: '2026-01-01', term_months: 1, expires_on: '2026-02-01', reminded_on: null, remind_claimed_at: null, left_at: null },
	], bridgeMembers: [
		{ user_id: 'bot-1', name: '机器人', joined_at: 1, is_bot: true, is_self: false },
		{ user_id: 'self-1', name: '中继账号', joined_at: 1, is_bot: false, is_self: true },
	] });
	await feature.sync(CHAT);
	assert.equal(ledger.rows.length, 0);
});

test('sync rejects a bridge answer without bot or self flags before changing rows', async () => {
	const { feature, ledger } = setup({ bridgeMembers: [{ user_id: 'u1', name: '会员', joined_at: 1 }] });
	await assert.rejects(() => feature.sync(CHAT), /成员列表响应格式无效/);
	assert.equal(ledger.rows.length, 0);
});

test('editing a member recomputes expiry and clears its reminder', async () => {
	const { feature, ledger } = setup({ members: [{ user_id: 'u1', display_name: 'A', started_on: '2026-01-01', term_months: 1, expires_on: '2026-02-01', reminded_on: '2026-02-02', remind_claimed_at: null, left_at: null }] });
	const updated = await feature.updateMember(CHAT, 'u1', { started_on: '2026-01-31', term_months: 1, note: '续费' });
	assert.equal(updated.expires_on, '2026-02-28');
	assert.equal(updated.reminded_on, null);
});

test('editing a member can atomically update its exemption setting', async () => {
	const { feature, ledger } = setup({ members: [{ user_id: 'u1', display_name: 'A', started_on: '2026-01-01', term_months: 1, expires_on: '2026-02-01', reminded_on: null, remind_claimed_at: null, left_at: null }] });
	await feature.updateMember(CHAT, 'u1', { started_on: '2026-01-01', term_months: 2, note: null, exempt: true });
	assert.deepEqual(ledger.config.exempt_user_ids, ['u1']);
	assert.equal(ledger.rows[0].term_months, 2);
	await assert.rejects(() => feature.updateMember(CHAT, 'u1', { started_on: 'bad', term_months: 0, note: null, exempt: 'yes' }), /日期必须是/);
});

test('review only claims expired, present, unreminded, non-exempt members and escapes names', async () => {
	const { feature, sent } = setup({
		members: [
			{ user_id: 'u1', display_name: '张<三"', started_on: '2026-01-01', term_months: 1, expires_on: '2026-02-01', reminded_on: null, remind_claimed_at: null, left_at: null },
			{ user_id: 'u2', display_name: '已提醒', started_on: '2026-01-01', term_months: 1, expires_on: '2026-02-01', reminded_on: '2026-02-02', remind_claimed_at: null, left_at: null },
			{ user_id: 'u3', display_name: '离群', started_on: '2026-01-01', term_months: 1, expires_on: '2026-02-01', reminded_on: null, remind_claimed_at: null, left_at: date('2026-02-10') },
			{ user_id: 'u4', display_name: '豁免', started_on: '2026-01-01', term_months: 1, expires_on: '2026-02-01', reminded_on: null, remind_claimed_at: null, left_at: null },
		],
		settings: { target_chat_id: CHAT, enabled: true, default_term_months: 1, remind_text: '请续费', exempt_user_ids: ['u4'], last_review_on: null },
	});
	await feature.review(CHAT);
	assert.equal(sent.length, 1);
	assert.match(sent[0].text, /&lt;三&quot;/);
	assert.ok(!sent[0].text.includes('<三"'));
});

test('two concurrent reviews claim a member once', async () => {
	// The fake ledger enforces the claim predicate; the real check is done against the live database after release.
	const { feature, sent } = setup({ members: [{ user_id: 'u1', display_name: 'A', started_on: '2026-01-01', term_months: 1, expires_on: '2026-02-01', reminded_on: null, remind_claimed_at: null, left_at: null }] });
	await Promise.all([feature.review(CHAT), feature.review(CHAT)]);
	assert.equal(sent.length, 1);
});

test('failed batches are retried, successful batches are not', async () => {
	const setupResult = setup({ sendError: '飞书拒绝' });
	await setupResult.feature.review(CHAT);
	assert.equal(setupResult.sent.length, 1);
	setupResult.setNow('2026-03-02');
	setupResult.feature = createMemberships({ ledger: setupResult.ledger, bridgePost: async () => ({ status: 'ok', members: [{ user_id: 'u1', name: 'A', joined_at: 1, is_bot: false, is_self: false }] }), targetRegistry: { get: async () => ({ chat_id: CHAT, websocket_chat_id: WS, owned: true, usable: true }) }, webhookTargets: { sendText: async () => { setupResult.sent.push('retry'); } }, now: () => date('2026-03-02') });
	await setupResult.feature.review(CHAT);
	assert.equal(setupResult.sent.length, 2);
});

test('more than 20 members are sent in several batches', async () => {
	const members = Array.from({ length: 21 }, (_, index) => ({ user_id: `u${index}`, display_name: `会员${index}`, started_on: '2026-01-01', term_months: 1, expires_on: '2026-02-01', reminded_on: null, remind_claimed_at: null, left_at: null }));
	const { feature, sent } = setup({ members, bridgeMembers: members.map((row) => ({ user_id: row.user_id, name: row.display_name, joined_at: 1, is_bot: false, is_self: false })) });
	await feature.review(CHAT);
	assert.equal(sent.length, 2);
});

test('disabled targets are not reviewed and scheduler observes Shanghai time and late starts', async () => {
	const disabled = setup({ settings: { target_chat_id: CHAT, enabled: false, default_term_months: 1, remind_text: '请续费', exempt_user_ids: [], last_review_on: null } });
	await disabled.feature.tick();
	assert.equal(disabled.sent.length, 0);
	const before = setup();
	before.setNow('2026-03-01T00:30:00');
	await before.feature.tick();
	assert.equal(before.sent.length, 0);
	const after = setup();
	after.setNow('2026-03-01T01:30:00');
	await after.feature.tick();
	assert.equal(after.sent.length, 1);
	assert.equal(after.ledger.config.last_review_on, '2026-03-01');
});

test('scheduler throttles a failed target review for one hour without losing the day', async () => {
	let calls = 0;
	const state = setup({
		now: date('2026-03-01T01:00:00Z'),
		bridgePost: async () => { calls += 1; if (calls === 1) throw new Error('bridge down'); return { status: 'ok', members: [] }; },
	});
	await state.feature.tick();
	await state.feature.tick();
	assert.equal(calls, 1);
	state.setNow('2026-03-01T02:00:00Z');
	await state.feature.tick();
	assert.equal(calls, 2);
});

test('manual remind refuses an active, departed, or freshly claimed member', async () => {
	const { feature } = setup({ members: [{ user_id: 'u1', display_name: 'A', started_on: '2026-03-01', term_months: 1, expires_on: '2026-04-01', reminded_on: null, remind_claimed_at: null, left_at: null }] });
	await assert.rejects(() => feature.remind(CHAT, 'u1'), /未到期/);
	const departed = setup({ members: [{ user_id: 'u1', display_name: 'A', started_on: '2026-01-01', term_months: 1, expires_on: '2026-02-01', reminded_on: null, remind_claimed_at: null, left_at: date('2026-02-02') }], bridgeMembers: [] });
	await assert.rejects(() => departed.feature.remind(CHAT, 'u1'), /已离群/);
	const fresh = setup({ now: date('2026-03-01T01:00:00Z'), members: [{ user_id: 'u1', display_name: 'A', started_on: '2026-01-01', term_months: 1, expires_on: '2026-02-01', reminded_on: null, remind_claimed_at: date('2026-03-01T00:55:00Z'), left_at: null }] });
	await assert.rejects(() => fresh.feature.remind(CHAT, 'u1'), /正在提醒中/);
});

test('term values reject zero, negative, and fractional numbers', async () => {
	const { feature } = setup();
	for (const value of [0, -1, 1.5]) await assert.rejects(() => feature.updateSettings(CHAT, { default_term_months: value }), /默认期限/);
	for (const value of [0, -1, 1.5]) await assert.rejects(() => feature.updateMember(CHAT, 'u1', { started_on: '2026-01-01', term_months: value }), /期限/);
});

const expired = { user_id: 'u1', display_name: '甲', started_on: '2026-01-31', term_months: 1, expires_on: '2026-02-28', note: null, reminded_on: null, remind_claimed_at: null, left_at: null };
const off = { target_chat_id: CHAT, enabled: false, default_term_months: 1, remind_text: '请续费', exempt_user_ids: [], last_review_on: null };

test('date columns are answered as calendar dates and an expired member is reported as expired', async () => {
	const { feature } = setup({ members: [expired] });
	const list = feature.routes.find((route) => route.method === 'GET' && route.path.endsWith('/memberships'));
	const body = await list.handle({ params: { chatId: CHAT } });
	assert.deepEqual(body.members.map((row) => [row.started_on, row.expires_on, row.status]), [['2026-01-31', '2026-02-28', '已到期']]);
	const saved = await feature.updateMember(CHAT, 'u1', { started_on: '2026-02-15', term_months: 3, note: null });
	assert.deepEqual([saved.started_on, saved.expires_on], ['2026-02-15', '2026-05-15']);
});

test('the preview and a review the user asks for do not depend on the daily switch', async () => {
	const { feature, sent } = setup({ settings: { ...off }, members: [expired] });
	assert.deepEqual((await feature.review(CHAT, { dryRun: true })).map((row) => [row.user_id, row.status]), [['u1', '已到期']]);
	assert.equal(sent.length, 0);
	await feature.tick();
	assert.equal(sent.length, 0);
	await feature.review(CHAT);
	assert.equal(sent.length, 1);
});

test('a target already reviewed today is not reviewed again by the scheduler', async () => {
	const { feature, sent } = setup({ settings: { ...off, enabled: true, last_review_on: '2026-03-01' }, members: [expired] });
	await feature.tick();
	assert.equal(sent.length, 0);
});

test('rows keyed by the target id are moved to the group id on first use, and the reminder still goes to the target', async () => {
	const { feature, ledger, sent } = setup({ members: [expired] });
	assert.equal(ledger.rows[0].target_chat_id, CHAT);
	const list = feature.routes.find((route) => route.method === 'GET' && route.path.endsWith('/memberships'));
	const body = await list.handle({ params: { chatId: CHAT } });
	assert.deepEqual(body.members.map((row) => row.user_id), ['u1']);
	assert.equal(body.target_chat_id, CHAT);
	assert.equal(ledger.rows[0].target_chat_id, WS);
	assert.equal(ledger.config.target_chat_id, WS);
	await feature.review(CHAT);
	assert.deepEqual(sent.map((item) => item.chatId), [CHAT]);
});

test('a group without a bot can be listed, edited and previewed, but not reviewed, reminded or switched on', async () => {
	const { feature, sent } = setup({ usable: false, settings: { ...off }, members: [expired] });
	assert.deepEqual((await feature.review(CHAT, { dryRun: true })).map((row) => row.user_id), ['u1']);
	const saved = await feature.updateMember(CHAT, 'u1', { started_on: '2026-01-31', term_months: 1, note: null });
	assert.equal(saved.expires_on, '2026-02-28');
	await assert.rejects(() => feature.review(CHAT), /还没有可用的机器人/);
	await assert.rejects(() => feature.remind(CHAT, 'u1'), /还没有可用的机器人/);
	await assert.rejects(() => feature.updateSettings(CHAT, { enabled: true }), /还没有可用的机器人/);
	assert.equal((await feature.updateSettings(CHAT, { default_term_months: 3 })).default_term_months, 3);
	assert.equal(sent.length, 0);
});

test('an alias written as month-day plus term is read into offers, never into more than the text says', () => {
	const offers = (alias) => aliasSuggestions(alias, '2026-10-13').map((item) => item.label);
	assert.deepEqual(offers('阿明 823三个月'), ['08-23 起 3 个月']);
	assert.deepEqual(offers('小华 三个月826'), ['08-26 起 3 个月']);
	assert.deepEqual(offers('小黑24。94三个月'), ['09-04 起 3 个月']);
	assert.deepEqual(offers('静静 108'), ['10-08 起']);
	assert.deepEqual(offers('Abc1997 924三个月'), ['09-24 起 3 个月']);
	assert.deepEqual(offers('飞书用户1234AB 923'), ['09-23 起']);
	assert.deepEqual(offers('用户33安强827三个917一个'), ['08-27 起 3 个月', '09-17 起 1 个月']);
	assert.deepEqual(offers('雪饼哥 97三个月921三'), ['09-07 起 3 个月', '09-21 起 3 个月']);
	assert.deepEqual(offers('Example 98一个月108'), ['09-08 起 1 个月', '10-08 起']);
	assert.deepEqual(offers('用户444444 到12月底 98一周'), ['到 12-31', '09-08 起 1 周（到 09-15）']);
	assert.deepEqual(offers('阳阳 98一个月一周'), ['09-08 起 1 个月加 1 周（到 10-15）']);
	assert.deepEqual(offers('用户 三个月'), ['3 个月']);
	assert.deepEqual(offers('用户777777 97三个鱼13'), ['09-07 起 3 个月']);
	for (const plain of ['老王', '用户888888', '', null]) assert.deepEqual(offers(plain), []);
	assert.deepEqual(aliasSuggestions('阿明 823三个月', '2026-10-13')[0], { started_on: '2026-08-23', term_months: 3, expires_on: null, label: '08-23 起 3 个月' });
	assert.deepEqual(aliasSuggestions('到12月底', '2026-10-13')[0], { started_on: null, term_months: null, expires_on: '2026-12-31', label: '到 12-31' });
	// In January a December date is last month's, not eleven months ahead.
	assert.deepEqual(aliasSuggestions('阿明 1225一个月', '2027-01-05').map((item) => item.started_on), ['2026-12-25']);
});

test('a sync keeps each member\'s alias, and the list offers what the alias says', async () => {
	const { feature, ledger } = setup({ bridgeMembers: [{ user_id: 'u1', name: '群昵称', alias: '阿明 108三个月', joined_at: Math.floor(date('2026-01-31').getTime() / 1000), is_bot: false, is_self: false }] });
	const rows = await feature.sync(CHAT);
	assert.equal(rows[0].alias, '阿明 108三个月');
	const list = feature.routes.find((route) => route.method === 'GET' && route.path.endsWith('/memberships'));
	const body = await list.handle({ params: { chatId: CHAT } });
	// The clock here is 2026-03-01, so "108" is read as the nearer 8 January, not 8 October.
	assert.deepEqual(body.members[0].suggestions.map((item) => item.label), ['01-08 起 3 个月']);
	// The alias follows Feishu at the next sync; the dates a person set do not move.
	ledger.rows[0].started_on = '2026-02-10';
	const again = createMemberships({ ledger, bridgePost: async () => ({ status: 'ok', members: [{ user_id: 'u1', name: '群昵称', alias: '阿明 18三个月', joined_at: 1, is_bot: false, is_self: false }] }), targetRegistry: { get: async () => ({ chat_id: CHAT, websocket_chat_id: WS, owned: true, usable: true }) }, webhookTargets: { sendText: async () => {} }, now: () => date('2026-03-01') });
	const synced = await again.sync(CHAT);
	assert.deepEqual([synced[0].alias, synced[0].started_on], ['阿明 18三个月', '2026-02-10']);
});

test('a typed expiry date wins over the term until the member is saved without one', async () => {
	const { feature } = setup({ members: [expired] });
	const fixed = await feature.updateMember(CHAT, 'u1', { started_on: '2026-09-20', term_months: 1, expires_on: '2026-10-30', note: null });
	assert.deepEqual([fixed.started_on, fixed.expires_on, fixed.expiry_fixed], ['2026-09-20', '2026-10-30', true]);
	const counted = await feature.updateMember(CHAT, 'u1', { started_on: '2026-09-20', term_months: 1, note: null });
	assert.deepEqual([counted.expires_on, counted.expiry_fixed], ['2026-10-20', false]);
	const withExempt = await feature.updateMember(CHAT, 'u1', { started_on: '2026-09-20', term_months: 3, expires_on: '2026-12-31', note: null, exempt: false });
	assert.deepEqual([withExempt.expires_on, withExempt.expiry_fixed], ['2026-12-31', true]);
	await assert.rejects(() => feature.updateMember(CHAT, 'u1', { started_on: '2026-09-20', term_months: 1, expires_on: '2026-09-01', note: null }), /到期日不能早于开始日期/);
	await assert.rejects(() => feature.updateMember(CHAT, 'u1', { started_on: '2026-09-20', term_months: 1, expires_on: '10-30', note: null }), /到期日必须是 YYYY-MM-DD/);
});
