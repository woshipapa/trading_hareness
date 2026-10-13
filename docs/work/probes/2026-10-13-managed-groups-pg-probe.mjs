// Runs the target registry and the membership module together against a real PostgreSQL with node-pg: the groups the
// account owns are listed beside the relay targets, membership rows are keyed by the group's own id, and rows written
// under the target id (as on the live system before this change) are moved on first use.
// Bridge, webhook sending and the clock are stand-ins; nothing leaves this machine.
// usage: node managed_groups_pg_probe.mjs <adapter dir> <postgres url>
import path from 'node:path';
const [adapterDir, url] = process.argv.slice(2);
const { default: pg } = await import(path.join(adapterDir, 'node_modules/pg/lib/index.js'));
const { createTargetRegistry } = await import(path.join(adapterDir, 'target-registry.mjs'));
const { createMemberships } = await import(path.join(adapterDir, 'memberships.mjs'));
const pool = new pg.Pool({ connectionString: url, max: 2 });
const ledger = { query: (sql, params) => pool.query(sql, params) };
const now = () => new Date('2026-10-13T02:30:00Z');
const day = (text) => Math.floor(new Date(`${text}T04:00:00Z`).getTime() / 1000);
const TARGET_GROUP = '7690000000000000001';
const OWNED_GROUP = '7680000000000000002';
let joined = [
	{ chat_id: TARGET_GROUP, name: '已有目标群', owned: true, bots: [{ bot_id: 'bot-1', name: '转发机器人', webhook_hash: 'hash-env' }] },
	{ chat_id: OWNED_GROUP, name: '擒龙群', owned: true, bots: [] },
	{ chat_id: '7680000000000000003', name: '别人的群', owned: false, bots: [] },
];
const rosters = {
	[TARGET_GROUP]: [{ user_id: 'a1', name: '甲', joined_at: day('2026-08-01'), is_bot: false, is_self: false }, { user_id: 'a2', name: '乙', joined_at: day('2026-10-01'), is_bot: false, is_self: false }],
	[OWNED_GROUP]: [{ user_id: 'q1', name: '丙', joined_at: day('2026-08-01'), is_bot: false, is_self: false }, { user_id: 'q2', name: '丁', joined_at: day('2026-10-05'), is_bot: false, is_self: false }],
};
const sendTargets = [{ chat_id: 'oc_env', name: null, keyword: '转发', origin: 'env', usable: true, managed: false, websocket_chat_id: null, verified_at: null, created_at: null }];
const sent = [];
const webhookTargets = {
	list: () => sendTargets.map((item) => ({ ...item })),
	webhookHashes: () => [{ chat_id: 'oc_env', webhook_hash: 'hash-env' }],
	sendText: async (chatId, text) => { if (!sendTargets.some((item) => item.chat_id === chatId)) throw new Error('目标群不存在或 webhook 不可用'); sent.push(`${chatId}: ${text}`); },
};
const bridgePost = async (route, body) => (route === '/targets/resolve' ? { groups: joined } : { status: 'ok', chat_id: body.chat_id, members: rosters[body.chat_id] });
const registry = createTargetRegistry({ ledger, bridgePost, webhookTargets, now });
const memberships = createMemberships({ ledger, bridgePost, targetRegistry: registry, webhookTargets, now });
const route = (method, suffix) => memberships.routes.find((item) => item.method === method && item.path.endsWith(suffix));
const keys = async () => (await pool.query('SELECT target_chat_id, count(*)::int AS n FROM feishu_group_relay_memberships GROUP BY 1 ORDER BY 1')).rows.map((row) => `${row.target_chat_id}×${row.n}`);
const settingKeys = async () => (await pool.query('SELECT target_chat_id, enabled, last_review_on::text FROM feishu_group_relay_membership_settings ORDER BY 1')).rows.map((row) => `${row.target_chat_id} ${row.enabled} ${row.last_review_on}`);
let failed = 0;
async function step(name, run, expectError) {
	try {
		const value = await run();
		if (expectError) { failed += 1; console.log(`FAIL ${name}: expected a refusal, got ${JSON.stringify(value)}`); } else console.log(`ok   ${name}: ${JSON.stringify(value)}`);
	} catch (error) {
		if (expectError && expectError.test(error.message)) console.log(`ok   ${name}: refused — ${error.message}`);
		else { failed += 1; console.log(`FAIL ${name}: ${error.message}`); }
	}
}
const brief = (rows) => rows.map((row) => `${row.chat_id} ${row.name} ${row.origin} usable=${row.usable} owned=${row.owned}`);
const members = (rows) => rows.map((row) => `${row.user_id} ${row.started_on}→${row.expires_on} ${row.status ?? ''}`.trim());
for (const sql of [...registry.schema, ...memberships.schema]) await pool.query(sql);
// The live system's state before this change: a resolved target row, and membership rows and settings keyed by the target id.
await pool.query(`INSERT INTO feishu_group_relay_target_groups (chat_id,websocket_chat_id,chat_name,bot_id,bot_name,owned,resolved_at) VALUES ('oc_env',$1,'已有目标群','bot-1','转发机器人',true,now())`, [TARGET_GROUP]);
await pool.query(`INSERT INTO feishu_group_relay_memberships (target_chat_id,user_id,display_name,started_on,term_months,expires_on,note) VALUES ('oc_env','a1','甲','2026-08-23',3,'2026-11-23','手填'),('oc_env','a2','乙','2026-09-01',1,'2026-10-01',NULL)`);
await pool.query(`INSERT INTO feishu_group_relay_membership_settings (target_chat_id,enabled,default_term_months,remind_text,exempt_user_ids) VALUES ('oc_env',false,1,'会员已到期，请联系群主续费','[]'::jsonb)`);
await step('before: rows keyed by the target id', keys);
await step('start resolves once and lists owned groups', async () => { await registry.start(); return brief(await registry.groups()); });
await step('targets only (what forwarding sees)', async () => (await registry.list()).map((row) => row.chat_id));
await step('old target: members read back with the dates typed by hand', async () => members((await route('GET', '/memberships').handle({ params: { chatId: 'oc_env' } })).members));
await step('old target: rows and settings now keyed by the group id', async () => [await keys(), await settingKeys()]);
await step('owned group: sync', async () => members(await memberships.sync(OWNED_GROUP)));
await step('owned group: save a member', async () => { const row = await memberships.updateMember(OWNED_GROUP, 'q1', { started_on: '2026-09-01', term_months: 1, note: null }); return [row.started_on, row.expires_on]; });
await step('owned group: preview works without a bot', async () => members(await memberships.review(OWNED_GROUP, { dryRun: true })));
await step('owned group: review refused without a bot', () => memberships.review(OWNED_GROUP), /还没有可用的机器人/);
await step('owned group: remind refused without a bot', () => memberships.remind(OWNED_GROUP, 'q1'), /还没有可用的机器人/);
await step('owned group: daily switch refused without a bot', () => memberships.updateSettings(OWNED_GROUP, { enabled: true }), /还没有可用的机器人/);
await step('owned group: other settings saved', async () => (await memberships.updateSettings(OWNED_GROUP, { default_term_months: 3 })).default_term_months);
sendTargets.push({ chat_id: 'wh_new', name: '擒龙群', keyword: '提醒', origin: 'dashboard', usable: true, managed: true, websocket_chat_id: OWNED_GROUP, verified_at: null, created_at: null });
await step('after a bot is added: one row for the group, under the new target id', async () => brief(await registry.groups()));
await step('after a bot is added: the same members, nothing moved', async () => [members((await route('GET', '/memberships').handle({ params: { chatId: 'wh_new' } })).members), await keys()]);
await step('after a bot is added: daily switch on for both groups', async () => [(await memberships.updateSettings('wh_new', { enabled: true })).enabled, (await memberships.updateSettings('oc_env', { enabled: true })).enabled]);
await step('scheduler reviews both groups', async () => { await memberships.tick(); return { sent: sent.slice(), settings: await settingKeys() }; });
await step('scheduler does nothing more the same day', async () => { const before = sent.length; await memberships.tick(); return sent.length - before; });
joined = joined.filter((group) => group.chat_id !== OWNED_GROUP);
sendTargets.pop();
await step('a group no longer owned leaves the list', async () => brief(await registry.resolve()));
joined = [];
await step('no joined group at all: only the configured targets remain', async () => brief(await registry.resolve()));
await step('membership rows are kept either way', keys);
console.log(failed ? `${failed} STEP(S) FAILED` : 'ALL STEPS PASSED');
await pool.end();
process.exit(failed ? 1 : 0);
