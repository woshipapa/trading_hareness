import { freshLedger, stepper, mod, runSchema, realWebhooks } from './lib.mjs';
const { createDeliveryCheck } = await mod('delivery-check.mjs');
const { createTargetRegistry } = await mod('target-registry.mjs');
const { ledger, pool } = await freshLedger('probe_dc');
const S = stepper();
let clock = new Date('2026-10-13T02:30:00Z');
const now = () => clock;
const wh = await realWebhooks(ledger, now);
const A = await wh.add({ name: 'A群', webhookUrl: 'https://open.feishu.cn/open-apis/bot/v2/hook/aaaaaaaaaaaa1', keyword: '转发', websocketChatId: '111111' });
const recalls = []; let failRecall = false; const observeBodies = [];
const bridgePost = async (route, body) => {
	if (route === '/messages/recall-in-group') { if (failRecall) throw new Error('recall refused'); recalls.push(body); return {}; }
	if (route === '/targets/observe') { observeBodies.push(body); return {}; }
	return {};
};
const reg = createTargetRegistry({ ledger, bridgePost, webhookTargets: wh, now });
await runSchema(ledger, reg);
await reg.record({ chat_id: A.chat_id, websocket_chat_id: '111111', chat_name: 'A', bot_id: '7', bot_name: 'b', owned: true });
const dc = createDeliveryCheck({ ledger, bridgePost, targetRegistry: reg, now });
await runSchema(ledger, dc);
const route = (m, p) => dc.routes.find((r) => r.method === m && r.path.endsWith(p));
await S.step('record delivery (src m1)', async () => { await dc.recordWebhookDelivery({ messageId: 'm1', targetChatId: A.chat_id, msgType: 'text', content: { text: '转发 内容一' } }); return 1; });
await S.step('record delivery (src m2, other text)', async () => { await dc.recordWebhookDelivery({ messageId: 'm2', targetChatId: A.chat_id, msgType: 'text', content: { text: '转发 内容二' } }); return 1; });
await S.step('record non-text is skipped', async () => { await dc.recordWebhookDelivery({ messageId: 'm3', targetChatId: A.chat_id, msgType: 'image', content: {} }); return 1; });
await S.step('observed: match m1 (30 s later)', async () => { clock = new Date('2026-10-13T02:30:30Z'); return route('POST', 'target-observed').handle({ body: { msg_type: 'text', message_id: 'om1', chat_id: '111111', text: '转发 内容一' } }); });
await S.step('observed: replay of the same target message', async () => route('POST', 'target-observed').handle({ body: { msg_type: 'text', message_id: 'om1', chat_id: '111111', text: '转发 内容一' } }));
await S.step('observed: no candidate', async () => route('POST', 'target-observed').handle({ body: { msg_type: 'text', message_id: 'om9', chat_id: '111111', text: '没有记录' } }));
await S.step('observed: match m2', async () => route('POST', 'target-observed').handle({ body: { msg_type: 'text', message_id: 'om2', chat_id: '111111', text: '转发 内容二' } }));
await S.step('recallDeliveries m1 (success path -> UPDATE recalled_at)', async () => { await dc.recallDeliveries('m1'); return recalls.length; });
await S.step('recallDeliveries m2 (failure path -> UPDATE recall_error)', async () => { failRecall = true; await dc.recallDeliveries('m2'); return 1; });
await S.step('recallDeliveries again selects nothing', async () => { failRecall = false; const n = recalls.length; await dc.recallDeliveries('m1'); await dc.recallDeliveries('m2'); return recalls.length - n; });
await S.step('deliveries route: counts + rows', async () => { const r = await route('GET', '/deliveries').handle({ params: { chatId: A.chat_id } }); return { counts: r.counts, n: r.deliveries.length, sent_at_type: r.deliveries[0].sent_at instanceof Date ? 'Date' : typeof r.deliveries[0].sent_at, delivery_id_type: typeof r.deliveries[0].delivery_id, recalled: r.deliveries.map((d) => [d.source_message_id, !!d.recalled_at, d.recall_error]) }; });
await S.step('start (observe list sent)', async () => { const stop = await dc.start(); stop(); return observeBodies; });
S.done(); process.exit(S.failed ? 1 : 0);
