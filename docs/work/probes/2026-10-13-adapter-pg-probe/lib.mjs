// Shared set-up: a fresh database, the real ledger.mjs (real schema, real source-profile and webhook-target methods), step runner.
import path from 'node:path';
export const ADAPTER = '/Users/papa/codebase/.codex-wt/n8n-relay-auto-int/feishu-relay/adapter';
const { default: pg } = await import(path.join(ADAPTER, 'node_modules/pg/lib/index.js'));
const BASE = 'postgres://postgres:scratch@127.0.0.1:55432/';
export async function freshLedger(dbName, registry = { routes: [], default_topic_key: 'general' }) {
	const admin = new pg.Client({ connectionString: BASE + 'postgres' });
	await admin.connect();
	await admin.query(`DROP DATABASE IF EXISTS ${dbName} WITH (FORCE)`);
	await admin.query(`CREATE DATABASE ${dbName}`);
	await admin.end();
	const { createLedger } = await import(path.join(ADAPTER, 'ledger.mjs'));
	const ledger = createLedger(BASE + dbName);
	await ledger.init(registry);
	const pool = new pg.Pool({ connectionString: BASE + dbName, max: 2 });
	return { ledger, pool };
}
export async function runSchema(ledger, feature) { for (const sql of feature.schema ?? []) await ledger.query(sql); }
export function stepper() {
	const s = { failed: 0, total: 0 };
	s.step = async (name, run) => {
		s.total += 1;
		try { console.log(`ok   ${name}: ${JSON.stringify(await run())}`); }
		catch (error) { s.failed += 1; console.log(`FAIL ${name}: ${error.message}`); }
	};
	s.done = () => { console.log(`steps=${s.total} failed=${s.failed}`); };
	return s;
}
export const mod = (name) => import(path.join(ADAPTER, name));
export async function realWebhooks(ledger, now) {
	const { createWebhookTargets } = await mod('webhook-targets.mjs');
	const fetchImpl = async () => ({ ok: true, status: 200, json: async () => ({ code: 0 }) });
	const bots = { ensure: async ({ chatId }) => ({ webhook: `https://open.feishu.cn/open-apis/bot/v2/hook/bot${chatId}abcdef`, bot_id: `bot${chatId}`, created: true }), remove: async () => ({ status: 'removed' }) };
	return createWebhookTargets({ ledger, secret: 'probe-secret', webhooks: new Map(), keywords: new Map(), bots, fetchImpl, now });
}
