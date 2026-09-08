import assert from 'node:assert/strict';
import test from 'node:test';
import { buildRelayCard, cardImageKeys, cardPayload, cardText } from './card-content.mjs';

const V2 = { schema: '2.0', body: { elements: [{ tag: 'markdown', content: '长光华X回到了8月17日高点' }] } };

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

test('drops the client-upgrade banner and keeps nothing else from it', () => {
	const card = { title: null, elements: [[{ tag: 'img', image_key: 'img_dead' }, { tag: 'text', text: '请升级至最新版本客户端，以查看内容' }, { tag: 'text', text: '' }]] };
	assert.equal(cardText(card), '');
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
