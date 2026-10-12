import assert from 'node:assert/strict';
import test from 'node:test';
import { readFileSync } from 'node:fs';
import { hasLarkAgentXCardPayload, isDirectLarkAgentXRelayType, larkAgentXMessageType, normalizeLarkAgentXMessage, normalizeLarkAgentXRelayMessage, normalizeLarkAgentXSummaryMessage, normalizeLarkAgentXUnsupportedMessage } from './larkagentx-ingress.mjs';

test('normalizes an inbound LarkAgentX text message into the adapter event contract', () => {
	const event = normalizeLarkAgentXMessage({
		msg_id: 'om_larkx_1', chat_id: 'oc_source', from_id: 'ou_sender', chat_type: 2,
		msg_type_name: 'TEXT', content: '  #liwei\n正文  ', create_time: 1720000000,
	}, { now: () => 1730000000 });

	assert.equal(event.event_id, 'larkagentx:om_larkx_1');
	assert.equal(event.source, 'larkagentx');
	assert.equal(event.message.chat_type, 'group');
	assert.equal(event.message.message_type, 'text');
	assert.deepEqual(JSON.parse(event.message.content), { text: '#liwei\n正文' });
	assert.equal(event.sender.sender_id.open_id, 'ou_sender');
});

test('keeps Paper-KB commands on the shared adapter event path', () => {
	const event = normalizeLarkAgentXMessage({
		msg_id: 'om_larkx_paper_command', chat_id: '7660000000000000000', from_id: 'ou_sender',
		msg_type_name: 'TEXT', content: '收录 2609.30059v1', _larkagentx_command_lane: true,
	});
	assert.equal(JSON.parse(event.message.content).text, '收录 2609.30059v1');
	assert.equal(event.larkagentx_command_lane, true);

	// The handler used to call forwardToN8n directly, bypassing paper command
	// parsing. Keep this small source contract next to the ingress fixture so a
	// future WebSocket refactor cannot silently reintroduce that split path.
	const source = readFileSync(new URL('./index.mjs', import.meta.url), 'utf8');
	const start = source.indexOf('async function handleLarkAgentXInbound');
	const end = source.indexOf('async function handleLarkAgentXSummaryInbound');
	assert.ok(start >= 0 && end > start);
	const handler = source.slice(start, end);
	assert.match(handler, /dispatchFeishuMessage\(data/);
	assert.doesNotMatch(handler, /forwardToN8n\(data/);
});

test('normalizes a summary-group WebSocket POST into the n8n event contract', () => {
	const event = normalizeLarkAgentXSummaryMessage({
		msg_id: 'om_summary_ws_1', chat_id: 'oc_summary', from_id: 'ou_bot', msg_type_name: 'POST',
		content: '[富文本] #quanneng [图片]', content_data: { richText: { innerText: '#quanneng 图片' } },
		_larkagentx_images: [{ image_id: 'img_v3_summary', key_hex: 'a'.repeat(64), iv_hex: 'b'.repeat(24) }],
		create_time: 1730000000000,
	}, { now: () => 1730000001000 });
	assert.equal(event.event_id, 'larkagentx:summary:oc_summary:om_summary_ws_1');
	assert.equal(event.source, 'larkagentx-summary');
	assert.equal(event.message.message_type, 'post');
	assert.equal(event.message.chat_id, 'oc_summary');
	const content = JSON.parse(event.message.content);
	assert.equal(content.zh_cn.content.some((line) => line.some((item) => item.image_key === 'img_v3_summary')), true);
});

test('fails closed when an inbound message has no stable identity or content', () => {
	assert.throws(() => normalizeLarkAgentXMessage({ chat_id: 'oc_1', from_id: 'ou_1', content: 'x' }), /message_id/);
	assert.throws(() => normalizeLarkAgentXMessage({ msg_id: 'om_1', chat_id: 'oc_1', from_id: 'ou_1' }), /文本内容/);
});

test('keeps text and system events direct while keeping cards on realtime path', () => {
	assert.equal(larkAgentXMessageType({ msg_type_name: 'TEXT' }), 'TEXT');
	assert.equal(isDirectLarkAgentXRelayType({ msg_type_name: 'TEXT' }), true);
	assert.equal(isDirectLarkAgentXRelayType({ msg_type_name: 'TEXT', content: '看起来像正文', content_status: 'unsupported' }), false);
	assert.equal(isDirectLarkAgentXRelayType({ msg_type_name: 'TEXT', content: '[image] [图片] imageKey=img_v3_source 已加密' }), false);
	assert.equal(isDirectLarkAgentXRelayType({ msg_type_name: 'POST', content: '[富文本] [图片]' }), false);
	assert.equal(isDirectLarkAgentXRelayType({ msg_type_name: 'POST', content: '[富文本] [图片]', content_data: { richText: { imageIds: ['img_post'] } } }), false);
	assert.equal(isDirectLarkAgentXRelayType({ msg_type_name: 'POST', content: '正文 [图片]', content_data: { richText: { imageIds: ['img_post'] } } }), false);
	assert.equal(isDirectLarkAgentXRelayType({ msg_type_name: 'POST', content: '正文 [图片]', content_data: { richText: { imageIds: ['img_post'] } }, _larkagentx_images: [{ image_id: 'img_post', key_hex: 'a'.repeat(64), iv_hex: 'b'.repeat(24) }] }), true);
	assert.equal(isDirectLarkAgentXRelayType({ msg_type_name: 'POST', content: '正文 [图片]', content_data: { richText: { imageIds: ['element-1'] } }, _larkagentx_images: [{ image_id: 'img_v3_post', source_id: 'element-1', key_hex: 'a'.repeat(64), iv_hex: 'b'.repeat(24) }] }), true);
	assert.equal(isDirectLarkAgentXRelayType({ msg_type_name: 'IMAGE' }), false);
	assert.equal(isDirectLarkAgentXRelayType({ msg_type_name: 'INTERACTIVE', content: '[interactive] {"schema":"2.0","body":{}}' }), true);
	assert.equal(normalizeLarkAgentXRelayMessage({ msg_id: 'om_system', msg_type_name: 'SYSTEM', content: 'join' }).msg_type, 'system');
	assert.equal(normalizeLarkAgentXRelayMessage({ msg_id: 'om_post', msg_type_name: 'POST', content: 'cat post' }).msg_type, 'post');
	const post = normalizeLarkAgentXRelayMessage({ msg_id: 'om_post_image', msg_type_name: 'POST', content: '[图片]', _larkagentx_images: [{ image_id: 'img_post', key_hex: 'a'.repeat(64), iv_hex: 'b'.repeat(24) }] });
	assert.deepEqual(JSON.parse(post.body.content).zh_cn.content.find((line) => line[0]?.tag === 'img')?.[0], { tag: 'img', image_key: 'img_post', larkagentx_resource: { image_id: 'img_post', key_hex: 'a'.repeat(64), iv_hex: 'b'.repeat(24) } });
	assert.throws(() => normalizeLarkAgentXRelayMessage({ msg_id: 'om_image', msg_type_name: 'IMAGE' }), /需要通过官方消息接口补读/);
});

test('keeps a Baidu share link, removes the Feishu CDN resource URL, and collapses a duplicated POST summary', () => {
	const share = 'https://pan.baidu.com/s/1LOcGyinHFG_WHY9O1NdB5Q?pwd=7kr2';
	const cdn = 'https://s1-imfile.feishucdn.com/static-resource/v1/img_v3_example~?image_size=noop&format=image';
	const body = `【超级会员V2】通过百度网盘分享的文件：[26.09.1…\n\n链接:\n${share}\n\n复制这段内容打开「百度网盘APP 即可获取」`;
	const input = {
		msg_id: 'om_post_links', msg_type_name: 'POST', content: `[富文本] ${body}\n\n${body}\n${cdn}`,
		content_data: { richText: { innerText: body, elements: { dictionary: { anchor: { tag: 6, property: share } } } } },
	};
	const message = normalizeLarkAgentXRelayMessage(input);
	const content = JSON.parse(message.body.content).zh_cn.content;
	const serialized = JSON.stringify(content);
	assert.equal(content.filter((line) => line.some((item) => item.tag === 'a' && item.href === share)).length, 1);
	assert.equal(serialized.includes('s1-imfile.feishucdn.com'), false);
	assert.equal(serialized.includes(body + '\\n\\n' + body), false);
	assert.deepEqual(content.find((line) => line.some((item) => item.tag === 'a')), [{ tag: 'a', href: share, text: share }]);
});

test('recovers an external link that survives only in LarkAgentX content_data', () => {
	const share = 'https://pan.baidu.com/s/example?pwd=7kr2';
	const message = normalizeLarkAgentXRelayMessage({
		msg_id: 'om_post_link_in_data', msg_type_name: 'POST', content: '[富文本] 分享文件',
		content_data: { richText: { innerText: '分享文件', elements: { dictionary: { anchor: { tag: 6, property: share } } } } },
	});
	const content = JSON.parse(message.body.content).zh_cn.content;
	assert.deepEqual(content.at(-1), [{ tag: 'a', href: share, text: share }]);
});

test('uses an image key decoded in the websocket event before OAuth backfill', () => {
	const input = { msg_id: 'om_image_direct', msg_type_name: 'IMAGE', content_data: { imageV2: { imageKey: 'img_v2_source' } } };
	assert.equal(isDirectLarkAgentXRelayType(input), false);
	const directInput = { ...input, _larkagentx_image: { image_id: 'img_v2_source', key_hex: 'a'.repeat(64), iv_hex: 'b'.repeat(24) } };
	assert.equal(isDirectLarkAgentXRelayType(directInput), true);
	const message = normalizeLarkAgentXRelayMessage(directInput);
	assert.equal(message.msg_type, 'image');
	assert.deepEqual(JSON.parse(message.body.content), { image_key: 'img_v2_source', larkagentx_resource: directInput._larkagentx_image });
});

test('preserves direct image crypto metadata for the cookie-backed resource bridge', () => {
	const message = normalizeLarkAgentXRelayMessage({
		msg_id: 'om_image_crypto', msg_type_name: 'IMAGE',
		content_data: { imageV2: { imageKey: 'img_v2_source' } },
		_larkagentx_image: { image_id: 'img_v2_source', key_hex: 'a'.repeat(64), iv_hex: 'b'.repeat(24) },
	});
	assert.deepEqual(JSON.parse(message.body.content), {
		image_key: 'img_v2_source',
		larkagentx_resource: { image_id: 'img_v2_source', key_hex: 'a'.repeat(64), iv_hex: 'b'.repeat(24) },
	});
});

test('normalizes a complete LarkAgentX CARD jsonCard without OAuth backfill', () => {
	const card = { schema: '2.0', body: { elements: [{ tag: 'div', text: { tag: 'plain_text', content: '安强卡片正文' } }] } };
	const message = normalizeLarkAgentXRelayMessage({
		msg_id: 'om_card_direct', msg_type_name: 'CARD', content_data: { jsonCard: JSON.stringify(card) },
	});
	assert.equal(isDirectLarkAgentXRelayType({ msg_type_name: 'CARD', content_data: { jsonCard: JSON.stringify(card) } }), true);
	assert.equal(message.msg_type, 'interactive');
	assert.deepEqual(JSON.parse(message.body.content), card);

	const imageCard = normalizeLarkAgentXRelayMessage({
		msg_id: 'om_card_image_only',
		msg_type_name: 'CARD',
		content: '[卡片]',
		_larkagentx_images: [{ image_id: 'img_v3_card_only', key_hex: 'a'.repeat(64), iv_hex: 'b'.repeat(24) }],
	});
	assert.equal(imageCard.msg_type, 'post');
	const imageCardContent = JSON.parse(imageCard.body.content);
	assert.deepEqual(imageCardContent.zh_cn.content[0][0], {
		tag: 'img',
		image_key: 'img_v3_card_only',
		larkagentx_resource: { image_id: 'img_v3_card_only', key_hex: 'a'.repeat(64), iv_hex: 'b'.repeat(24) },
	});
	assert.equal(hasLarkAgentXCardPayload({ msg_type_name: 'CARD', _larkagentx_images: [{ image_id: 'img_v3_card_only' }] }), true);
});

test('accepts JSON carried by LarkAgentX openCardContent', () => {
	const card = { schema: '2.0', body: { elements: [{ tag: 'div', text: { tag: 'plain_text', content: 'open card' } }] } };
	const input = { msg_id: 'om_card_open_content', msg_type_name: 'CARD', content_data: { openCardContent: JSON.stringify(card) } };
	assert.equal(isDirectLarkAgentXRelayType(input), true);
	assert.deepEqual(JSON.parse(normalizeLarkAgentXRelayMessage(input).body.content), card);
});

test('recovers a card JSON suffix carried in the human summary', () => {
	const card = { schema: '2.0', body: { elements: [{ tag: 'div', text: { tag: 'plain_text', content: 'summary card' } }] } };
	const input = { msg_id: 'om_card_summary', msg_type_name: 'CARD', content: `[卡片] ${JSON.stringify(card)}` };
	assert.equal(hasLarkAgentXCardPayload(input), true);
	assert.equal(isDirectLarkAgentXRelayType(input), true);
	assert.deepEqual(JSON.parse(normalizeLarkAgentXRelayMessage(input).body.content), card);
});

test('decodes LarkAgentX internal richtext cards with text and encrypted images', () => {
	const input = {
		msg_id: 'om_diaoyan_richtext_card', msg_type_name: 'CARD',
		content: '[卡片] 📢 新动态 · 2026-09-26 22:48',
		content_data: {
			cardVersion: 2,
			richtext: {
				imageIds: ['5'],
				elements: { dictionary: {
					'1': { tag: 1, property: '\n\uFFFD\u0017电子布高端需求挤压普通供给' },
					'5': { tag: 2, property: 'img_v3_diaoyan' },
				} },
			},
		},
		_larkagentx_images: [{ image_id: 'img_v3_diaoyan', source_id: '5', key_hex: 'a'.repeat(64), iv_hex: 'b'.repeat(24) }],
	};
	assert.equal(isDirectLarkAgentXRelayType(input), true);
	const message = normalizeLarkAgentXRelayMessage(input);
	assert.equal(message.msg_type, 'post');
	const content = JSON.parse(message.body.content).zh_cn.content;
	assert.equal(content[0][0].text, '电子布高端需求挤压普通供给');
	assert.deepEqual(content[1][0], {
		tag: 'img', image_key: 'img_v3_diaoyan',
		larkagentx_resource: { image_id: 'img_v3_diaoyan', source_id: '5', key_hex: 'a'.repeat(64), iv_hex: 'b'.repeat(24) },
	});
});

test('an upgrade-banner card never relays its carried image, even with imageIds', () => {
	// 2026-10-09 实证推翻了旧行为（剥掉横幅文字后按纯图转发）：猫群 9/24 起的
	// 900+ 条降级卡片携带的是同一个固定 image id —— 那是横幅美术图，不是内容。
	// 升级横幅卡片必须整体视为降级，改走官方补读专线。
	const input = {
		msg_id: 'om_cat_image_only', msg_type_name: 'CARD', content: '[卡片]  ',
		content_data: {
			cardVersion: 2,
			richtext: {
				imageIds: ['1'],
				elements: { dictionary: {
					'1': { tag: 2, property: 'img_v3_cat_image' },
					'4': { tag: 1, property: '\n5Upgrade to the latest app version to view the content' },
				} },
			},
		},
		_larkagentx_images: [{ image_id: 'img_v3_cat_image', source_id: '1', key_hex: 'a'.repeat(64), iv_hex: 'b'.repeat(24) }],
	};
	assert.equal(hasLarkAgentXCardPayload(input), false);
	assert.equal(isDirectLarkAgentXRelayType(input), false);
});

test('marks incomplete LarkAgentX cards for the narrow official backfill lane', () => {
	const input = { msg_id: 'om_card_incomplete', msg_type_name: 'CARD', content: '[卡片]', content_data: { cardDesc: '仅摘要' } };
	assert.equal(hasLarkAgentXCardPayload(input), false);
	assert.equal(isDirectLarkAgentXRelayType(input), false);
	const message = normalizeLarkAgentXRelayMessage(input);
	assert.equal(message.msg_type, 'text');
	assert.deepEqual(JSON.parse(message.body.content), { text: '[卡片]' });
});

test('downgrades an unsupported WebSocket type without requiring OAuth backfill', () => {
	const message = normalizeLarkAgentXUnsupportedMessage({ msg_id: 'om_file_unsupported', msg_type_name: 'FILE', content: '[文件] fileKey=file_source' });
	assert.equal(message.msg_type, 'text');
	assert.match(JSON.parse(message.body.content).text, /^\[file\]/i);
});

test('a server-degraded card must not relay its banner image and goes to backfill', async () => {
	const { larkAgentXCardIsDegraded } = await import('./larkagentx-ingress.mjs');
	const degraded = {
		msg_id: '7694496929497074904', chat_id: '7684122107030031634', from_id: 'ou_cat',
		msg_type_name: 'CARD', create_time: 1791514672, content: '[卡片]  ',
		content_data: {
			cardVersion: 2,
			richtext: {
				elementIds: ['12'],
				elements: {
					dictionary: {
						1: { tag: 2, property: '\u00120img_v3_02ad_banner-fixed-key' },
						4: { tag: 1, property: '\n5Upgrade to the latest app version to view the content' },
						7: { tag: 1, property: '\n\u0000' },
					},
				},
			},
		},
		_larkagentx_images: [{ image_id: 'img_v3_02ad_banner-fixed-key', key_hex: 'a'.repeat(64), iv_hex: 'b'.repeat(24), source_id: '1' }],
	};
	assert.equal(larkAgentXCardIsDegraded(degraded), true);
	assert.equal(hasLarkAgentXCardPayload(degraded), false);
	assert.equal(isDirectLarkAgentXRelayType(degraded), false);
});

test('a complete rich-text card with real text keeps its image and the direct path', async () => {
	const { larkAgentXCardIsDegraded } = await import('./larkagentx-ingress.mjs');
	const complete = {
		msg_id: '7694496929497074905', chat_id: '7684122107030031634', from_id: 'ou_cat',
		msg_type_name: 'CARD', create_time: 1791514673, content: '[卡片] 盘面复盘',
		content_data: {
			richtext: {
				elements: {
					dictionary: {
						1: { tag: 2, property: '\u00120img_v3_0215v_real-content-key' },
						2: { tag: 1, property: '\n今日盘面三条主线' },
					},
				},
			},
		},
		_larkagentx_images: [{ image_id: 'img_v3_0215v_real-content-key', key_hex: 'a'.repeat(64), iv_hex: 'b'.repeat(24), source_id: '1' }],
	};
	assert.equal(larkAgentXCardIsDegraded(complete), false);
	assert.equal(hasLarkAgentXCardPayload(complete), true);
	assert.equal(isDirectLarkAgentXRelayType(complete), true);
});

import { createRouteBinder } from './larkagentx-ingress.mjs';

function fakeBridge(responses) {
	const calls = [];
	const fetchImpl = async (url, init) => {
		calls.push({ path: new URL(url).pathname, body: JSON.parse(init.body), token: init.headers['x-larkagentx-token'] });
		const next = responses.shift();
		if (next instanceof Error) throw next;
		return { ok: next.ok, statusText: next.statusText ?? '', json: async () => next.body };
	};
	return { calls, fetchImpl };
}

test('a group picked from the joined list is bound by its id, not searched by name', async () => {
	const bridge = fakeBridge([{ ok: true, body: { status: 'bound', chat_id: '7700000000000000001', chat_name: '同名群' } }, { ok: true, body: { status: 'unbound' } }]);
	const binder = createRouteBinder({ baseUrl: 'http://127.0.0.1:8090/health', token: 't', fetchImpl: bridge.fetchImpl });
	await binder.bind({ key: 'relay_x', chatName: '同名群', chatId: '', enabled: true }, { websocketChatId: '7700000000000000001' });
	assert.deepEqual(bridge.calls[0].body, { source_key: 'relay_x', chat_name: '同名群', source_chat_id: '', chat_id: '7700000000000000001' });
	// a disabled route is unbound and never sends an id to bind
	await binder.bind({ key: 'relay_x', chatName: '同名群', chatId: '', enabled: false }, { websocketChatId: '7700000000000000001' });
	assert.equal(bridge.calls[1].path, '/routes/unbind');
	assert.equal('chat_id' in bridge.calls[1].body, false);
});

test('a saved route is bound to its WebSocket chat id and the result is reported', async () => {
	const bridge = fakeBridge([{ ok: true, body: { status: 'bound', chat_id: '7694956403840011216', chat_name: '小杰夜报B' } }]);
	const binder = createRouteBinder({ baseUrl: 'http://127.0.0.1:8090/health', token: 'secret', fetchImpl: bridge.fetchImpl });
	const result = await binder.bind({ key: 'relay_x', chatName: '小杰夜报B', chatId: 'oc_demo', enabled: true });
	assert.deepEqual(result, { status: 'bound', chat_id: '7694956403840011216', chat_name: '小杰夜报B' });
	assert.equal(bridge.calls[0].path, '/routes/bind');
	assert.deepEqual(bridge.calls[0].body, { source_key: 'relay_x', chat_name: '小杰夜报B', source_chat_id: 'oc_demo' });
	assert.equal(bridge.calls[0].token, 'secret');
});

test('a disabled route is unbound instead of kept subscribed', async () => {
	const bridge = fakeBridge([{ ok: true, body: { status: 'unbound' } }]);
	const binder = createRouteBinder({ baseUrl: 'http://127.0.0.1:8090/health', token: 't', fetchImpl: bridge.fetchImpl });
	await binder.bind({ key: 'relay_x', chatName: 'A', enabled: false });
	assert.equal(bridge.calls[0].path, '/routes/unbind');
});

test('a refused or unreachable bind is reported without throwing', async () => {
	const refused = fakeBridge([{ ok: false, body: { message: '有 2 个同名群，拒绝自动绑定：A' } }]);
	const binder = createRouteBinder({ baseUrl: 'http://127.0.0.1:8090/health', token: 't', fetchImpl: refused.fetchImpl });
	const failed = await binder.bind({ key: 'k', chatName: 'A' });
	assert.equal(failed.status, 'failed');
	assert.match(failed.message, /同名群/);
	const down = fakeBridge([new Error('ECONNREFUSED')]);
	const offline = createRouteBinder({ baseUrl: 'http://127.0.0.1:8090/health', token: 't', fetchImpl: down.fetchImpl });
	assert.equal((await offline.bind({ key: 'k', chatName: 'A' })).status, 'unavailable');
	assert.equal((await createRouteBinder({ baseUrl: '', token: '' }).bind({ key: 'k', chatName: 'A' })).status, 'unavailable');
});

// Shapes observed 2026-10-11/12 from the anqiang groups.  content_data is what
// the adapter really receives: protobuf property bytes already decoded as text.
const BAIDU = 'https://pan.baidu.com/s/1-g1gEqFhrgNlFejflFZAwA?pwd=q7sx';
const STICKER = 'https://s3-imfile.feishucdn.com/static-resource/v1/v3_00160_00f38314~?image_size=noop&format=image&sticker_format=.webp';
const anchorProperty = (url, long = false) => (long
	? `\n�\u0001${url}\u0012�\u0001${url}\u001a�\u0001${url} \u0000`
	: `\n8${url}\u00128${url}\u001a8${url} \u0000`);
const textWithLink = {
	msg_id: '7695418070096939996', msg_type_name: 'TEXT', chat_id: '7667839423698029860', from_id: '7471672673018593282', create_time: 1791729142,
	content: '通过网盘分享的文件：2026.10.11 安强内部vip.mp4\n链接:  提取码: q7sx',
	content_data: { text: '', richText: { anchorIds: ['a-1'], elementIds: ['t', 'nl', 'a-0', 'a-1', 'a-2'], elements: { dictionary: {
		'a-0': { tag: 1, property: '\n\b链接: ' }, 'a-1': { tag: 6, property: anchorProperty(BAIDU) }, 'a-2': { tag: 1, property: '\n\u0010 提取码: q7sx' },
		t: { tag: 1, property: '\n<通过网盘分享的文件：2026.10.11 安强内部vip.mp4' }, nl: { tag: 1, property: '\n\u0001\n' },
	} } } },
};

test('a TEXT message keeps the link the upstream summary dropped, where it was typed', () => {
	const relay = normalizeLarkAgentXRelayMessage({
		...textWithLink,
		_larkagentx_rich_text: { text: `通过网盘分享的文件：2026.10.11 安强内部vip.mp4\n链接: ${BAIDU} 提取码: q7sx`, links: [BAIDU] },
	});
	assert.equal(relay.msg_type, 'text');
	assert.equal(JSON.parse(relay.body.content).text, `通过网盘分享的文件：2026.10.11 安强内部vip.mp4\n链接: ${BAIDU} 提取码: q7sx`);
});

test('a TEXT message without the bridge rendering still relays its summary unchanged', () => {
	const relay = normalizeLarkAgentXRelayMessage(textWithLink);
	assert.equal(JSON.parse(relay.body.content).text, '通过网盘分享的文件：2026.10.11 安强内部vip.mp4\n链接:  提取码: q7sx');
});

const postWithLinks = {
	msg_id: '7695573360809348361', msg_type_name: 'POST', chat_id: '7661209668907207659', from_id: '7679773622105296093', create_time: 1791765299,
	content: '[富文本] \n通过百度网盘分享的文件：10.11 安强…\n链接:\n提取码:w12g \n复制这段内容打开「百度网盘APP 即可获取」',
	content_data: { isNotified: false, richText: { anchorIds: ['s', 'b'], elementIds: ['p1', 'p2'], elements: { dictionary: {
		s: { tag: 6, property: anchorProperty(STICKER, true) },
		b: { tag: 6, property: `\n/https://pan.baidu.com/s/1Skefd7vwcGD5xBESLj_gJw\u0012/https://pan.baidu.com/s/1Skefd7vwcGD5xBESLj_gJw` },
		p1: { tag: 3, property: '', childIds: ['s'] }, p2: { tag: 3, property: '', childIds: ['b'] },
	} } } },
};

test('a POST places its link inline and never relays a private-CDN sticker as a link', () => {
	const relay = normalizeLarkAgentXRelayMessage({
		...postWithLinks,
		_larkagentx_rich_text: {
			text: '通过百度网盘分享的文件：10.11 安强…\n链接:https://pan.baidu.com/s/1Skefd7vwcGD5xBESLj_gJw\n提取码:w12g\n复制这段内容打开「百度网盘APP 即可获取」',
			links: ['https://pan.baidu.com/s/1Skefd7vwcGD5xBESLj_gJw'],
		},
	});
	const rows = JSON.parse(relay.body.content).zh_cn.content;
	assert.deepEqual(rows[1], [{ tag: 'text', text: '链接:' }, { tag: 'a', href: 'https://pan.baidu.com/s/1Skefd7vwcGD5xBESLj_gJw', text: 'https://pan.baidu.com/s/1Skefd7vwcGD5xBESLj_gJw' }]);
	assert.equal(rows.length, 4);
	assert.doesNotMatch(relay.body.content, /feishucdn|\\u0012|�/);
});

test('without the bridge rendering, legacy URL extraction stops at protobuf control bytes', () => {
	const relay = normalizeLarkAgentXRelayMessage(postWithLinks);
	const anchors = JSON.parse(relay.body.content).zh_cn.content.flat().filter((node) => node.tag === 'a');
	assert.deepEqual(anchors, [{ tag: 'a', href: 'https://pan.baidu.com/s/1Skefd7vwcGD5xBESLj_gJw', text: 'https://pan.baidu.com/s/1Skefd7vwcGD5xBESLj_gJw' }]);
});

test('bridge-rendered links are filtered again at the adapter boundary', () => {
	const relay = normalizeLarkAgentXRelayMessage({
		...postWithLinks,
		_larkagentx_rich_text: { text: '看这里', links: [STICKER, 'javascript:alert(1)', 'https://example.com/ok'] },
	});
	const anchors = JSON.parse(relay.body.content).zh_cn.content.flat().filter((node) => node.tag === 'a');
	assert.deepEqual(anchors.map((node) => node.href), ['https://example.com/ok']);
});

import { resolveLarkAgentXSource } from './larkagentx-ingress.mjs';

const ROUTES = [
	{ key: 'relay_old', chatName: '小杰夜报B', chatId: 'oc_new', enabled: true },
	{ key: 'relay_new', chatName: '小杰交流', chatId: 'oc_talk', enabled: true },
	{ key: 'xiaojie', chatName: '小杰夜报～', chatId: '', enabled: true },
];

test('a verified bridge binding overrides a stale static route instead of failing the message', () => {
	// Observed 2026-10-11: the env still mapped 小杰交流 to the route that had been renamed to 小杰夜报B.
	const resolved = resolveLarkAgentXSource({
		chatId: '7678195174698191820', configuredKey: 'relay_old', routes: ROUTES,
		declaredRoute: { source_key: 'relay_new', chat_name: '小杰交流', source_chat_id: 'oc_talk' },
	});
	assert.equal(resolved.sourceKey, 'relay_new');
	assert.equal(resolved.source.chatName, '小杰交流');
	assert.equal(resolved.staleConfiguredKey, 'relay_old');
});

test('an unverified declaration never overrides the configured route and still fails closed', () => {
	for (const declaredRoute of [
		{ source_key: 'relay_new', chat_name: '别的群', source_chat_id: 'oc_talk' },
		{ source_key: 'relay_new', chat_name: '小杰交流', source_chat_id: 'oc_other' },
		{ source_key: 'relay_new', chat_name: '小杰交流', source_chat_id: '' },
		{ source_key: 'relay_missing', chat_name: '小杰交流', source_chat_id: 'oc_talk' },
	]) {
		assert.throws(() => resolveLarkAgentXSource({ chatId: '1', configuredKey: 'relay_old', routes: ROUTES, declaredRoute }), /动态源群映射不匹配/);
	}
});

test('agreeing, static-only and declared-only inputs resolve as before', () => {
	const agree = resolveLarkAgentXSource({ chatId: '1', configuredKey: 'relay_new', routes: ROUTES, declaredRoute: { source_key: 'relay_new', chat_name: '小杰交流', source_chat_id: 'oc_talk' } });
	assert.deepEqual([agree.sourceKey, agree.staleConfiguredKey], ['relay_new', null]);
	assert.equal(resolveLarkAgentXSource({ chatId: '1', configuredKey: 'xiaojie', routes: ROUTES }).sourceKey, 'xiaojie');
	assert.equal(resolveLarkAgentXSource({ chatId: '1', routes: ROUTES, declaredRoute: { source_key: 'relay_new', chat_name: '小杰交流', source_chat_id: 'oc_talk' } }).sourceKey, 'relay_new');
	// a route without a resolved oc_ id accepts the bridge's declaration, as it always did
	assert.equal(resolveLarkAgentXSource({ chatId: '1', configuredKey: 'xiaojie', routes: ROUTES, declaredRoute: { source_key: 'xiaojie', chat_name: '小杰夜报～', source_chat_id: '' } }).sourceKey, 'xiaojie');
	assert.throws(() => resolveLarkAgentXSource({ chatId: '9', routes: ROUTES }), /未配置 LarkAgentX 源群/);
	assert.throws(() => resolveLarkAgentXSource({ chatId: '9', configuredKey: 'gone', routes: ROUTES }), /未找到实时源路由/);
});

test('a relayed card keeps its real image keys beside the card JSON, and a banner card keeps none', () => {
	const KEY = 'ab'.repeat(32), IV = 'cd'.repeat(12);
	const base = {
		msg_id: '7695597877444906163', msg_type_name: 'CARD', chat_id: '7684122107030031634', from_id: '7001', create_time: 1791765299,
		content: '[卡片]', _larkagentx_images: [{ image_id: 'img_v3_card_1', key_hex: KEY, iv_hex: IV, source_id: '5' }],
	};
	const card = { schema: '2.0', body: { elements: [{ tag: 'img', property: { imageID: '5' } }, { tag: 'markdown', property: { elements: [{ tag: 'plain_text', property: { content: '研报摘要' } }] } }] } };
	const withImage = normalizeLarkAgentXRelayMessage({ ...base, content_data: { jsonCard: JSON.stringify(card), richtext: { imageIds: ['5'], elements: { dictionary: { 1: { tag: 1, property: '研报摘要' }, 5: { tag: 2, property: 'x' } } } } } });
	assert.equal(withImage.msg_type, 'interactive');
	assert.deepEqual(withImage.larkagentx_images, [{ image_id: 'img_v3_card_1', key_hex: KEY, iv_hex: IV }]);
	const banner = normalizeLarkAgentXRelayMessage({ ...base, content_data: { jsonCard: JSON.stringify(card), richtext: { imageIds: ['5'], elements: { dictionary: { 1: { tag: 1, property: '请升级至最新版本客户端，以查看内容' }, 5: { tag: 2, property: 'x' } } } } } });
	assert.equal(banner.larkagentx_images, undefined);
	const noKeys = normalizeLarkAgentXRelayMessage({ ...base, _larkagentx_images: [{ image_id: 'img_v3_card_1', key_hex: '[redacted]', iv_hex: '' }], content_data: { jsonCard: JSON.stringify(card) } });
	assert.equal(noKeys.larkagentx_images, undefined);
});

import { isSummaryChat } from './larkagentx-ingress.mjs';

test('the summary lane accepts the configured oc_ id or a numeric id the bridge lists, and nothing else', () => {
	const configuredChatId = 'oc_523b7e9e29854acba64272a948cb8eda';
	const bridgeSummaryChatIds = [configuredChatId, '7669744189332065542'];
	assert.equal(isSummaryChat({ chatId: configuredChatId, configuredChatId, bridgeSummaryChatIds: [] }), true);
	assert.equal(isSummaryChat({ chatId: '7669744189332065542', configuredChatId, bridgeSummaryChatIds }), true);
	// a source group, an unknown numeric id, an unreachable bridge, a non-numeric stranger
	assert.equal(isSummaryChat({ chatId: '7661209668907207659', configuredChatId, bridgeSummaryChatIds }), false);
	assert.equal(isSummaryChat({ chatId: '7669744189332065542', configuredChatId, bridgeSummaryChatIds: [] }), false);
	assert.equal(isSummaryChat({ chatId: 'oc_other', configuredChatId, bridgeSummaryChatIds: ['oc_other'] }), false);
	assert.equal(isSummaryChat({ chatId: '', configuredChatId, bridgeSummaryChatIds }), false);
});

test('a POST keeps its title, which is not part of the rendered element tree', () => {
	const base = { msg_id: '1', msg_type_name: 'POST', chat_id: '7661209668907207659', from_id: '7001', create_time: 1791765299, content: '[富文本] 午评\n正文第一行', content_data: {} };
	const titled = normalizeLarkAgentXRelayMessage({ ...base, _larkagentx_rich_text: { text: '正文第一行\n链接 https://example.com/x', links: ['https://example.com/x'], title: '午评' } });
	assert.deepEqual(JSON.parse(titled.body.content).zh_cn.content.map((row) => row.map((node) => node.text).join('')), ['午评', '正文第一行', '链接 https://example.com/x']);
	// no duplicate when the body already opens with the title, and no change without one
	const repeated = normalizeLarkAgentXRelayMessage({ ...base, _larkagentx_rich_text: { text: '午评\n正文', links: [], title: '午评' } });
	assert.deepEqual(JSON.parse(repeated.body.content).zh_cn.content.map((row) => row[0].text), ['午评', '正文']);
	const untitled = normalizeLarkAgentXRelayMessage({ ...base, _larkagentx_rich_text: { text: '正文', links: [] } });
	assert.deepEqual(JSON.parse(untitled.body.content).zh_cn.content.map((row) => row[0].text), ['正文']);
});

test('a summary-group message read over the WebSocket is ingestible: tag, forwarding time and picture keys survive', () => {
	const KEY = 'ab'.repeat(32), IV = 'cd'.repeat(12);
	const text = normalizeLarkAgentXSummaryMessage({ msg_id: '9001', msg_type_name: 'TEXT', chat_id: '7669744189332065542', from_id: 'bot', create_time: 1791765299,
		content: '#jinli 2026-10-12 13:51:02\n核心还是看回踩', _larkagentx_rich_text: { text: '#jinli 2026-10-12 13:51:02\n核心还是看回踩', links: [] } });
	assert.equal(text.source, 'larkagentx-summary');
	assert.equal(text.message.chat_id, '7669744189332065542');
	assert.equal(JSON.parse(text.message.content).text, '#jinli 2026-10-12 13:51:02\n核心还是看回踩');
	const card = { schema: '2.0', body: { elements: [{ tag: 'div', property: { text: { tag: 'plain_text', property: { content: '#cat 2026-10-12 13:51:02\n图文卡片' } } } }, { tag: 'img', property: { imageID: '5' } }] } };
	const withPicture = normalizeLarkAgentXSummaryMessage({ msg_id: '9002', msg_type_name: 'CARD', chat_id: '7669744189332065542', from_id: 'bot', create_time: 1791765299, content: '[卡片]',
		_larkagentx_images: [{ image_id: 'img_v3_card', key_hex: KEY, iv_hex: IV, source_id: '5' }],
		content_data: { jsonCard: JSON.stringify(card), richtext: { imageIds: ['5'], elements: { dictionary: { 1: { tag: 1, property: '图文卡片' }, 5: { tag: 2, property: 'x' } } } } } });
	assert.equal(withPicture.message.message_type, 'interactive');
	assert.deepEqual(withPicture.message.larkagentx_images, [{ image_id: 'img_v3_card', key_hex: KEY, iv_hex: IV }]);
});
