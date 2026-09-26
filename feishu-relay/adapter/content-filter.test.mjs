import test from 'node:test';
import assert from 'node:assert/strict';
import { blockedMessageReason, messageFilterText, normalizeFilterText } from './content-filter.mjs';

test('normalizes punctuation and full-width text for keyword matching', () => {
	assert.equal(normalizeFilterText('到期　联系！'), '到期联系');
	assert.match(blockedMessageReason({ msg_type: 'text', body: { content: JSON.stringify({ text: '服务到期，联系我领取' }) } }), /到期联系/);
});

test('filters interactive card text before media or relay work', () => {
	const message = {
		msg_type: 'interactive',
		body: { content: JSON.stringify({ elements: [{ tag: 'text', text: '扫码联系客服' }, { tag: 'img', image_key: 'img_v' }] }) },
	};
	assert.equal(messageFilterText(message), '扫码联系客服');
	assert.match(blockedMessageReason(message), /扫码|联系客服/);
});

test('filters promotional text inside LarkAgentX schema 2.0 property wrappers', () => {
	const message = {
		msg_type: 'interactive',
		body: { content: JSON.stringify({
			body: { elements: [{ tag: 'markdown', property: { elements: [{ tag: 'plain_text', property: { content: '请加微信领取资料' } }] } }] },
			newBody: { tag: 'body', property: { elements: [] } },
		}) },
	};
	assert.equal(messageFilterText(message), '请加微信领取资料');
	assert.match(blockedMessageReason(message), /加微信/);
});

test('does not filter ordinary analyst content', () => {
	assert.equal(blockedMessageReason({ msg_type: 'text', body: { content: JSON.stringify({ text: '#anqiang\n今日指数缩量震荡' }) } }), null);
});

test('keeps source-specific phrases out of the global filter list', () => {
	assert.equal(blockedMessageReason({ msg_type: 'text', body: { content: JSON.stringify({ text: '般若星登山的川柏' }) } }), null);
});

test('supports an additive configured keyword list', () => {
	assert.match(blockedMessageReason({ body: { content: JSON.stringify({ text: '测试专属词' }) } }, { keywords: '专属词' }), /专属词/);
});
