import assert from 'node:assert/strict';
import test from 'node:test';
import { buildRelayCard, cardImageKeys, cardPayload, cardText, isCardUnavailableNotice } from './card-content.mjs';

const V2 = { schema: '2.0', body: { elements: [{ tag: 'markdown', content: '长光华X回到了8月17日高点' }] } };

const LARKAGENTX_INTERNAL_V2 = {
	schema: '2.0',
	body: { elements: [{
		tag: 'column_set',
		property: { columns: [{
			tag: 'column',
			property: { elements: [
				{ tag: 'markdown', property: { elements: [{ tag: 'plain_text', property: { content: '请升级至最新版本客户端，以查看内容' } }] } },
				{ tag: 'div', property: { text: { tag: 'plain_text', property: {} } } },
			] },
		}] },
	}] },
	newBody: { tag: 'body', property: { elements: [{
		tag: 'markdown',
		property: { elements: [{ tag: 'plain_text', property: { content: '安强卡片里的真实教学内容' } }] },
	}] } },
};

test('reads a legacy 1.0 card', () => {
	const card = { title: null, elements: [[{ tag: 'text', text: '通过网盘分享的文件\n链接:' }, { tag: 'a', href: 'https://pan.baidu.com/s/x', text: 'https://pan.baidu.com/s/x' }]] };
	assert.equal(cardText(card), '通过网盘分享的文件\n链接:\nhttps://pan.baidu.com/s/x');
});

test('reads a card JSON 2.0 markdown body', () => {
	assert.equal(cardText(JSON.stringify(V2)), '长光华X回到了8月17日高点');
});

test('reads 2.0 header title, div text objects and lark_md', () => {
	const card = { schema: '2.0', header: { title: { tag: 'plain_text', content: '9.8 收盘' } }, body: { elements: [{ tag: 'div', text: { tag: 'lark_md', content: '创业板小阴调整' } }] } };
	assert.equal(cardText(card), '9.8 收盘\n创业板小阴调整');
});

test('reads LarkAgentX internal schema 2.0 property wrappers and newBody', () => {
	assert.equal(cardText(LARKAGENTX_INTERNAL_V2), '安强卡片里的真实教学内容');
	// imageID values in this representation are element ids, not CDN keys;
	// they must not cause a bogus resource download.
	const withElementImage = { ...LARKAGENTX_INTERNAL_V2, body: { elements: [{ tag: 'img', property: { imageID: '16' } }] } };
	assert.deepEqual(cardImageKeys(withElementImage), []);
});

test('keeps an image-like internal imageID when it is a real resource key', () => {
	assert.deepEqual(cardImageKeys({ body: { elements: [{ tag: 'img', property: { imageID: 'img_v3_card_asset' } }] } }), ['img_v3_card_asset']);
});

test('drops the client-upgrade banner and keeps nothing else from it', () => {
	const card = { title: null, elements: [[{ tag: 'img', image_key: 'img_dead' }, { tag: 'text', text: '请升级至最新版本客户端，以查看内容' }, { tag: 'text', text: '' }]] };
	assert.equal(cardText(card), '');
});

test('one banner matcher covers both languages with the protobuf prefix tolerance', () => {
	assert.ok(isCardUnavailableNotice('Upgrade to the latest app version to view the content'));
	assert.ok(isCardUnavailableNotice('5Upgrade to the latest app version to view the content'));
	assert.ok(isCardUnavailableNotice('请升级至最新版本客户端，以查看内容'));
	assert.ok(isCardUnavailableNotice('请升级至最新版本客户端以查看内容'));
	assert.ok(isCardUnavailableNotice('\u00125请升级至最新版本客户端，以查看内容'));
	assert.ok(!isCardUnavailableNotice('临盘拉了欧盟反制'));
	assert.ok(!isCardUnavailableNotice(''));
	// cardText must drop the English banner too, not only the Chinese one.
	const card = { title: null, elements: [[{ tag: 'text', text: 'Upgrade to the latest app version to view the content' }]] };
	assert.equal(cardText(card), '');
});

test('skips anti-scrape decoy elements hidden by large negative margins', () => {
	// Observed 2026-10-09 in the cat group (书房猫 bot, om_x100b63b37c1...):
	// the decoy line carries garbled text and margin "0px 0px -24px -99px",
	// which real clients render out of view behind the next element.
	const card = { schema: '2.0', body: { direction: 'vertical', elements: [
		{ element_id: '_2', margin: '0px 0px -24px -99px', tag: 'div',
			text: { content: 'EQr?icQ:N 临盘$拉&了~欧盟$反+制', tag: 'plain_text' }, width: 'fill' },
		{ element_id: '_4', tag: 'div',
			text: { content: 'Eric:\n临盘拉了欧盟反制', tag: 'plain_text' }, width: 'fill' },
	] } };
	assert.equal(cardText(card), 'Eric:\n临盘拉了欧盟反制');
});

test('keeps elements whose small negative margins are layout tweaks', () => {
	const card = { schema: '2.0', body: { elements: [
		{ tag: 'div', margin: '0px 0px -4px 0px', text: { content: '正常紧凑排版', tag: 'plain_text' } },
	] } };
	assert.equal(cardText(card), '正常紧凑排版');
});

test('collects image keys under either name, once each', () => {
	const card = { body: { elements: [{ tag: 'img', img_key: 'k2' }, { tag: 'img', img_key: 'k2' }, { tag: 'img', image_key: 'k1' }] } };
	assert.deepEqual(cardImageKeys(card), ['k2', 'k1']);
});

test('cardPayload exposes text and image resources the ingestion expects', () => {
	const card = { schema: '2.0', body: { elements: [{ tag: 'div', text: { tag: 'plain_text', content: '#anqiang\n正文' } }, { tag: 'img', img_key: 'k' }] } };
	assert.deepEqual(cardPayload(card), { text: '#anqiang\n正文', resources: [{ key: 'k', resource_type: 'image' }] });
});

test('the outbound card leads with the route tag so ingestion can route it', () => {
	const card = buildRelayCard({ tag: 'anqiang', text: '致尚在190左右接回。', imageKeys: ['img_a'] });
	assert.equal(card.schema, '2.0');
	assert.equal(card.body.elements[0].text.content, '#anqiang\n致尚在190左右接回。');
	assert.deepEqual(card.body.elements[1], { tag: 'img', img_key: 'img_a', alt: { tag: 'plain_text', content: '' } });
	// Round trip: what the relay sends is what the summary ingestion reads.
	assert.deepEqual(cardPayload(card), { text: '#anqiang\n致尚在190左右接回。', resources: [{ key: 'img_a', resource_type: 'image' }] });
});

test('an already-tagged text is not tagged twice', () => {
	assert.equal(buildRelayCard({ tag: 'liwei', text: '#liwei\n正文' }).body.elements[0].text.content, '#liwei\n正文');
	assert.equal(buildRelayCard({ tag: 'liwei' }).body.elements[0].text.content, '#liwei');
});

// Observed 2026-10-11 from the anqiang source: the visible body is the client
// upgrade banner and the real content is one link node under newBody.
const linkOnlyClientCard = {
	body: { elements: [{ tag: 'column_set', property: { columns: [{ tag: 'column', property: { elements: [
		{ tag: 'img', property: { alt: { tag: 'plain_text', property: {} }, imageID: '16' } },
		{ tag: 'markdown', property: { elements: [{ tag: 'plain_text', property: { content: '请升级至最新版本客户端，以查看内容' } }] } },
	] } }] } }] },
	newBody: { tag: 'body', property: { elements: [{ tag: 'markdown', property: { elements: [
		{ tag: 'link', id: '_2_0', property: { content: 'http://liveplay.example.com/live/5060.m3u8', url: { url: 'http://liveplay.example.com/live/5060.m3u8' } } },
	] } }] } },
	schema: '2.0',
};

test('a client-side 2.0 card whose only content is a link node keeps the link', () => {
	assert.equal(cardText(linkOnlyClientCard), 'http://liveplay.example.com/live/5060.m3u8');
	assert.equal(cardText(JSON.stringify(linkOnlyClientCard)), 'http://liveplay.example.com/live/5060.m3u8');
});

test('a link node with a label keeps both the label and its nested target', () => {
	const card = { newBody: { tag: 'body', property: { elements: [{ tag: 'markdown', property: { elements: [
		{ tag: 'plain_text', property: { content: '8点上课链接' } },
		{ tag: 'link', property: { content: '点开观看', url: { url: 'https://example.com/live', pcUrl: 'https://example.com/pc' } } },
	] } }] } } };
	assert.equal(cardText(card), '8点上课链接\n点开观看\nhttps://example.com/live');
});

test('card JSON anchors and buttons with object-valued urls are still read', () => {
	assert.equal(cardText({ elements: [[{ tag: 'a', text: '原文', href: 'https://example.com/a' }]] }), '原文\nhttps://example.com/a');
	assert.equal(cardText({ schema: '2.0', body: { elements: [{ tag: 'button', text: { tag: 'plain_text', content: '打开' }, multi_url: { url: 'https://example.com/b' } }] } }), '打开\nhttps://example.com/b');
});

import { beijingTimestamp, stampRelayPayload } from './card-content.mjs';
import { extractImportContent } from './message-time.mjs';

test('the forwarding timestamp is Beijing wall-clock time regardless of the host zone', () => {
	assert.equal(beijingTimestamp(Date.UTC(2026, 9, 12, 3, 45, 3)), '2026-10-12 11:45:03');
	assert.equal(beijingTimestamp(Date.UTC(2026, 9, 12, 16, 0, 0)), '2026-10-13 00:00:00');
	assert.equal(beijingTimestamp(Date.UTC(2026, 11, 31, 15, 59, 59)), '2026-12-31 23:59:59');
	assert.match(beijingTimestamp(Number.NaN), /^\d{4}-\d{2}-\d{2} \d{2}:\d{2}:\d{2}$/);
});

test('stamping rewrites only the leading tag of each outbound shape, once, without mutating its input', () => {
	const at = Date.UTC(2026, 9, 12, 3, 45, 3);
	const header = '#anqiang 2026-10-12 11:45:03';
	const text = { component: 'text', msgType: 'text', content: { text: '#anqiang\n正文\n#anqiang 不是标签' } };
	const stampedText = stampRelayPayload(text, 'anqiang', at);
	assert.equal(stampedText.content.text, `${header}\n正文\n#anqiang 不是标签`);
	assert.equal(text.content.text, '#anqiang\n正文\n#anqiang 不是标签');
	assert.equal(stampRelayPayload(stampedText, 'anqiang', at + 60_000), stampedText);
	const imageOnly = { msgType: 'post', content: { zh_cn: { title: '', content: [[{ tag: 'text', text: '#anqiang' }], [{ tag: 'img', image_key: 'img_1' }]] } } };
	assert.deepEqual(stampRelayPayload(imageOnly, 'anqiang', at).content.zh_cn.content, [[{ tag: 'text', text: header }], [{ tag: 'img', image_key: 'img_1' }]]);
	const card = { msgType: 'interactive', content: buildRelayCard({ tag: 'anqiang', text: '卡片正文', imageKeys: ['img_1'] }) };
	const stampedCard = stampRelayPayload(card, 'anqiang', at);
	assert.equal(cardText(stampedCard.content), `${header}\n卡片正文`);
	assert.deepEqual(cardImageKeys(stampedCard.content), ['img_1']);
	// a tag-only message, a different tag and a tagless payload
	assert.equal(stampRelayPayload({ msgType: 'text', content: { text: '#anqiang' } }, 'anqiang', at).content.text, header);
	const other = { msgType: 'text', content: { text: '#anqiangx\n正文' } };
	assert.equal(stampRelayPayload(other, 'anqiang', at), other);
	const file = { msgType: 'file', content: { file_key: 'file_1' } };
	assert.equal(stampRelayPayload(file, 'anqiang', at), file);
});

test('summary ingestion reads the stamped header as tag plus message time and keeps the content clean', () => {
	assert.deepEqual(extractImportContent('#anqiang 2026-10-12 11:45:03\n今天网络课改成8点'), { content: '今天网络课改成8点', content_date: '2026-10-12', content_time: '11:45' });
	assert.deepEqual(extractImportContent('#anqiang 2026-10-12 11:45:03'), { content: '', content_date: '2026-10-12', content_time: '11:45' });
});
