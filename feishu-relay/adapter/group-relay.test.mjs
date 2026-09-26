import assert from 'node:assert/strict';
import test from 'node:test';
import { Readable } from 'node:stream';
import { performance } from 'node:perf_hooks';
import { createGroupRelay } from './group-relay.mjs';
import { readLarkAgentXBackfill } from './larkagentx-backfill.mjs';

function createHarness(messages, { imageResponse = { image_key: 'img_target' }, imageError = null, targetChatIds = [], failTargetChatId = null, retryFailed = false, canWrite = null, sources = null, messageListDelayMs = 0, sourceConcurrency = 3, resourceErrorKeys = [], outboundCard = false, failUpdateMessageId = null, logger = null, webhooksByChatId = null, webhookKeywordsByChatId = null, larkAgentXResourceUrl = '', larkAgentXToken = '', messageList = null, initialSourceStates = null } = {}) {
	const saved = new Map();
	const sourceStates = new Map(Object.entries(initialSourceStates ?? {}));
	const sent = [];
	const updated = [];
	const patched = [];
	let failedTarget = false;
	const ledger = {
		relayRetryQueue: async () => {
			if (!retryFailed) return [];
			const failed = [...saved.entries()].find(([, value]) => value.status === 'failed');
			if (!failed) return [];
			const [sourceMessageId, value] = failed;
			return [{ ...value, source_message_id: sourceMessageId, source_key: value.sourceKey, source_chat_id: value.sourceChatId,
				source_create_time: value.sourceCreateTime, target_chat_id: value.targetChatId, route_tag: value.routeTag,
				target_message_ids: value.targetMessageIds ?? [] }];
	},
		portableInteractiveSummaryUpgradeQueue: async () => [],
		markPortableSummaryVersion: async (id, version) => saved.set(id, { ...saved.get(id), portableSummaryVersion: version }),
		relaySourceState: async (key) => sourceStates.get(key) ?? null,
		saveRelaySourceCursor: async ({ sourceKey, chatId, cursorCreateTime }) => sourceStates.set(sourceKey, { chat_id: chatId, cursor_create_time: cursorCreateTime }),
		getRelayMessage: async (id) => saved.get(id) ?? null,
		getRelayMessages: async (ids) => ids.map((id) => saved.get(id)).filter(Boolean),
		relayMessagesBySourceWindow: async (sourceKey, from, to) => [...saved.values()].filter((row) => row.sourceKey === sourceKey && row.sourceCreateTime >= from && row.sourceCreateTime <= to),
		skipRelayMessage: async (record) => saved.set(record.sourceMessageId, { ...record, status: 'skipped_bootstrap' }),
		filterRelayMessage: async (record, reason) => saved.set(record.sourceMessageId, { ...saved.get(record.sourceMessageId), ...record, status: 'filtered_system', errorMessage: reason }),
		claimRelayMessage: async (record) => {
			const sourceMessageId = record.sourceMessageId ?? record.source_message_id;
			const existing = saved.get(sourceMessageId);
			if (existing && existing.status !== 'failed') return null;
			const claimed = { ...existing, ...record, sourceMessageId, status: 'processing', target_message_ids: existing?.targetMessageIds ?? existing?.target_message_ids ?? [] };
			saved.set(sourceMessageId, claimed); return claimed;
		},
		resetRelayMessageForRetry: async (sourceMessageId, reason) => {
			const existing = saved.get(sourceMessageId);
			if (!existing || existing.status !== 'sent') return false;
			saved.set(sourceMessageId, { ...existing, status: 'failed', targetMessageIds: [], target_message_ids: [], errorMessage: reason });
			return true;
		},
		markRelayMessage: async (id, update) => saved.set(id, { ...saved.get(id), ...update, target_message_ids: update.targetMessageIds ?? saved.get(id)?.target_message_ids ?? [] }),
		updateRelaySourceMessage: async (id, update) => { const next = { ...saved.get(id), ...update }; saved.set(id, next); return next; },
	};
	const larkClient = {
		im: { v1: {
			message: { create: async ({ data }) => {
				if (data.receive_id === failTargetChatId && !failedTarget) { failedTarget = true; throw new Error(`target unavailable: ${data.receive_id}`); }
				sent.push(data); return { data: { message_id: `om_target_${sent.length}` } };
			}, update: async (payload) => {
				if (payload.path.message_id === failUpdateMessageId) { const error = new Error('Request failed with status code 400'); error.response = { data: { code: 230011, msg: 'message has been recalled' } }; throw error; }
				updated.push(payload); return { code: 0, data: {} };
			}, patch: async (payload) => { patched.push(payload); return { code: 0, data: {} }; } },
			image: { create: async () => { if (imageError) throw imageError; return imageResponse; } },
			file: { create: async () => ({ file_key: 'file_target' }) },
		} },
	};
	const listCalls = [];
	const resourceCalls = [];
	const sourceApi = {
		messageList: async (params) => {
			listCalls.push(params);
			if (messageList) return messageList(params);
			if (messageListDelayMs) await new Promise((resolve) => setTimeout(resolve, messageListDelayMs));
			return { data: { items: messages, has_more: false } };
		},
		messageResourceGet: async ({ fileKey, messageId }) => {
			resourceCalls.push({ fileKey, messageId });
			// A card frequently carries an image key whose resource the sender's
			// own client renders but the API has already dropped.
			if (resourceErrorKeys.includes(fileKey)) throw new Error(`读取飞书消息资源失败（HTTP 400）：14005 Resource Has Been Deleted`);
			return ({
			headers: { 'content-type': fileKey.startsWith('img') ? 'image/png' : 'video/mp4', 'content-disposition': `attachment; filename=${fileKey}.bin`, 'content-length': '5' },
			getReadableStream: () => Readable.from([Buffer.from('bytes')]),
		});
		},
	};
	const relay = createGroupRelay({
		larkClient, sourceApi, ledger, workbench: { publishActionCard: async () => { throw new Error('action card should be off by default'); } }, logger: { info() {}, error() {}, warn() {}, ...(logger ?? {}) },
		canWrite,
		config: {
			enabled: true, targetChatId: 'oc_summary', intervalSeconds: 10, sourceConcurrency, historyLookbackSeconds: 300, overlapSeconds: 30, outboundCard,
			bootstrapMode: 'forward_existing', sources: sources ?? [{ key: 'anqiang', tag: 'anqiang', chatId: 'oc_source', chatName: '马安强 (1)', targetChatIds }],
			webhooksByChatId: webhooksByChatId ? new Map(Object.entries(webhooksByChatId)) : undefined,
			webhookKeywordsByChatId: webhookKeywordsByChatId ? new Map(Object.entries(webhookKeywordsByChatId)) : undefined,
			larkAgentXResourceUrl, larkAgentXToken,
		},
	});
	return { relay, sent, updated, patched, saved, listCalls, sourceStates, resourceCalls };
}

test('OAuth backfill keeps the official resource ID and deduplicates subsequent OAuth polling', async () => {
	const stamp = Date.now();
	const official = { message_id: 'om_backfill', chat_id: 'oc_source', msg_type: 'image', create_time: String(stamp), body: { content: '{"image_key":"img_source"}' } };
	const input = { msg_id: '7685299326167305463', chat_id: '7684122107030031634', msg_type_name: 'IMAGE', create_time: stamp, content_data: { imageV2: { imageKey: 'img_source' } } };
	const message = await readLarkAgentXBackfill(input, { chatId: 'oc_source' }, { sourceApi: { messageList: async () => ({ data: { items: [official] } }) }, logger: { info() {} } });
	const { relay, sent, saved, resourceCalls } = createHarness([official]);
	const source = { key: 'anqiang', tag: 'anqiang', resolvedChatId: message.oauth_chat_id, targetChatId: 'oc_summary', targetChatIds: ['oc_summary'] };
	assert.equal((await relay.processInbound(message, source)).status, 'sent');
	assert.equal((await relay.processInbound(message, source)).status, 'duplicate');
	await relay.tick();
	assert.equal(sent.length, 1);
	assert.deepEqual(resourceCalls, [{ messageId: 'om_backfill', fileKey: 'img_source' }]);
	assert.equal(saved.get('om_backfill').message.larkagentx_origin.message_id, input.msg_id);
});

test('a fenced relay observes no source messages and never sends', async () => {
	const message = { message_id: 'om_fenced', msg_type: 'text', create_time: String(Date.now()), body: { content: JSON.stringify({ text: 'must not send' }) } };
	const { relay, sent } = createHarness([message], { canWrite: async () => ({ allowed: false, writer_id: 'relay-edge-47' }) });
	await relay.tick();
	assert.equal(sent.length, 0);
	assert.equal(relay.status().writer_state, 'fenced');
	assert.match(relay.status().last_tick_error, /relay 写入权归属/);
});

test('a LarkAgentX source message uses the existing relay ledger and fan-out', async () => {
	const { relay, sent, saved } = createHarness([]);
	const source = { key: 'anqiang', tag: 'anqiang', resolvedChatId: '767_source', targetChatId: 'oc_summary', targetChatIds: ['oc_summary'] };
	const message = { message_id: 'larkx_live_1', msg_type: 'text', create_time: String(Date.now()), body: { content: JSON.stringify({ text: '实时内容' }) } };
	const first = await relay.processInbound(message, source);
	assert.equal(first.status, 'sent');
	assert.equal(sent.length, 1);
	assert.equal(JSON.parse(sent[0].content).text, '#anqiang\n实时内容');
	assert.equal(saved.get('larkx_live_1').status, 'sent');
	const duplicate = await relay.processInbound(message, source);
	assert.equal(duplicate.status, 'duplicate');
	assert.equal(sent.length, 1);
});

test('a WebSocket ID and an official ID for the same source content are cross-deduplicated', async () => {
	const stamp = Date.now();
	const official = { message_id: 'om_official_same', msg_type: 'interactive', create_time: String(stamp), body: { content: JSON.stringify(CARD_2_0_TRADING_NOTE) } };
	const live = { message_id: '768_ws_same', msg_type: 'interactive', create_time: String(stamp + 500), body: { content: JSON.stringify(CARD_2_0_TRADING_NOTE) } };
	const { relay, sent } = createHarness([]);
	const source = { key: 'anqiang', tag: 'anqiang', resolvedChatId: '767_source', targetChatId: 'oc_summary', targetChatIds: ['oc_summary'] };
	assert.equal((await relay.processInbound(official, source)).status, 'sent');
	assert.equal((await relay.processInbound(live, source)).status, 'duplicate');
	assert.equal(sent.length, 1);
});

test('a WebSocket post image and official post image ignore transport metadata when deduplicating', async () => {
	const stamp = Date.now();
	const wsMessage = {
		message_id: '768_post_image', msg_type: 'post', create_time: String(stamp),
		body: { content: JSON.stringify({ zh_cn: { title: '', content: [[{ tag: 'text', text: 'same post' }, { tag: 'img', image_key: 'img_same', larkagentx_resource: { image_id: 'img_same', key_hex: 'redacted', iv_hex: 'redacted' } }]] } }) },
	};
	const officialMessage = {
		message_id: 'om_post_image', msg_type: 'post', create_time: String(stamp + 700),
		body: { content: JSON.stringify({ zh_cn: { title: '', content: [[{ tag: 'text', text: 'same post' }, { tag: 'img', image_key: 'img_same', width: 1290, height: 2796 }]], content_v2: [[{ tag: 'text', text: 'same post' }, { tag: 'img', image_key: 'img_same', width: 1290, height: 2796 }]] } }) },
	};
	const { relay, sent, saved } = createHarness([officialMessage, wsMessage]);
	const source = { key: 'anqiang', tag: 'anqiang', resolvedChatId: '767_source', targetChatId: 'oc_summary', targetChatIds: ['oc_summary'] };
	saved.set(wsMessage.message_id, { sourceMessageId: wsMessage.message_id, sourceKey: source.key, sourceCreateTime: stamp, status: 'sent', message: wsMessage });
	const result = await relay.repairFromOfficial({ fromCreateTime: stamp - 1000, toCreateTime: stamp + 2000, sourceKeys: ['anqiang'] });
	assert.equal(result.sent, 0);
	assert.equal(result.deduplicated, 2);
	assert.equal(sent.length, 0);
});

test('official gap repair forwards a missed interactive card and does not resend it', async () => {
	const stamp = Date.now();
	const missed = { message_id: 'om_gap_card', chat_id: 'oc_source', msg_type: 'interactive', create_time: String(stamp), body: { content: JSON.stringify({ schema: '2.0', body: { elements: [{ tag: 'markdown', content: '最新安强内容' }] } }) } };
	const { relay, sent, saved } = createHarness([missed]);
	const first = await relay.repairFromOfficial({ fromCreateTime: stamp - 1000, toCreateTime: stamp + 1000, sourceKeys: ['anqiang'] });
	assert.equal(first.sent, 1);
	assert.equal(first.deduplicated, 0);
	assert.equal(saved.get('om_gap_card').status, 'sent');
	const second = await relay.repairFromOfficial({ fromCreateTime: stamp - 1000, toCreateTime: stamp + 1000, sourceKeys: ['anqiang'] });
	assert.equal(second.sent, 0);
	assert.equal(second.deduplicated, 1);
	assert.equal(sent.length, 1);
});

test('official gap repair replaces a websocket card placeholder with the full official card', async () => {
	const stamp = Date.now();
	const card = { schema: '2.0', body: { elements: [{ tag: 'markdown', content: '书房猫完整卡片' }] } };
	const official = { message_id: 'om_cat_placeholder', chat_id: 'oc_source', msg_type: 'interactive', create_time: String(stamp), body: { content: JSON.stringify(card) } };
	const { relay, sent, saved } = createHarness([official]);
	saved.set(official.message_id, { sourceMessageId: official.message_id, sourceKey: 'anqiang', sourceCreateTime: stamp, status: 'sent', message: { msg_type: 'text', body: { content: JSON.stringify({ text: '[card] [卡片]' }) } }, targetMessageIds: [{ targetChatId: 'oc_summary', messageId: 'old', msgType: 'text' }] });
	const result = await relay.repairFromOfficial({ fromCreateTime: stamp - 1000, toCreateTime: stamp + 1000, sourceKeys: ['anqiang'], forcePlaceholderCards: true });
	assert.equal(result.sent, 1);
	assert.equal(result.replaced_placeholders, 1);
	assert.equal(saved.get(official.message_id).status, 'sent');
	assert.equal(JSON.parse(sent[0].content).text, '#anqiang\n[interactive]\n书房猫完整卡片');
});

test('official gap repair does not skip a missed message before a later sent row', async () => {
	const stamp = Date.now();
	const missed = { message_id: 'om_gap_before_later', msg_type: 'interactive', create_time: String(stamp), body: { content: JSON.stringify({ schema: '2.0', body: { elements: [{ tag: 'markdown', content: '窗口前半段漏收消息' }] } }) } };
	const later = { message_id: 'om_later_sent', msg_type: 'text', create_time: String(stamp + 5000), body: { content: JSON.stringify({ text: '后续已发送消息' }) } };
	const { relay, sent, saved } = createHarness([missed, later]);
	saved.set(later.message_id, { sourceMessageId: later.message_id, sourceKey: 'anqiang', sourceChatId: 'oc_source', sourceCreateTime: stamp + 5000, status: 'sent', message: later, targetMessageIds: [] });
	const result = await relay.repairFromOfficial({ fromCreateTime: stamp - 1000, toCreateTime: stamp + 6000, sourceKeys: ['anqiang'] });
	assert.equal(result.sent, 1);
	assert.equal(result.deduplicated, 1);
	assert.equal(saved.get(missed.message_id).status, 'sent');
	assert.equal(sent.length, 1);
});

test('independent source groups are polled with bounded concurrency', async () => {
	const sources = Array.from({ length: 3 }, (_, index) => ({ key: `source-${index}`, tag: `source-${index}`, chatId: `oc_source_${index}`, chatName: `source-${index}`, targetChatIds: [] }));
	const { relay } = createHarness([], { sources, messageListDelayMs: 100, sourceConcurrency: 3 });
	const started = performance.now();
	await relay.tick();
	const elapsed = performance.now() - started;
	assert.ok(elapsed < 240, `expected concurrent polling, took ${elapsed.toFixed(1)}ms`);
	assert.equal(relay.status().source_concurrency, 3);
});

test('image is relayed once as one tagged rich-text bubble and source ID is deduplicated', async () => {
	const message = { message_id: 'om_image_1', msg_type: 'image', create_time: String(Date.now()), body: { content: JSON.stringify({ image_key: 'img_source' }) } };
	const { relay, sent, saved } = createHarness([message]);
	await relay.tick();
	await relay.tick();
	assert.equal(sent.length, 1);
	assert.equal(sent[0].msg_type, 'post');
	assert.deepEqual(JSON.parse(sent[0].content), { zh_cn: { title: '', content: [[{ tag: 'text', text: '#anqiang' }], [{ tag: 'img', image_key: 'img_target' }]] } });
	assert.deepEqual(saved.get('om_image_1').targetMessageIds, [{ targetChatId: 'oc_summary', messageId: 'om_target_1', msgType: 'post' }]);
});

test('a route-specific target fans one source message out to the summary and dedicated group', async () => {
	const message = { message_id: 'om_fanout_1', msg_type: 'text', create_time: String(Date.now()), body: { content: JSON.stringify({ text: 'liwei update' }) } };
	const { relay, sent, saved } = createHarness([message], { targetChatIds: ['oc_liwei_forward'] });
	await relay.tick();
	assert.equal(sent.length, 2);
	assert.deepEqual(sent.map((item) => item.receive_id), ['oc_summary', 'oc_liwei_forward']);
	assert.deepEqual(saved.get('om_fanout_1').targetMessageIds, [
		{ targetChatId: 'oc_summary', messageId: 'om_target_1', msgType: 'text' },
		{ targetChatId: 'oc_liwei_forward', messageId: 'om_target_2', msgType: 'text' },
	]);
});

test('a partial fan-out keeps successful target IDs so retry only needs the failed target', async () => {
	const message = { message_id: 'om_partial_1', msg_type: 'text', create_time: String(Date.now()), body: { content: JSON.stringify({ text: 'partial update' }) } };
	const { relay, sent, saved } = createHarness([message], { targetChatIds: ['oc_liwei_forward'], failTargetChatId: 'oc_liwei_forward', retryFailed: true });
	await relay.tick();
	assert.equal(sent.length, 1);
	assert.equal(saved.get('om_partial_1').status, 'failed');
	assert.deepEqual(saved.get('om_partial_1').targetMessageIds, [{ targetChatId: 'oc_summary', messageId: 'om_target_1', msgType: 'text' }]);
	assert.match(saved.get('om_partial_1').errorMessage, /oc_liwei_forward/);
	await relay.tick();
	assert.equal(sent.length, 2);
	assert.deepEqual(saved.get('om_partial_1').targetMessageIds, [
		{ targetChatId: 'oc_summary', messageId: 'om_target_1', msgType: 'text' },
		{ targetChatId: 'oc_liwei_forward', messageId: 'om_target_2', msgType: 'text' },
	]);
	assert.equal(saved.get('om_partial_1').status, 'sent');
});

test('image accepts the SDK nested response shape and reports a missing upload scope safely', async () => {
	const message = { message_id: 'om_image_nested', msg_type: 'image', create_time: String(Date.now()), body: { content: JSON.stringify({ image_key: 'img_source' }) } };
	const nested = createHarness([message], { imageResponse: { data: { image_key: 'img_target_nested' } } });
	await nested.relay.tick();
	assert.equal(JSON.parse(nested.sent[0].content).zh_cn.content[1][0].image_key, 'img_target_nested');

	const denied = createHarness([message], { imageError: { message: 'Request failed with status code 400', response: { data: { code: 99991672, msg: 'Access denied. One of the following scopes is required: [im:resource:upload, im:resource].' } } } });
	await denied.relay.tick();
	assert.equal(denied.saved.get('om_image_nested').status, 'failed');
	assert.equal(denied.saved.get('om_image_nested').errorMessage, '飞书图片上传失败：机器人应用缺少 im:resource:upload（或 im:resource）应用身份权限');
});

test('rich text retains text, image and video under one tag in one outgoing post', async () => {
	const message = {
		message_id: 'om_post_1', msg_type: 'post', create_time: String(Date.now()),
		body: { content: JSON.stringify({ title: '原始标题', content: [[{ tag: 'text', text: '原文' }, { tag: 'img', image_key: 'img_post' }], [{ tag: 'media', file_key: 'video_post' }]] }) },
	};
	const { relay, sent } = createHarness([message]);
	await relay.tick();
	assert.equal(sent.length, 1);
	assert.equal(sent[0].msg_type, 'post');
	const content = JSON.parse(sent[0].content);
	assert.deepEqual(content.zh_cn.content[0], [{ tag: 'text', text: '#anqiang' }]);
	assert.deepEqual(content.zh_cn.content[1], [{ tag: 'text', text: '原文' }, { tag: 'img', image_key: 'img_target' }]);
	assert.deepEqual(content.zh_cn.content[2], [{ tag: 'media', file_key: 'file_target' }]);
});

test('ordinary files preserve their tag in the single native file bubble', async () => {
	const message = { message_id: 'om_file_1', msg_type: 'file', create_time: String(Date.now()), body: { content: JSON.stringify({ file_key: 'file_source', file_name: 'source.pdf' }) } };
	const { relay, sent } = createHarness([message]);
	await relay.tick();
	assert.equal(sent.length, 1);
	assert.equal(sent[0].msg_type, 'file');
	assert.deepEqual(JSON.parse(sent[0].content), { file_key: 'file_target', file_name: '#anqiang file_source.bin' });
});

test('group system notices are filtered before they can be tagged or sent', async () => {
	const message = { message_id: 'om_system_1', msg_type: 'system', create_time: String(Date.now()), body: { content: JSON.stringify({ template: '{to_chatters} joined the group' }) } };
	const { relay, sent, saved } = createHarness([message]);
	await relay.tick();
	assert.equal(sent.length, 0);
	assert.equal(saved.get('om_system_1').status, 'filtered_system');
});

test('the Anqiang source filter drops its configured phrase before webhook or API delivery', async () => {
	const message = { message_id: 'om_anqiang_blocked', msg_type: 'text', create_time: String(Date.now()), body: { content: JSON.stringify({ text: '般若星登山的川柏' }) } };
	const { relay, sent, saved } = createHarness([]);
	const source = { key: 'anqiang', tag: 'anqiang', chatName: '安强训练营', resolvedChatId: 'oc_source', targetChatId: 'oc_summary', targetChatIds: ['oc_summary'] };
	assert.equal((await relay.processInbound(message, source)).status, 'filtered');
	assert.equal(sent.length, 0);
	assert.equal(saved.get(message.message_id).status, 'filtered_system');
});

test('the Anqiang phrase does not filter an unrelated source', async () => {
	const message = { message_id: 'om_liwei_phrase', msg_type: 'text', create_time: String(Date.now()), body: { content: JSON.stringify({ text: '般若星登山的川柏' }) } };
	const { relay, sent } = createHarness([]);
	const source = { key: 'liwei', tag: 'liwei', chatName: '立伟群', resolvedChatId: 'oc_liwei', targetChatId: 'oc_summary', targetChatIds: ['oc_summary'] };
	assert.equal((await relay.processInbound(message, source)).status, 'sent');
	assert.equal(sent.length, 1);
});

test('an edited text source updates the original single outgoing message instead of sending another bubble', async () => {
	const message = { message_id: 'om_text_edit_1', msg_type: 'text', create_time: String(Date.now()), body: { content: JSON.stringify({ text: '第一版' }) } };
	const { relay, sent, updated } = createHarness([message]);
	await relay.tick();
	message.updated = true;
	message.update_time = String(Date.now() + 1_000);
	message.body.content = JSON.stringify({ text: '修订版' });
	await relay.tick();
	assert.equal(sent.length, 1);
	assert.equal(updated.length, 1);
	assert.deepEqual(updated[0], { path: { message_id: 'om_target_1' }, data: { msg_type: 'text', content: JSON.stringify({ text: '#anqiang\n修订版' }) } });
});

test('an edited rich-text source refreshes image/video keys in its original outgoing post', async () => {
	const message = {
		message_id: 'om_post_edit_1', msg_type: 'post', create_time: String(Date.now()),
		body: { content: JSON.stringify({ title: '', content: [[{ tag: 'text', text: '第一版' }, { tag: 'img', image_key: 'img_edit' }], [{ tag: 'media', file_key: 'video_edit' }]] }) },
	};
	const { relay, sent, updated } = createHarness([message]);
	await relay.tick();
	message.updated = true;
	message.update_time = String(Date.now() + 1_000);
	message.body.content = JSON.stringify({ title: '', content: [[{ tag: 'text', text: '修订版' }, { tag: 'img', image_key: 'img_edit_2' }], [{ tag: 'media', file_key: 'video_edit_2' }]] });
	await relay.tick();
	assert.equal(sent.length, 1);
	assert.equal(updated.length, 1);
	assert.equal(updated[0].data.msg_type, 'post');
	const content = JSON.parse(updated[0].data.content);
	assert.deepEqual(content.zh_cn.content[0], [{ tag: 'text', text: '#anqiang' }]);
	assert.equal(content.zh_cn.content[1][0].text, '修订版');
	assert.equal(content.zh_cn.content[1][1].image_key, 'img_target');
	assert.equal(content.zh_cn.content[2][0].file_key, 'file_target');
});

test('interactive cards relay their portable text and links for every configured target group', async () => {
	const message = {
		message_id: 'om_interactive_1', msg_type: 'interactive', create_time: String(Date.now()),
		body: { content: JSON.stringify({ title: null, elements: [[
			{ tag: 'text', text: '8-23 20:04:05\n通过网盘分享的文件：安强8.23回放.mp4\n链接:' },
			{ tag: 'a', href: 'https://pan.baidu.com/s/example', text: 'https://pan.baidu.com/s/example' },
			{ tag: 'text', text: '提取码: g6ap' },
		]] }) },
	};
	const { relay, sent, saved } = createHarness([message], { targetChatIds: ['oc_anqiang_forward'] });
	await relay.tick();
	assert.equal(sent.length, 2);
	assert.ok(sent.every((item) => item.msg_type === 'text'));
	assert.deepEqual(JSON.parse(sent[0].content), { text: '#anqiang\n[interactive]\n8-23 20:04:05\n通过网盘分享的文件：安强8.23回放.mp4\n链接:\nhttps://pan.baidu.com/s/example\n提取码: g6ap' });
	assert.equal(saved.get('om_interactive_1').portableSummaryVersion, 'interactive-text-summary-v1');
});

test('an edited interactive card updates the original portable summary instead of sending a duplicate', async () => {
	const message = { message_id: 'om_interactive_edit_1', msg_type: 'interactive', create_time: String(Date.now()), body: { content: JSON.stringify({ elements: [[{ tag: 'text', text: '第一版卡片' }]] }) } };
	const { relay, sent, updated } = createHarness([message]);
	await relay.tick();
	message.updated = true;
	message.update_time = String(Date.now() + 1_000);
	message.body.content = JSON.stringify({ elements: [[{ tag: 'text', text: '修订后的卡片正文' }, { tag: 'a', text: '查看详情', href: 'https://example.test/detail' }]] });
	await relay.tick();
	assert.equal(sent.length, 1);
	assert.deepEqual(updated[0], { path: { message_id: 'om_target_1' }, data: { msg_type: 'text', content: JSON.stringify({ text: '#anqiang\n[interactive]\n修订后的卡片正文\n查看详情\nhttps://example.test/detail' }) } });
});

test('a card carrying its content as an image relays the image, not a caption about it', async () => {
	// The #anqiang training-camp group posts cards whose payload is an image;
	// walking the card for text alone forwarded a bubble with no content.
	const message = {
		message_id: 'om_card_image_1', msg_type: 'interactive', create_time: String(Date.now()),
		body: { content: JSON.stringify({ title: null, elements: [[{ tag: 'img', image_key: 'img_card_body' }, { tag: 'text', text: '9-8 复盘' }]] }) },
	};
	const { relay, sent } = createHarness([message]);
	await relay.tick();
	assert.equal(sent.length, 1);
	assert.equal(sent[0].msg_type, 'post');
	assert.deepEqual(JSON.parse(sent[0].content), {
		zh_cn: { title: '', content: [[{ tag: 'text', text: '#anqiang' }], [{ tag: 'text', text: '9-8 复盘' }], [{ tag: 'img', image_key: 'img_target' }]] },
	});
});

test('the client-upgrade banner never reaches the target group as card text', async () => {
	// Feishu returns this fixed notice for cards its message API cannot express.
	// Relaying it verbatim produced bubbles that read like analyst content.
	const message = {
		message_id: 'om_card_banner_1', msg_type: 'interactive', create_time: String(Date.now()),
		body: { content: JSON.stringify({ title: null, elements: [[{ tag: 'img', image_key: 'img_card_body' }, { tag: 'text', text: '请升级至最新版本客户端，以查看内容' }, { tag: 'text', text: '' }]] }) },
	};
	const { relay, sent } = createHarness([message]);
	await relay.tick();
	const content = JSON.parse(sent[0].content);
	assert.equal(sent[0].msg_type, 'post');
	assert.equal(JSON.stringify(content).includes('请升级至最新版本客户端'), false);
	assert.deepEqual(content.zh_cn.content, [[{ tag: 'text', text: '#anqiang' }], [{ tag: 'img', image_key: 'img_target' }]]);
});

test('a card whose image resource is already deleted says so instead of forwarding the banner', async () => {
	// The live #anqiang case on 2026-09-08: banner text plus an image key the
	// API answers with 14005 Resource Has Been Deleted.  Nothing is relayable,
	// and the reader needs to know that rather than receive a upgrade notice.
	const message = {
		message_id: 'om_card_dead_1', msg_type: 'interactive', create_time: String(Date.now()),
		body: { content: JSON.stringify({ title: null, elements: [[{ tag: 'img', image_key: 'img_dead' }, { tag: 'text', text: '请升级至最新版本客户端，以查看内容' }, { tag: 'text', text: '' }]] }) },
	};
	const { relay, sent } = createHarness([message], { resourceErrorKeys: ['img_dead'] });
	await relay.tick();
	assert.equal(sent[0].msg_type, 'text');
	const { text } = JSON.parse(sent[0].content);
	assert.equal(text.includes('请升级至最新版本客户端'), false);
	assert.match(text, /^#anqiang\n\[interactive\]\n卡片内容无法通过接口获取（1 张图片资源已失效）/);
});

test('a text-only card keeps its existing portable summary shape', async () => {
	// The baidu-pan share cards this group sent until 2026-09-06 must relay
	// unchanged, so widening card support cannot regress the working case.
	const message = {
		message_id: 'om_card_text_1', msg_type: 'interactive', create_time: String(Date.now()),
		body: { content: JSON.stringify({ title: null, elements: [[{ tag: 'text', text: '9-6 19:39:27\n通过百度网盘分享的文件' }, { tag: 'a', href: 'https://pan.baidu.com/s/example', text: 'https://pan.baidu.com/s/example' }]] }) },
	};
	const { relay, sent, saved } = createHarness([message]);
	await relay.tick();
	assert.equal(sent[0].msg_type, 'text');
	assert.deepEqual(JSON.parse(sent[0].content), { text: '#anqiang\n[interactive]\n9-6 19:39:27\n通过百度网盘分享的文件\nhttps://pan.baidu.com/s/example' });
	assert.equal(saved.get('om_card_text_1').portableSummaryVersion, 'interactive-text-summary-v1');
});


// --- card JSON 2.0 -----------------------------------------------------------
// The #anqiang bot moved to 2.0 cards on 2026-09-08.  Read without
// card_msg_content_type=user_card_content, the API renders every one as
// "请升级至最新版本客户端，以查看内容" plus a deleted image key.

const CARD_2_0_TRADING_NOTE = {
	schema: '2.0', config: { enable_forward_interaction: false, streaming_mode: false },
	body: { direction: 'vertical', padding: '12px 12px 12px 12px', elements: [
		{ tag: 'markdown', element_id: '_2', content: '长光华X回到了8月17日高点了，跑赢指数到320左右目标完成，考虑先出局，等几天在接回。致尚在190左右接回。' },
	] },
};

test('the source poll asks the API for the card JSON the sender posted', async () => {
	const { relay, listCalls } = createHarness([]);
	await relay.tick();
	assert.ok(listCalls.length >= 1);
	for (const params of listCalls) assert.equal(params.card_msg_content_type, 'user_card_content');
});

test('a card JSON 2.0 markdown body relays as its text', async () => {
	// The live 10:25 card from 安强训练营1 on 2026-09-08, verbatim.
	const message = { message_id: 'om_card_v2_1', msg_type: 'interactive', create_time: String(Date.now()), body: { content: JSON.stringify(CARD_2_0_TRADING_NOTE) } };
	const { relay, sent } = createHarness([message]);
	await relay.tick();
	assert.equal(sent[0].msg_type, 'text');
	assert.deepEqual(JSON.parse(sent[0].content), { text: '#anqiang\n[interactive]\n长光华X回到了8月17日高点了，跑赢指数到320左右目标完成，考虑先出局，等几天在接回。致尚在190左右接回。' });
});

test('a card JSON 2.0 header, div and image are all carried', async () => {
	// 2.0 names the image resource img_key and wraps a div's text in an object;
	// both were invisible to a walker written for 1.0 element shapes.
	const card = {
		schema: '2.0', header: { title: { tag: 'plain_text', content: '9.8 收盘' } },
		body: { elements: [
			{ tag: 'div', text: { tag: 'lark_md', content: '创业板小阴调整' } },
			{ tag: 'img', img_key: 'img_v2_body', alt: { tag: 'plain_text', content: '' } },
		] },
	};
	const message = { message_id: 'om_card_v2_2', msg_type: 'interactive', create_time: String(Date.now()), body: { content: JSON.stringify(card) } };
	const { relay, sent } = createHarness([message]);
	await relay.tick();
	assert.equal(sent[0].msg_type, 'post');
	assert.deepEqual(JSON.parse(sent[0].content).zh_cn.content, [
		[{ tag: 'text', text: '#anqiang' }], [{ tag: 'text', text: '9.8 收盘' }], [{ tag: 'text', text: '创业板小阴调整' }], [{ tag: 'img', image_key: 'img_target' }],
	]);
});

test('an LarkAgentX interactive schema 2.0 card unwraps property content before webhook delivery', async () => {
	await withFetchMock(
		() => ({ ok: true, json: async () => ({ code: 0 }) }),
		async (webhookCalls) => {
			const card = {
				schema: '2.0',
				body: { elements: [{
					tag: 'column_set',
					property: { columns: [{
						tag: 'column',
						property: { elements: [{
							tag: 'markdown',
							property: { elements: [{ tag: 'plain_text', property: { content: '请升级至最新版本客户端，以查看内容' } }] },
						}, {
							tag: 'div',
							property: { text: { tag: 'plain_text', property: {} } },
						}] },
					}] },
				}] },
				newBody: { tag: 'body', property: { elements: [{
					tag: 'markdown',
					property: { elements: [{ tag: 'plain_text', property: { content: '安强 interactive 2.0 正文' } }] },
				}] } },
				source: 'json',
			};
			const message = { message_id: 'om_anqiang_internal_card', msg_type: 'interactive', create_time: String(Date.now()), body: { content: JSON.stringify(card) } };
			const { relay, sent } = createHarness([message], {
				targetChatIds: ['oc_anqiang_forward'],
				webhooksByChatId: { oc_summary: 'https://x/summary-hook', oc_anqiang_forward: 'https://x/anqiang-hook' },
				webhookKeywordsByChatId: { oc_summary: '汇总', oc_anqiang_forward: 'anqiang' },
			});
			await relay.tick();
			assert.equal(sent.length, 0);
			assert.equal(webhookCalls.length, 2);
			const anqiangCall = webhookCalls.find((call) => call.url === 'https://x/anqiang-hook');
			assert.equal(anqiangCall.body.msg_type, 'text');
			assert.equal(anqiangCall.body.content.text, '#anqiang\n[interactive]\n安强 interactive 2.0 正文');
			assert.equal(anqiangCall.body.content.text.includes('anqiang'), true);
			assert.equal(anqiangCall.body.content.text.includes('请升级至最新版本客户端'), false);
		},
	);
});


// --- outbound card JSON 2.0 ---------------------------------------------------
// From 2026-09-08 the relay speaks card JSON 2.0 to every target group.  The
// legacy text/post shape stays behind FEISHU_GROUP_RELAY_OUTBOUND_CARD=false,
// which is what the harness default above exercises.

function cardTextOf(sent) {
	const card = JSON.parse(sent.content);
	assert.equal(card.schema, '2.0');
	return card.body.elements[0].text.content;
}

test('a text source relays as a 2.0 card led by the route tag', async () => {
	const message = { message_id: 'om_c_text', msg_type: 'text', create_time: String(Date.now()), body: { content: JSON.stringify({ text: '致尚在190左右接回。' }) } };
	const { relay, sent } = createHarness([message], { outboundCard: true });
	await relay.tick();
	assert.equal(sent[0].msg_type, 'interactive');
	assert.equal(cardTextOf(sent[0]), '#anqiang\n致尚在190左右接回。');
});

test('rich text with images relays as a card carrying the re-uploaded images', async () => {
	const message = { message_id: 'om_c_post', msg_type: 'post', create_time: String(Date.now()), body: { content: JSON.stringify({ zh_cn: { title: '', content: [[{ tag: 'text', text: '收盘复盘' }], [{ tag: 'img', image_key: 'img_src' }]] } }) } };
	const { relay, sent } = createHarness([message], { outboundCard: true });
	await relay.tick();
	assert.equal(sent[0].msg_type, 'interactive');
	const card = JSON.parse(sent[0].content);
	assert.equal(card.body.elements[0].text.content, '#anqiang\n收盘复盘');
	assert.deepEqual(card.body.elements[1], { tag: 'img', img_key: 'img_target', alt: { tag: 'plain_text', content: '' } });
});

test('rich text carrying a video stays a post, since a card cannot embed it', async () => {
	const message = { message_id: 'om_c_video', msg_type: 'post', create_time: String(Date.now()), body: { content: JSON.stringify({ zh_cn: { title: '', content: [[{ tag: 'text', text: '回放' }], [{ tag: 'media', file_key: 'file_src' }]] } }) } };
	const { relay, sent } = createHarness([message], { outboundCard: true });
	await relay.tick();
	assert.equal(sent[0].msg_type, 'post');
});

test('a source 2.0 card relays as an outbound 2.0 card with the same text', async () => {
	const message = { message_id: 'om_c_card', msg_type: 'interactive', create_time: String(Date.now()), body: { content: JSON.stringify(CARD_2_0_TRADING_NOTE) } };
	const { relay, sent } = createHarness([message], { outboundCard: true });
	await relay.tick();
	assert.equal(sent[0].msg_type, 'interactive');
	assert.equal(cardTextOf(sent[0]), '#anqiang\n长光华X回到了8月17日高点了，跑赢指数到320左右目标完成，考虑先出局，等几天在接回。致尚在190左右接回。');
});

test('a direct image message relays as a card holding the image', async () => {
	const message = { message_id: 'om_c_img', msg_type: 'image', create_time: String(Date.now()), body: { content: JSON.stringify({ image_key: 'img_src' }) } };
	const { relay, sent } = createHarness([message], { outboundCard: true });
	await relay.tick();
	assert.equal(sent[0].msg_type, 'interactive');
	const card = JSON.parse(sent[0].content);
	assert.equal(card.body.elements[0].text.content, '#anqiang');
	assert.equal(card.body.elements[1].img_key, 'img_target');
});

test('an edited source updates the delivered card in place through patch, not update', async () => {
	const message = { message_id: 'om_c_edit', msg_type: 'text', create_time: String(Date.now()), body: { content: JSON.stringify({ text: '第一版' }) } };
	const { relay, sent, updated, patched } = createHarness([message], { outboundCard: true });
	await relay.tick();
	assert.equal(sent.length, 1);
	message.updated = true;
	message.update_time = String(Date.now() + 1000);
	message.body.content = JSON.stringify({ text: '修订版' });
	await relay.tick();
	assert.equal(sent.length, 1, 'an edit must not send a second bubble');
	assert.equal(updated.length, 0);
	assert.equal(patched.length, 1);
	assert.equal(JSON.parse(patched[0].data.content).body.elements[0].text.content, '#anqiang\n修订版');
});

test('an edit to a target delivered before the card upgrade keeps it a text bubble', async () => {
	const message = { message_id: 'om_legacy_edit', msg_type: 'text', create_time: String(Date.now()), body: { content: JSON.stringify({ text: '第一版' }) } };
	const { relay, sent, updated, patched, saved } = createHarness([message], { outboundCard: true });
	await relay.tick();
	assert.equal(sent.length, 1);
	// A pre-upgrade ledger row: the target entry carries no msgType.
	const record = saved.get('om_legacy_edit');
	record.target_message_ids = record.targetMessageIds.map(({ targetChatId, messageId }) => ({ targetChatId, messageId }));
	record.targetMessageIds = record.target_message_ids;
	message.updated = true;
	message.body.content = JSON.stringify({ text: '修订版' });
	message.update_time = String(Date.now() + 1000);
	await relay.tick();
	assert.equal(patched.length, 0);
	assert.equal(updated.length, 1);
	assert.equal(updated[0].data.msg_type, 'text');
	assert.equal(JSON.parse(updated[0].data.content).text, '#anqiang\n修订版');
});

test('a delivered target records the msg_type it was sent as', async () => {
	const message = { message_id: 'om_shape', msg_type: 'text', create_time: String(Date.now()), body: { content: JSON.stringify({ text: '内容' }) } };
	const { relay, saved } = createHarness([message], { outboundCard: true });
	await relay.tick();
	assert.equal(saved.get('om_shape').targetMessageIds[0].msgType, 'interactive');
});

test('an edit still reaches the other targets when one delivered bubble was recalled', async () => {
	const message = { message_id: 'om_recalled_target', msg_type: 'text', create_time: String(Date.now()), body: { content: JSON.stringify({ text: '第一版' }) } };
	const warnings = [];
	const { relay, sent, updated, saved } = createHarness([message], { targetChatIds: ['oc_liwei_forward'], failUpdateMessageId: 'om_target_2', logger: { warn: (line) => warnings.push(line) } });
	await relay.tick();
	assert.equal(sent.length, 2);
	message.updated = true;
	message.update_time = String(Date.now() + 1000);
	message.body.content = JSON.stringify({ text: '修订版' });
	await relay.tick();
	assert.equal(sent.length, 2, 'an edit must not send a second bubble');
	assert.equal(updated.length, 1);
	assert.equal(updated[0].path.message_id, 'om_target_1');
	assert.equal(saved.get('om_recalled_target').status, 'sent');
	assert.ok(warnings.some((line) => line.includes('om_target_2') && line.includes('230011')), warnings.join('\n'));
	assert.ok(!warnings.some((line) => line.includes('同步源消息编辑失败')), warnings.join('\n'));
});

function withFetchMock(handler, run) {
	const original = globalThis.fetch;
	const calls = [];
	globalThis.fetch = async (url, options) => {
		calls.push({ url, body: options?.body ? JSON.parse(options.body) : null });
		return handler(url, options);
	};
	return run(calls).finally(() => { globalThis.fetch = original; });
}

test('a webhook-configured target is delivered through the webhook, not the tenant API', async () => {
	await withFetchMock(
		() => ({ ok: true, json: async () => ({ code: 0 }) }),
		async (webhookCalls) => {
			const message = { message_id: 'om_webhook_1', msg_type: 'text', create_time: String(Date.now()), body: { content: JSON.stringify({ text: 'liwei update' }) } };
			const { relay, sent, saved } = createHarness([message], {
				targetChatIds: ['oc_liwei_forward'],
				webhooksByChatId: { oc_liwei_forward: 'https://open.feishu.cn/open-apis/bot/v2/hook/fake' },
			});
			await relay.tick();
			// The summary group still goes through the tenant API; only the
			// webhook-configured target is diverted.
			assert.equal(sent.length, 1);
			assert.equal(sent[0].receive_id, 'oc_summary');
			assert.equal(webhookCalls.length, 1);
			assert.equal(webhookCalls[0].url, 'https://open.feishu.cn/open-apis/bot/v2/hook/fake');
			assert.deepEqual(webhookCalls[0].body, { msg_type: 'text', content: { text: '#anqiang\nliwei update' } });
			const targets = saved.get('om_webhook_1').targetMessageIds;
			const webhookTarget = targets.find((entry) => entry.targetChatId === 'oc_liwei_forward');
			assert.match(webhookTarget.messageId, /^webhook-sent:oc_liwei_forward:\d+$/);
			assert.equal(saved.get('om_webhook_1').status, 'sent');
		},
	);
});

test('a webhook target receives its configured keyword without changing other targets', async () => {
	await withFetchMock(
		() => ({ ok: true, json: async () => ({ code: 0 }) }),
		async (webhookCalls) => {
			const message = { message_id: 'om_webhook_keyword', msg_type: 'text', create_time: String(Date.now()), body: { content: JSON.stringify({ text: '正文' }) } };
			const { relay, sent } = createHarness([message], {
				targetChatIds: ['oc_liwei_forward'],
				webhooksByChatId: { oc_liwei_forward: 'https://x/hook' },
				webhookKeywordsByChatId: { oc_liwei_forward: '汇总' },
			});
			await relay.tick();
			assert.equal(sent.length, 1);
			assert.equal(JSON.parse(sent[0].content).text, '#anqiang\n正文');
			assert.equal(webhookCalls.length, 1);
			assert.equal(webhookCalls[0].body.content.text, '#anqiang\n正文\n汇总');
		},
	);
});

test('a webhook-only interactive card keeps source image keys without tenant upload', async () => {
	await withFetchMock(
		() => ({ ok: true, json: async () => ({ code: 0 }) }),
		async (webhookCalls) => {
			const message = {
				message_id: 'om_webhook_card_image', msg_type: 'interactive', create_time: String(Date.now()),
				body: { content: JSON.stringify({ schema: '2.0', body: { elements: [
					{ tag: 'markdown', content: '安强图片卡片' }, { tag: 'img', img_key: 'img_v3_source_card' },
				] } }) },
			};
			const { relay, sent } = createHarness([message], {
				webhooksByChatId: { oc_summary: 'https://x/summary-hook' },
				webhookKeywordsByChatId: { oc_summary: '汇总' },
			});
			await relay.tick();
			assert.equal(sent.length, 0);
			assert.equal(webhookCalls.length, 1);
			assert.equal(webhookCalls[0].body.msg_type, 'post');
			assert.deepEqual(webhookCalls[0].body.content.post.zh_cn.content, [
				[{ tag: 'text', text: '#anqiang\n安强图片卡片' }],
				[{ tag: 'img', image_key: 'img_v3_source_card' }],
				[{ tag: 'text', text: '汇总' }],
			]);
		},
	);
});

test('a post image with a LarkAgentX descriptor bypasses OAuth resource lookup', async () => {
	const png = Buffer.from('\x89PNG\r\n\x1a\n', 'binary');
	await withFetchMock(
		(url) => {
			if (!String(url).startsWith('https://larkagentx.test/resource')) return { ok: true, json: async () => ({ code: 0 }) };
			return { ok: true, body: Readable.from([png]), headers: new Headers({ 'content-type': 'image/png', 'content-length': String(png.length) }) };
		},
		async (fetchCalls) => {
			const message = {
				message_id: 'om_post_larkagentx_image', msg_type: 'post', create_time: String(Date.now()),
				body: { content: JSON.stringify({ zh_cn: { title: '', content: [[{
					tag: 'img', image_key: 'img_v3_nested', larkagentx_resource: {
						image_id: 'img_v3_nested', source_id: 'om_larkagentx_image', key_hex: '00'.repeat(32), iv_hex: '11'.repeat(12),
					},
				}]] } }) },
			};
			const { relay, sent, resourceCalls } = createHarness([message], { larkAgentXResourceUrl: 'https://larkagentx.test/resource', larkAgentXToken: 'test-token' });
			await relay.tick();
			assert.equal(resourceCalls.length, 0);
			assert.equal(sent.length, 1);
			assert.equal(sent[0].msg_type, 'post');
			assert.equal(fetchCalls.length, 1);
			const resourceUrl = new URL(String(fetchCalls[0].url));
			assert.equal(resourceUrl.searchParams.get('image_id'), 'img_v3_nested');
			assert.equal(resourceUrl.searchParams.get('source_id'), 'om_larkagentx_image');
		});
});

test('a keyword-configured card webhook uses a text fallback because Card 2.0 keyword matching is unsupported', async () => {
	await withFetchMock(
		() => ({ ok: true, json: async () => ({ code: 0 }) }),
		async (webhookCalls) => {
			const message = { message_id: 'om_webhook_card_keyword', msg_type: 'text', create_time: String(Date.now()), body: { content: JSON.stringify({ text: '卡片正文' }) } };
			const { relay } = createHarness([message], {
				targetChatIds: ['oc_liwei_forward'], outboundCard: true,
				webhooksByChatId: { oc_liwei_forward: 'https://x/hook' },
				webhookKeywordsByChatId: { oc_liwei_forward: '汇总' },
			});
			await relay.tick();
			assert.equal(webhookCalls.length, 1);
			assert.equal(webhookCalls[0].body.msg_type, 'text');
			assert.equal(webhookCalls[0].body.content.text, '#anqiang\n卡片正文\n汇总');
		},
	);
});

test('a keyword-configured card webhook preserves images as a rich-text post', async () => {
	await withFetchMock(
		() => ({ ok: true, json: async () => ({ code: 0 }) }),
		async (webhookCalls) => {
			const message = { message_id: 'om_webhook_card_image_keyword', msg_type: 'image', create_time: String(Date.now()), body: { content: JSON.stringify({ image_key: 'img_source' }) } };
			const { relay } = createHarness([message], {
				targetChatIds: ['oc_liuzi'], outboundCard: true,
				webhooksByChatId: { oc_liuzi: 'https://x/hook' },
				webhookKeywordsByChatId: { oc_liuzi: '汇总' },
			});
			await relay.tick();
			assert.equal(webhookCalls.length, 1);
			assert.equal(webhookCalls[0].body.msg_type, 'post');
			assert.deepEqual(webhookCalls[0].body.content.post.zh_cn.content, [
				[{ tag: 'text', text: '#anqiang' }],
				[{ tag: 'img', image_key: 'img_target' }],
				[{ tag: 'text', text: '汇总' }],
			]);
		},
	);
});

test('a direct LarkAgentX image uses the source key when every target is webhook-backed', async () => {
	await withFetchMock(
		() => ({ ok: true, json: async () => ({ code: 0 }) }),
		async (webhookCalls) => {
			const message = {
				message_id: 'om_webhook_larkagentx_image', msg_type: 'image', create_time: String(Date.now()),
				body: { content: JSON.stringify({ image_key: 'img_v3_source', larkagentx_resource: { image_id: 'img_v3_source', key_hex: '00'.repeat(32), iv_hex: '11'.repeat(12) } }) },
			};
			const quotaError = Object.assign(new Error('Request failed with status code 400'), { response: { data: { code: 99991403, msg: "This month's API call quota has been exceeded" } } });
			const { relay, sent, saved } = createHarness([message], {
				targetChatIds: ['oc_liwei_forward'], imageError: quotaError,
				webhooksByChatId: { oc_summary: 'https://x/summary', oc_liwei_forward: 'https://x/liwei' },
				webhookKeywordsByChatId: { oc_summary: '汇总', oc_liwei_forward: 'liwei' },
			});
			await relay.tick();
			assert.equal(sent.length, 0, 'the tenant API must not be used when all targets have webhooks');
			assert.equal(webhookCalls.length, 2);
			assert.ok(webhookCalls.every((call) => call.body.msg_type === 'post'));
			assert.ok(webhookCalls.every((call) => JSON.stringify(call.body).includes('img_v3_source')));
			assert.equal(saved.get(message.message_id).status, 'sent');
		},
	);
});

test('a LarkAgentX post image keeps the source key when every target is webhook-backed', async () => {
	await withFetchMock(
		() => ({ ok: true, json: async () => ({ code: 0 }) }),
		async (webhookCalls) => {
			const message = {
				message_id: 'om_webhook_larkagentx_post_image', msg_type: 'post', create_time: String(Date.now()),
				body: { content: JSON.stringify({ zh_cn: { title: '', content: [[{ tag: 'text', text: 'quanneng 图片' }], [{ tag: 'img', image_key: 'img_v3_post_source', larkagentx_resource: { image_id: 'img_v3_post_source', key_hex: '00'.repeat(32), iv_hex: '11'.repeat(12) } }]] } }) },
			};
			const quotaError = Object.assign(new Error('Request failed with status code 400'), { response: { data: { code: 99991403, msg: "This month's API call quota has been exceeded" } } });
			const { relay, sent, saved } = createHarness([message], {
				targetChatIds: ['oc_quanneng_forward'], imageError: quotaError,
				webhooksByChatId: { oc_summary: 'https://x/summary', oc_quanneng_forward: 'https://x/quanneng' },
				webhookKeywordsByChatId: { oc_summary: '汇总', oc_quanneng_forward: 'quanneng' },
			});
			await relay.tick();
			assert.equal(sent.length, 0, 'the tenant API must not be used for a webhook-backed post image');
			assert.equal(webhookCalls.length, 2);
			assert.ok(webhookCalls.every((call) => call.body.msg_type === 'post'));
			assert.ok(webhookCalls.every((call) => JSON.stringify(call.body).includes('img_v3_post_source')));
			assert.equal(saved.get(message.message_id).status, 'sent');
		},
	);
});

test('a webhook target that already succeeded is not resent when another target is retried', async () => {
	await withFetchMock(
		() => ({ ok: true, json: async () => ({ code: 0 }) }),
		async (webhookCalls) => {
			const message = { message_id: 'om_webhook_retry', msg_type: 'text', create_time: String(Date.now()), body: { content: JSON.stringify({ text: 'x' }) } };
			const { relay, saved } = createHarness([message], {
				targetChatIds: ['oc_liwei_forward'],
				webhooksByChatId: { oc_liwei_forward: 'https://x/hook' },
				failTargetChatId: 'oc_summary', retryFailed: true,
			});
			await relay.tick();
			assert.equal(webhookCalls.length, 1);
			assert.equal(saved.get('om_webhook_retry').status, 'failed');
			await relay.tick();
			assert.equal(webhookCalls.length, 1, 'the already-succeeded webhook target must not be re-sent on retry');
			assert.equal(saved.get('om_webhook_retry').status, 'sent');
		},
	);
});

test('an edit skips a webhook-delivered target but still updates the tenant-API target', async () => {
	await withFetchMock(
		() => ({ ok: true, json: async () => ({ code: 0 }) }),
		async (webhookCalls) => {
			const message = { message_id: 'om_webhook_edit', msg_type: 'text', create_time: String(Date.now()), body: { content: JSON.stringify({ text: '第一版' }) } };
			const { relay, updated } = createHarness([message], {
				targetChatIds: ['oc_liwei_forward'],
				webhooksByChatId: { oc_liwei_forward: 'https://x/hook' },
			});
			await relay.tick();
			assert.equal(webhookCalls.length, 1);
			message.updated = true;
			message.update_time = String(Date.now() + 1000);
			message.body.content = JSON.stringify({ text: '修订版' });
			await relay.tick();
			assert.equal(webhookCalls.length, 1, 'an edit must not re-post to the webhook target');
			assert.equal(updated.length, 1);
			assert.equal(updated[0].path.message_id, 'om_target_1');
		},
	);
});

test('a message type the webhook cannot carry still uses the tenant API even when a webhook is configured', async () => {
	await withFetchMock(
		() => { throw new Error('must not call the webhook for a file message'); },
		async (webhookCalls) => {
			const message = { message_id: 'om_webhook_file', msg_type: 'file', create_time: String(Date.now()), body: { content: JSON.stringify({ file_key: 'file_source', file_name: 'source.pdf' }) } };
			const { relay, sent } = createHarness([message], {
				targetChatIds: ['oc_liwei_forward'],
				webhooksByChatId: { oc_liwei_forward: 'https://x/hook' },
			});
			await relay.tick();
			assert.equal(webhookCalls.length, 0);
			assert.equal(sent.length, 2, 'both the summary group and the webhook-configured target fall back to the tenant API');
		},
	);
});

// A faithful stand-in for im/v1/messages list: start_time filters in seconds,
// results are ascending by create_time and paged by an opaque token.
function pagedHistory(messages) {
	return async (params) => {
		const from = Number(params.start_time) * 1000;
		const matching = messages.filter((message) => Number(message.create_time) >= from)
			.sort((left, right) => Number(left.create_time) - Number(right.create_time));
		const offset = Number(params.page_token ?? 0);
		const items = matching.slice(offset, offset + params.page_size);
		const next = offset + items.length;
		return { data: { items, has_more: next < matching.length, ...(next < matching.length ? { page_token: String(next) } : {}) } };
	};
}

function textMessage(id, createTime) {
	return { message_id: id, msg_type: 'text', create_time: String(createTime), body: { content: JSON.stringify({ text: id }) } };
}

test('a restart after an outage longer than the reconcile lookback relays every message since the cursor', async () => {
	const now = Date.now();
	const cursor = now - 66 * 3600_000;
	const messages = [
		textMessage('om_before_cursor', cursor - 3600_000),
		textMessage('om_friday_evening', cursor + 3600_000),
		textMessage('om_saturday', cursor + 20 * 3600_000),
		textMessage('om_monday', now - 60_000),
	];
	const { relay, sent, saved } = createHarness(messages, {
		messageList: pagedHistory(messages),
		initialSourceStates: { anqiang: { chat_id: 'oc_source', cursor_create_time: cursor } },
	});
	await relay.tick();
	const relayed = sent.map((data) => JSON.parse(data.content).text);
	assert.deepEqual(relayed, ['#anqiang\nom_friday_evening', '#anqiang\nom_saturday', '#anqiang\nom_monday']);
	assert.equal(saved.get('om_before_cursor'), undefined);
});

test('a backlog beyond the page cap resumes from the newest seen message instead of skipping the tail', async () => {
	const now = Date.now();
	const cursor = now - 3 * 3600_000;
	const messages = Array.from({ length: 1500 }, (_, index) => textMessage(`om_backlog_${index}`, cursor + (index + 1) * 5_000));
	const { relay, sent, sourceStates } = createHarness(messages, {
		messageList: pagedHistory(messages),
		initialSourceStates: { anqiang: { chat_id: 'oc_source', cursor_create_time: cursor } },
	});
	await relay.tick();
	assert.equal(sent.length, 1000);
	assert.ok(sourceStates.get('anqiang').cursor_create_time < now);
	await relay.tick();
	const relayed = new Set(sent.map((data) => JSON.parse(data.content).text));
	assert.equal(relayed.size, 1500);
	assert.equal(sent.length, 1500);
});

test('an edited source is synced once, not again on every overlapping or reconcile poll', async () => {
	const createTime = Date.now() - 5_000;
	const message = textMessage('om_edit_once', createTime);
	const { relay, sent, updated } = createHarness([message]);
	await relay.tick();
	message.updated = true;
	message.update_time = String(createTime + 2_000);
	message.body.content = JSON.stringify({ text: '修订版' });
	await relay.tick();
	await relay.tick();
	await relay.tick();
	assert.equal(sent.length, 1);
	assert.equal(updated.length, 1);
	message.update_time = String(createTime + 4_000);
	message.body.content = JSON.stringify({ text: '二次修订' });
	await relay.tick();
	assert.equal(updated.length, 2);
});
