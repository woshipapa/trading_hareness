// Runs the membership module against a real PostgreSQL with the real driver (node-pg): every statement the module sends is
// parsed and executed by the database, which a hand-written ledger in a unit test cannot show (parameter type inference, the
// shape of a `date` column). Bridge, target registry and webhook are stand-ins; nothing leaves this machine.
// usage: node memberships_pg_probe.mjs <adapter dir> <postgres url>
import path from 'node:path';
const [adapterDir, url] = process.argv.slice(2);
const { default: pg } = await import(path.join(adapterDir, 'node_modules/pg/lib/index.js'));
const { createMemberships } = await import(path.join(adapterDir, 'memberships.mjs'));
const pool = new pg.Pool({ connectionString: url, max: 2 });
const CHAT = 'oc_probe';
const day = (text) => Math.floor(new Date(`${text}T04:00:00Z`).getTime() / 1000);
let roster = [
	{ user_id: 'u1', name: '甲', joined_at: day('2026-08-01'), is_bot: false, is_self: false },
	{ user_id: 'u2', name: '乙', joined_at: day('2026-10-01'), is_bot: false, is_self: false },
	{ user_id: 'b1', name: '转发机器人', joined_at: day('2026-08-01'), is_bot: true, is_self: false },
];
let listCalls = 0;
const sent = [];
const feature = createMemberships({
	ledger: { query: (sql, params) => pool.query(sql, params) },
	bridgePost: async () => { listCalls += 1; return { status: 'ok', chat_id: '123', members: roster }; },
	targetRegistry: { get: async () => ({ chat_id: CHAT, websocket_chat_id: '123', owned: true }), list: async () => [{ chat_id: CHAT }] },
	webhookTargets: { sendText: async (chatId, text) => { sent.push(text); } },
	now: () => new Date('2026-10-13T02:30:00Z'),
});
const route = (method, suffix) => feature.routes.find((item) => item.method === method && item.path.endsWith(suffix));
let failed = 0;
async function step(name, run) {
	try { console.log(`ok   ${name}: ${JSON.stringify(await run())}`); }
	catch (error) { failed += 1; console.log(`FAIL ${name}: ${error.message}`); }
}
const brief = (rows) => rows.map((row) => `${row.user_id} ${row.started_on}→${row.expires_on} ${row.status ?? ''}`.trim());
for (const sql of feature.schema) await pool.query(sql);
await step('sync', async () => brief(await feature.sync(CHAT)));
await step('list', async () => brief((await route('GET', '/memberships').handle({ params: { chatId: CHAT } })).members));
await step('save member', async () => { const row = await feature.updateMember(CHAT, 'u2', { started_on: '2026-10-08', term_months: 3, note: 'n' }); return [row.started_on, row.expires_on]; });
await step('save member with exempt flag', async () => { const row = await feature.updateMember(CHAT, 'u2', { started_on: '2026-10-08', term_months: 3, note: 'n', exempt: false }); return [row.started_on, row.expires_on]; });
await step('settings off', async () => { const row = await feature.updateSettings(CHAT, { enabled: false }); return [row.enabled, row.last_review_on]; });
await step('preview (switch off)', async () => brief(await feature.review(CHAT, { dryRun: true })));
await step('nothing sent by the preview', async () => sent.length);
await step('review', async () => brief(await feature.review(CHAT)));
await step('sent by the review', async () => sent.slice());
await step('review again reminds nobody', async () => brief(await feature.review(CHAT)));
await step('remind one member again', async () => { const row = await feature.remind(CHAT, 'u1'); return [row.user_id, sent.length]; });
await step('stored after reminders', async () => (await pool.query('SELECT user_id, reminded_on::text, remind_claimed_at, updated_at::text FROM feishu_group_relay_memberships ORDER BY user_id')).rows.map((row) => [row.user_id, row.reminded_on, row.remind_claimed_at, row.updated_at]));
await step('settings on', async () => (await feature.updateSettings(CHAT, { enabled: true })).enabled);
await step('scheduler: first tick of the day reviews', async () => { const before = listCalls; await feature.tick(); return { member_list_reads: listCalls - before, last_review_on: (await route('GET', '/membership-settings').handle({ params: { chatId: CHAT } })).settings.last_review_on }; });
await step('scheduler: second tick the same day does nothing', async () => { const before = listCalls; await feature.tick(); return { member_list_reads: listCalls - before }; });
roster = roster.filter((member) => member.user_id !== 'u2');
await step('a member who left is marked', async () => (await feature.sync(CHAT)).map((row) => [row.user_id, Boolean(row.left_at)]));
console.log(failed ? `${failed} STEP(S) FAILED` : 'ALL STEPS PASSED');
await pool.end();
process.exit(failed ? 1 : 0);
