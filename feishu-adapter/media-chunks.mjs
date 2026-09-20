import { createHash } from 'node:crypto';

// The archive API accepts at most 1 MiB of UTF-8 text per item. Leave room for
// JSON framing and keep each chunk well below that hard limit.
export const remoteTextChunkBytes = 900 * 1024;

export function splitUtf8Text(value, { baseKey, maxBytes = remoteTextChunkBytes } = {}) {
	const text = String(value ?? '');
	if (!text) return [];
	if (!Number.isSafeInteger(maxBytes) || maxBytes < 1) throw new Error('文本分片上限必须为正整数');
	const key = String(baseKey ?? '').trim();
	if (!key) throw new Error('文本分片缺少幂等键');

	const chunks = [];
	let current = '';
	let currentBytes = 0;
	for (const character of text) {
		const characterBytes = Buffer.byteLength(character, 'utf8');
		if (characterBytes > maxBytes) throw new Error('单个 UTF-8 字符超过文本分片上限');
		if (current && currentBytes + characterBytes > maxBytes) {
			chunks.push(current);
			current = '';
			currentBytes = 0;
		}
		current += character;
		currentBytes += characterBytes;
	}
	if (current) chunks.push(current);

	return chunks.map((content, index) => ({
		idempotency_key: `${key}_${index}`,
		content,
		content_sha256: createHash('sha256').update(content, 'utf8').digest('hex'),
	}));
}
