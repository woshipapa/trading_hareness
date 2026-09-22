import assert from 'node:assert/strict';
import test from 'node:test';
import { matchOAuthMessage, readLarkAgentXBackfill } from './larkagentx-backfill.mjs';

const time = 1789373189000;
const input = { msg_id: '7685299326167305463', chat_id: '7684122107030031634', msg_type_name: 'POST', create_time: time / 1000, content: '[富文本] 正文内容' };
const candidate = (id = 'om_official', text = '正文内容', offset = 66) => ({ message_id: id, chat_id: 'oc_cat', create_time: String(time + offset), msg_type: 'post', body: { content: JSON.stringify({ title: '', content: [[{ tag: 'text', text }]] }) } });

test('matches different message IDs by type, source time and content', () => {
	assert.equal(matchOAuthMessage(input, [candidate()], 'oc_cat').message_id, 'om_official');
	assert.equal(matchOAuthMessage({ ...input, create_time: time }, [candidate()], 'oc_cat').message_id, 'om_official');
});
test('rejects different content, wrong chat, wrong type and missing time', () => {
	assert.equal(matchOAuthMessage(input, [candidate('om_other', '无关内容')], 'oc_cat'), null);
	assert.equal(matchOAuthMessage(input, [{ ...candidate(), chat_id: 'oc_wrong' }], 'oc_cat'), null);
	assert.equal(matchOAuthMessage(input, [{ ...candidate(), msg_type: 'image' }], 'oc_cat'), null);
	assert.throws(() => matchOAuthMessage({ ...input, create_time: null }, [], 'oc_cat'), /时间/);
	assert.equal(matchOAuthMessage(input, [candidate('om_old', '正文内容', 3000)], 'oc_cat'), null);
});
test('does not pick the nearest message when multiple candidates match', () => {
	assert.throws(() => matchOAuthMessage(input, [candidate('om_one'), candidate('om_two', '正文内容', 100)], 'oc_cat'), /多个/);
});
test('image resource keys provide evidence; placeholder alone never matches', () => {
	const image = { ...input, msg_type_name: 'IMAGE', content: '[图片]', content_data: { imageV2: { imageKey: 'img_original' } } };
	const message = { ...candidate(), msg_type: 'image', body: { content: '{"image_key":"img_original"}' } };
	assert.equal(matchOAuthMessage(image, [message], 'oc_cat').message_id, 'om_official');
	assert.equal(matchOAuthMessage({ ...image, content_data: {} }, [message], 'oc_cat'), null);
});
test('rich text imageIds provide resource evidence for placeholder POST events', () => {
	const post = { ...input, content_data: { richText: { imageIds: ['img_post'] } } };
	const message = { ...candidate(), body: { content: JSON.stringify({ title: '', content: [[{ tag: 'img', image_key: 'img_post' }]] }) } };
	assert.equal(matchOAuthMessage(post, [message], 'oc_cat').message_id, 'om_official');
});
test('reads camelCase route chatId, retries visibility delay and retains both identities', async () => {
	const calls = []; let attempt = 0;
	const sourceApi = { async messageList(params) { calls.push(params); return { code: 0, data: { items: ++attempt === 1 ? [] : [candidate()], has_more: false } }; } };
	const result = await readLarkAgentXBackfill(input, { chatId: 'oc_cat' }, { sourceApi, sleep: async () => {} });
	assert.equal(result.message_id, 'om_official');
	assert.equal(result.larkagentx_origin.message_id, input.msg_id);
	assert.equal(calls.length, 2);
	assert.ok(calls.every(p => p.container_id === 'oc_cat'));
	await assert.rejects(readLarkAgentXBackfill(input, { chatId: '' }, { sourceApi }), /官方群/);
});
test('collects all pages before matching and refuses incomplete pagination', async () => {
	const sourceApi = { async messageList(p) { return { data: { items: [candidate(p.page_token ? 'om_two' : 'om_one')], has_more: !p.page_token, page_token: p.page_token ? undefined : 'next' } }; } };
	await assert.rejects(readLarkAgentXBackfill(input, { chatId: 'oc_cat' }, { sourceApi }), /多个/);
	await assert.rejects(readLarkAgentXBackfill(input, { chatId: 'oc_cat' }, { sourceApi: { async messageList() { return { data: { items: [candidate()], has_more: true, page_token: 'next' } }; } } }), /分页/);
});
