import test from 'node:test';
import assert from 'node:assert/strict';
import { remoteTextChunkBytes, splitUtf8Text } from './media-chunks.mjs';

test('splits by UTF-8 bytes and preserves the exact text', () => {
	const input = '甲'.repeat(20) + '\n' + 'abc'.repeat(20);
	const chunks = splitUtf8Text(input, { baseKey: 'i_test000000000000000000' , maxBytes: 31 });

	assert.ok(chunks.length > 1);
	assert.equal(chunks.map((chunk) => chunk.content).join(''), input);
	for (const chunk of chunks) {
		assert.ok(Buffer.byteLength(chunk.content, 'utf8') <= 31);
		assert.match(chunk.content_sha256, /^[0-9a-f]{64}$/);
	}
	assert.deepEqual(chunks.map((chunk) => chunk.idempotency_key), [
		'i_test000000000000000000_0',
		'i_test000000000000000000_1',
		'i_test000000000000000000_2',
		'i_test000000000000000000_3',
	]);
});

test('keeps ordinary content as one chunk under the remote limit', () => {
	const content = '短消息';
	const chunks = splitUtf8Text(content, { baseKey: 'i_test000000000000000000' });
	assert.equal(chunks.length, 1);
	assert.equal(chunks[0].content, content);
	assert.ok(Buffer.byteLength(chunks[0].content, 'utf8') < remoteTextChunkBytes);
});
