import assert from 'node:assert/strict';
import test from 'node:test';
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
