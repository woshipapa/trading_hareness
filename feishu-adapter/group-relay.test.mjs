import assert from 'node:assert/strict';
import test from 'node:test';
import { Readable } from 'node:stream';
import { performance } from 'node:perf_hooks';
import { createGroupRelay } from './group-relay.mjs';

function createHarness(messages, { imageResponse = { image_key: 'img_target' }, imageError = null, targetChatIds = [], failTargetChatId = null, retryFailed = false, canWrite = null, sources = null, messageListDelayMs = 0, sourceConcurrency = 3, resourceErrorKeys = [], outboundCard = false, failUpdateMessageId = null, logger = null, webhooksByChatId = null, messageList = null, initialSourceStates = null } = {}) {
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
		skipRelayMessage: async (record) => saved.set(record.sourceMessageId, { ...record, status: 'skipped_bootstrap' }),
		filterRelayMessage: async (record, reason) => saved.set(record.sourceMessageId, { ...saved.get(record.sourceMessageId), ...record, status: 'filtered_system', errorMessage: reason }),
		claimRelayMessage: async (record) => {
			const sourceMessageId = record.sourceMessageId ?? record.source_message_id;
			const existing = saved.get(sourceMessageId);
			if (existing && existing.status !== 'failed') return null;
			const claimed = { ...existing, ...record, sourceMessageId, status: 'processing', target_message_ids: existing?.targetMessageIds ?? existing?.target_message_ids ?? [] };
			saved.set(sourceMessageId, claimed); return claimed;
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
	const sourceApi = {
		messageList: async (params) => {
			listCalls.push(params);
			if (messageList) return messageList(params);
			if (messageListDelayMs) await new Promise((resolve) => setTimeout(resolve, messageListDelayMs));
			return { data: { items: messages, has_more: false } };
		},
		messageResourceGet: async ({ fileKey }) => {
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
		},
	});
	return { relay, sent, updated, patched, saved, listCalls, sourceStates };
}

test('a fenced relay observes no source messages and never sends', async () => {
	const message = { message_id: 'om_fenced', msg_type: 'text', create_time: String(Date.now()), body: { content: JSON.stringify({ text: 'must not send' }) } };
	const { relay, sent } = createHarness([message], { canWrite: async () => ({ allowed: false, writer_id: 'relay-edge-47' }) });
	await relay.tick();
	assert.equal(sent.length, 0);
	assert.equal(relay.status().writer_state, 'fenced');
	assert.match(relay.status().last_tick_error, /relay 写入权归属/);
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
		calls.push({ url, body: JSON.parse(options.body) });
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
