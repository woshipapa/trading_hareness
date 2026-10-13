// The membership module's new columns on a real PostgreSQL with node-pg: a table created before the change gains
// `alias` and `expiry_fixed`, a sync stores each member's alias, the list offers what the alias says, and a typed
// expiry date is kept. Bridge, registry, webhook and clock are stand-ins; nothing leaves this machine.
// usage: node 2026-10-14-membership-alias-expiry-pg-probe.mjs <adapter dir> <postgres url>
import path from 'node:path';
const [adapterDir, url] = process.argv.slice(2);
const { default: pg } = await import(path.join(adapterDir, 'node_modules/pg/lib/index.js'));
const { createMemberships } = await import(path.join(adapterDir, 'memberships.mjs'));
const pool = new pg.Pool({ connectionString: url, max: 2 });
const GROUP = '7680000000000000002';
const day = (text) => Math.floor(new Date(`${text}T04:00:00Z`).getTime() / 1000);
const roster = [
	{ user_id: 'a1', name: '甲', alias: '阿明 823三个月', joined_at: day('2026-08-23'), is_bot: false, is_self: false },
	{ user_id: 'a2', name: '乙', alias: '小华 到12月底 918', joined_at: day('2026-09-18'), is_bot: false, is_self: false },
	{ user_id: 'a3', name: '丙', alias: '', joined_at: day('2026-10-01'), is_bot: false, is_self: false },
];
const sent = [];
const group = { chat_id: 'wh_probe', websocket_chat_id: GROUP, owned: true, usable: true };
const feature = createMemberships({
	ledger: { query: (sql, params) => pool.query(sql, params) },
	bridgePost: async () => ({ status: 'ok', chat_id: GROUP, members: roster }),
	targetRegistry: { get: async () => group, groups: async () => [group] },
	webhookTargets: { sendText: async (chatId, text) => { sent.push(text); } },
	now: () => new Date('2026-10-13T02:30:00Z'),
});
let failed = 0;
async function step(name, run) {
	try { console.log(`ok   ${name}: ${JSON.stringify(await run())}`); }
	catch (error) { failed += 1; console.log(`FAIL ${name}: ${error.message}`); }
}
const list = async () => (await feature.routes.find((item) => item.method === 'GET' && item.path.endsWith('/memberships')).handle({ params: { chatId: 'wh_probe' } })).members;
// The tables as they are on the live system before this change: created without the two new columns, with a row in place.
for (const sql of feature.schema.filter((statement) => statement.startsWith('CREATE'))) await pool.query(sql);
await pool.query(`INSERT INTO feishu_group_relay_memberships (target_chat_id,user_id,display_name,started_on,term_months,expires_on,note) VALUES ($1,'a1','甲','2026-08-23',3,'2026-11-23','手填')`, [GROUP]);
await step('columns before', async () => (await pool.query(`SELECT column_name FROM information_schema.columns WHERE table_name='feishu_group_relay_memberships' AND column_name IN ('alias','expiry_fixed')`)).rows.length);
await step('schema run twice adds the columns once', async () => { for (let pass = 0; pass < 2; pass += 1) for (const sql of feature.schema) await pool.query(sql); return (await pool.query(`SELECT column_name, data_type, is_nullable, column_default FROM information_schema.columns WHERE table_name='feishu_group_relay_memberships' AND column_name IN ('alias','expiry_fixed') ORDER BY 1`)).rows; });
await step('an old row reads with an empty alias and a counted expiry', async () => (await list()).map((row) => [row.user_id, row.alias, row.expiry_fixed, row.expires_on, row.suggestions]));
await step('sync stores the aliases', async () => (await feature.sync('wh_probe')).map((row) => [row.user_id, row.alias, row.started_on]));
await step('the list offers what each alias says', async () => (await list()).map((row) => [row.user_id, row.suggestions.map((item) => item.label)]));
await step('a typed expiry date is kept', async () => { const row = await feature.updateMember('wh_probe', 'a2', { started_on: '2026-09-18', term_months: 3, expires_on: '2026-12-31', note: null }); return [row.started_on, row.expires_on, row.expiry_fixed, row.alias]; });
await step('…also when the exempt flag is sent with it', async () => { const row = await feature.updateMember('wh_probe', 'a2', { started_on: '2026-09-18', term_months: 3, expires_on: '2026-12-31', note: null, exempt: false }); return [row.expires_on, row.expiry_fixed]; });
await step('saving without one counts from the term again', async () => { const row = await feature.updateMember('wh_probe', 'a2', { started_on: '2026-09-18', term_months: 3, note: null }); return [row.expires_on, row.expiry_fixed]; });
await step('an expiry before the start is refused', async () => { try { await feature.updateMember('wh_probe', 'a2', { started_on: '2026-09-18', term_months: 3, expires_on: '2026-09-01', note: null }); return 'not refused'; } catch (error) { return error.message; } });
await step('a typed expiry in the past is reviewed like any other', async () => { await feature.updateMember('wh_probe', 'a3', { started_on: '2026-10-01', term_months: 1, expires_on: '2026-10-08', note: null }); return (await feature.review('wh_probe', { dryRun: true })).map((row) => [row.user_id, row.expires_on, row.status]); });
await step('the alias changes with Feishu at the next sync, the dates do not', async () => { roster[0].alias = '阿明 1123三个月'; return (await feature.sync('wh_probe')).filter((row) => row.user_id === 'a1').map((row) => [row.alias, row.started_on, row.expires_on, row.note]); });
console.log(failed ? `${failed} STEP(S) FAILED` : 'ALL STEPS PASSED');
await pool.end();
process.exit(failed ? 1 : 0);
