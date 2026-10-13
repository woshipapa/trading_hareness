// The path that makes a group the account owns a relay target through a bot it already has, on a real PostgreSQL with the
// real ledger: bots listed, one taken (no test message), the reminder sent through it, the target removed without touching the bot.
// Bridge and the webhook's HTTP are stand-ins; nothing leaves this machine.
import { freshLedger, stepper, mod, runSchema } from './lib.mjs';
const { createWebhookTargets } = await mod('webhook-targets.mjs');
const { createTargetRegistry } = await mod('target-registry.mjs');
const { createMemberships } = await mod('memberships.mjs');
const { ledger, pool } = await freshLedger('probe_existing_bot');
const S = stepper();
const now = () => new Date('2026-10-13T02:30:00Z');
const GROUP = '7680753363566234587';
const posts = [];
const fetchImpl = async (url, init) => { posts.push(JSON.parse(init.body).content.text); return { ok: true, status: 200, json: async () => ({ code: 0 }) }; };
const removed = [];
const bots = {
	ensure: async () => { throw new Error('not used here'); },
	remove: async (input) => { removed.push(input); return { status: 'removed' }; },
	adopt: async ({ chatId, botId }) => ({ chat_id: chatId, bot_id: botId, name: 'dragon', key_words: [], webhook: 'https://open.feishu.cn/open-apis/bot/v2/hook/existing-bot-0001' }),
};
const webhookTargets = createWebhookTargets({ ledger, secret: 'probe-secret', webhooks: new Map(), keywords: new Map(), bots, fetchImpl, now });
const day = (text) => Math.floor(new Date(`${text}T04:00:00Z`).getTime() / 1000);
const bridgePost = async (route, body) => {
	if (route === '/targets/resolve') return { groups: [{ chat_id: GROUP, name: '擒龙群', owned: true, bots: [] }] };
	if (route === '/chat-bots/list') return { chat_id: body.chat_id, bots: [{ bot_id: '41', name: 'dragon', key_words: [], usable: true, reason: null }] };
	return { status: 'ok', chat_id: body.chat_id, members: [{ user_id: 'q1', name: '丙', joined_at: day('2026-08-01'), is_bot: false, is_self: false }] };
};
const registry = createTargetRegistry({ ledger, bridgePost, webhookTargets, now });
const memberships = createMemberships({ ledger, bridgePost, targetRegistry: registry, webhookTargets, now });
await runSchema(ledger, registry); await runSchema(ledger, memberships);
await webhookTargets.load();
const brief = (rows) => rows.map((row) => `${row.chat_id.startsWith('wh_') ? 'wh_…' : row.chat_id} ${row.name} ${row.origin} usable=${row.usable} bot=${row.bot_name}`);
await S.step('owned group listed, no bot', async () => brief(await registry.resolve()));
await S.step('members synced before any bot', async () => (await memberships.sync(GROUP)).map((row) => [row.user_id, row.expires_on]));
await S.step('bots already in the group', async () => registry.existingBots(GROUP));
let target;
await S.step('take the existing bot', async () => { target = await registry.useBot(GROUP, '41'); return [brief([target]), `owned=${target.owned}`, `managed=${target.managed}`]; });
await S.step('nothing was posted in the group', async () => posts.length);
await S.step('stored row', async () => (await pool.query('SELECT chat_name, keyword, websocket_chat_id, bot_id, managed, length(webhook_ciphertext) > 20 AS encrypted FROM feishu_group_relay_webhook_targets')).rows);
await S.step('the list shows one row for the group', async () => brief(await registry.groups()));
await S.step('the same members under the new row', async () => (await memberships.routes.find((item) => item.method === 'GET' && item.path.endsWith('/memberships')).handle({ params: { chatId: target.chat_id } })).members.map((row) => [row.user_id, row.status]));
await S.step('daily switch can be turned on now', async () => (await memberships.updateSettings(target.chat_id, { enabled: true })).enabled);
await S.step('reminder goes out through the bot, no keyword in front', async () => { await memberships.review(target.chat_id); return posts.slice(); });
await S.step('after a restart the target and its address are still there', async () => { const again = createWebhookTargets({ ledger, secret: 'probe-secret', webhooks: new Map(), keywords: new Map(), bots, fetchImpl, now }); const loaded = await again.load(); await again.sendText(target.chat_id, '重启后'); return [loaded, posts.at(-1)]; });
await S.step('taking a bot for a group that is a target already is refused', async () => { try { await registry.useBot(target.chat_id, '41'); return 'not refused'; } catch (error) { return error.message; } });
await S.step('removing the target leaves the bot alone', async () => [await webhookTargets.remove(target.chat_id), removed.length]);
await S.step('the group is back to managed-only, members kept', async () => { await registry.forget(target.chat_id); return [brief(await registry.groups()), (await pool.query('SELECT count(*)::int AS n FROM feishu_group_relay_memberships')).rows[0].n]; });
S.done(); process.exit(S.failed ? 1 : 0);
