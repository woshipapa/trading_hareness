import assert from 'node:assert/strict';
import test from 'node:test';
import { addMonths, createMemberships } from './memberships.mjs';

const CHAT = 'oc_target';
const WS = '7696053062053530578';

function date(value) { return value instanceof Date ? value : new Date(value.includes('T') ? `${value}${value.endsWith('Z') ? '' : 'Z'}` : `${value}T01:00:00Z`); }

function fakeLedger({ members = [], settings = null } = {}) {
	const rows = members.map((row) => ({ target_chat_id: CHAT, created_at: date('2026-01-01'), updated_at: date('2026-01-01'), ...row }));
	const config = settings ?? { target_chat_id: CHAT, enabled: true, default_term_months: 1, remind_text: '请续费', exempt_user_ids: [], last_review_on: null };
	const clone = (row) => ({ ...row });
	return {
		rows,
		config,
		async query(sql, params = []) {
			if (sql.startsWith('SELECT target_chat_id,enabled,last_review_on')) return config.enabled ? { rows: [{ target_chat_id: CHAT, enabled: true, last_review_on: config.last_review_on }] } : { rows: [] };
			if (sql.startsWith('SELECT target_chat_id,enabled,default_term_months')) return { rows: [clone(config)] };
			if (sql.startsWith('INSERT INTO feishu_group_relay_membership_settings')) { Object.assign(config, { target_chat_id: params[0], enabled: params[1], default_term_months: params[2], remind_text: params[3], exempt_user_ids: JSON.parse(params[4]), last_review_on: params[5] ?? config.last_review_on }); return { rows: [clone(config)] }; }
			if (sql.startsWith('UPDATE feishu_group_relay_membership_settings')) { config.last_review_on = params[1]; return { rows: [] }; }
			if (sql.startsWith('SELECT target_chat_id,user_id')) return { rows: rows.filter((row) => row.target_chat_id === String(params[0])).map(clone) };
			if (sql.startsWith('INSERT INTO feishu_group_relay_memberships')) {
				if (!rows.some((row) => row.user_id === params[1])) rows.push({ target_chat_id: params[0], user_id: params[1], display_name: params[2], started_on: params[3], term_months: params[4], expires_on: params[5], note: params[6], reminded_on: null, remind_claimed_at: null, left_at: null, created_at: params[7], updated_at: params[7] });
				return { rows: [] };
			}
			if (sql.startsWith('UPDATE feishu_group_relay_memberships SET display_name')) { const row = rows.find((item) => item.user_id === params[1]); Object.assign(row, { display_name: params[2], left_at: null, updated_at: params[3] }); return { rows: [] }; }
			if (sql.startsWith('UPDATE feishu_group_relay_memberships SET left_at')) { const row = rows.find((item) => item.user_id === params[1]); Object.assign(row, { left_at: params[2], updated_at: params[2] }); return { rows: [] }; }
			if (sql.startsWith('UPDATE feishu_group_relay_memberships SET started_on')) { const row = rows.find((item) => item.user_id === params[1]); if (!row) return { rows: [] }; Object.assign(row, { started_on: params[2], term_months: params[3], expires_on: params[4], note: params[5], reminded_on: null, remind_claimed_at: null, updated_at: params[6] }); return { rows: [clone(row)] }; }
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
	const members = options.bridgeMembers ?? [{ user_id: 'u1', name: '张<三"', joined_at: Math.floor(date('2026-01-31').getTime() / 1000) }];
	const bridgePost = async () => ({ status: 'ok', chat_id: WS, members });
	const targetRegistry = { get: async () => ({ chat_id: CHAT, websocket_chat_id: WS, owned: true }), list: async () => [{ chat_id: CHAT }] };
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
});

test('editing a member recomputes expiry and clears its reminder', async () => {
	const { feature, ledger } = setup({ members: [{ user_id: 'u1', display_name: 'A', started_on: '2026-01-01', term_months: 1, expires_on: '2026-02-01', reminded_on: '2026-02-02', remind_claimed_at: null, left_at: null }] });
	const updated = await feature.updateMember(CHAT, 'u1', { started_on: '2026-01-31', term_months: 1, note: '续费' });
	assert.equal(updated.expires_on, '2026-02-28');
	assert.equal(updated.reminded_on, null);
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
	const { feature, sent } = setup({ members: [{ user_id: 'u1', display_name: 'A', started_on: '2026-01-01', term_months: 1, expires_on: '2026-02-01', reminded_on: null, remind_claimed_at: null, left_at: null }] });
	await Promise.all([feature.review(CHAT), feature.review(CHAT)]);
	assert.equal(sent.length, 1);
});

test('failed batches are retried, successful batches are not', async () => {
	const setupResult = setup({ sendError: '飞书拒绝' });
	await setupResult.feature.review(CHAT);
	assert.equal(setupResult.sent.length, 1);
	setupResult.setNow('2026-03-02');
	setupResult.feature = createMemberships({ ledger: setupResult.ledger, bridgePost: async () => ({ members: [{ user_id: 'u1', name: 'A', joined_at: 1 }] }), targetRegistry: { get: async () => ({ websocket_chat_id: WS, owned: true }), list: async () => [{ chat_id: CHAT }] }, webhookTargets: { sendText: async () => { setupResult.sent.push('retry'); } }, now: () => date('2026-03-02') });
	await setupResult.feature.review(CHAT);
	assert.equal(setupResult.sent.length, 2);
});

test('more than 20 members are sent in several batches', async () => {
	const members = Array.from({ length: 21 }, (_, index) => ({ user_id: `u${index}`, display_name: `会员${index}`, started_on: '2026-01-01', term_months: 1, expires_on: '2026-02-01', reminded_on: null, remind_claimed_at: null, left_at: null }));
	const { feature, sent } = setup({ members, bridgeMembers: members.map((row) => ({ user_id: row.user_id, name: row.display_name, joined_at: 1 })) });
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
