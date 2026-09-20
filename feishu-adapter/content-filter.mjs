// Shared first-hop filter for Feishu group traffic.  It runs before media
// downloads, relay claims, or analyst ingestion so noisy promotional messages
// cannot consume downstream quota or occupy the delivery queue.
const DEFAULT_BLOCKED_KEYWORDS = [
	'咸鱼', '到期联系', '加微信', '加V', '加v', '扫码', '返现', '优惠券',
	'推广', '广告', '私聊领取', '联系客服', '代理加盟', '般若星登山的川柏',
]; 

function parseKeywords(value) {
	const configured = String(value ?? '').split(/[,，\n]/).map((item) => item.trim()).filter(Boolean);
	return [...new Set([...DEFAULT_BLOCKED_KEYWORDS, ...configured])];
}

export function normalizeFilterText(value) {
	return String(value ?? '').normalize('NFKC').toLowerCase()
		.replace(/[\s\u3000\u200b\u200c\u200d\-_/|:：,，。.!！?？~～]+/g, '');
}

function walkText(value, chunks) {
	if (typeof value === 'string') {
		try { walkText(JSON.parse(value), chunks); return; } catch { chunks.push(value); return; }
	}
	if (Array.isArray(value)) { value.forEach((item) => walkText(item, chunks)); return; }
	if (!value || typeof value !== 'object') return;
	for (const [key, child] of Object.entries(value)) {
		if (['text', 'title', 'content', 'markdown', 'plain_text', 'alt', 'href', 'url', 'name'].includes(key)) walkText(child, chunks);
		else if (['header', 'body', 'newBody', 'elements', 'columns', 'fields', 'content_v2', 'note', 'property', 'markdownElements'].includes(key)) walkText(child, chunks);
	}
}

export function messageFilterText(message) {
	const chunks = [];
	walkText(message?.body?.content ?? message?.content ?? message?.text ?? '', chunks);
	return chunks.join('\n');
}

export function blockedMessageReason(message, { keywords = process.env.FEISHU_MESSAGE_BLOCK_KEYWORDS, skipKeywords = [] } = {}) {
	const original = messageFilterText(message);
	const normalized = normalizeFilterText(original);
	if (!normalized) return null;
	const skipped = new Set(skipKeywords.map((keyword) => normalizeFilterText(keyword)).filter(Boolean));
	for (const keyword of parseKeywords(keywords)) {
		const normalizedKeyword = normalizeFilterText(keyword);
		if (skipped.has(normalizedKeyword)) continue;
		if (normalizedKeyword && normalized.includes(normalizedKeyword)) return `命中过滤关键词：${keyword}`;
	}
	return null;
}

export const blockedMessageKeywords = () => parseKeywords(process.env.FEISHU_MESSAGE_BLOCK_KEYWORDS);
