// Feishu card content, shared by every path that reads or writes one.
//
// Reading: the message API hands back a card the sender posted (with
// card_msg_content_type=user_card_content) as either the legacy 1.0 shape
// ({title, elements:[[...]]}) or card JSON 2.0 ({schema:"2.0", header, body:
// {elements:[...]}}).  One walker covers both, so the relay, the summary
// ingestion and the content filter agree on what a card says.
//
// Writing: the relay now speaks card JSON 2.0 outbound.  The route tag stays
// the first line of the card's text so the summary-group ingestion, which
// keys every message on a leading "#tag", reads a relayed card exactly as it
// read the text bubble it replaces.

// Feishu renders a card its message API cannot express as this fixed banner
// plus an image key whose resource is already deleted.  It is a client-version
// notice, never analyst content.
export const CARD_UNAVAILABLE_NOTICES = new Set([
	'请升级至最新版本客户端，以查看内容',
	'请升级至最新版本客户端以查看内容',
]);

const TEXT_TAGS = new Set(['text', 'markdown', 'plain_text', 'lark_md']);
// Keys whose children can carry text or images, in the order they render.
// Rich text wraps its blocks in a locale key ({zh_cn: {title, content}}), so
// those are walked too.  LarkAgentX's CardContent decoder exposes the
// client-side Card 2.0 representation as `{tag, property: {...}}`; `property`
// and `newBody` are therefore part of the portable walk as well.
const CHILD_KEYS = [
	'header', 'body', 'newBody', 'elements', 'columns', 'fields', 'content', 'content_v2',
	'note', 'text', 'zh_cn', 'en_us', 'ja_jp', 'property', 'markdownElements',
];

// A text value may itself be a Card 2.0 text node, and the internal serialized
// form moves its content under `property.content`.  Follow only text-bearing
// keys here; walking arbitrary card metadata would leak ids and style values
// into the relay body.
function directText(value) {
	if (typeof value === 'string') return value;
	if (!value || typeof value !== 'object') return '';
	for (const key of ['content', 'text']) {
		if (typeof value[key] === 'string') return value[key];
	}
	if (value.property && typeof value.property === 'object') {
		for (const key of ['content', 'text']) {
			if (typeof value.property[key] === 'string') return value.property[key];
		}
		const nested = directText(value.property.text);
		if (nested) return nested;
	}
	return '';
}

function parseCard(content) {
	if (content && typeof content === 'object') return content;
	try { return JSON.parse(String(content ?? '')); } catch { return { raw: String(content ?? '') }; }
}

function labelOf(value) { return directText(value); }

export function cardText(content) {
	const card = parseCard(content);
	const chunks = [];
	const append = (value) => {
		if (typeof value !== 'string') return;
		const text = value.trim();
		if (text && !CARD_UNAVAILABLE_NOTICES.has(text) && !chunks.includes(text)) chunks.push(text);
	};
	const walk = (value) => {
		if (Array.isArray(value)) { for (const item of value) walk(item); return; }
		if (!value || typeof value !== 'object') return;
		const tag = String(value.tag ?? '').toLowerCase();
		if (TEXT_TAGS.has(tag)) {
			append(directText(value));
			// Internal schema 2.0 markdown nodes keep their children in
			// property.elements, while plain_text leaves keep content there.
			walk(value.text);
			walk(value.content);
			walk(value.property);
			return;
		}
		if (tag === 'a') {
			append(labelOf(value.text ?? value.content));
			append(value.href ?? value.url ?? value.property?.href ?? value.property?.url);
			walk(value.property);
			return;
		}
		if (tag === 'button') {
			append(labelOf(value.text) || labelOf(value.content));
			append(value.url ?? value.multi_url?.url ?? value.action?.url ?? value.property?.url);
			walk(value.property);
			return;
		}
		if (tag === 'img') { append(labelOf(value.alt)); walk(value.property); return; }
		if (Object.hasOwn(value, 'title')) walk(value.title);
		// A 2.0 div carries its text as an object; walking it as a child lets
		// the text branch pick it up, and a string text is not an object, so a
		// 1.0 text element is never appended twice.
		for (const key of CHILD_KEYS) walk(value[key]);
	};
	walk(card);
	if (!chunks.length && typeof card.raw === 'string') return card.raw;
	return chunks.join('\n');
}

// Card JSON 2.0 names the image resource img_key; 1.0 and rich text use
// image_key.  The internal CardContent representation also has imageID, but
// values such as "16" are element references inside the card, not CDN media
// keys.  Only image-like values from that internal field are portable.
export function cardImageKeys(content) {
	const keys = [];
	const add = (value) => {
		const key = String(value ?? '').trim();
		if (key && !keys.includes(key)) keys.push(key);
	};
	const walk = (value) => {
		if (Array.isArray(value)) { value.forEach(walk); return; }
		if (!value || typeof value !== 'object') return;
		for (const name of ['image_key', 'img_key', 'imageKey', 'imgKey', 'image_id']) {
			if (typeof value[name] === 'string') add(value[name]);
		}
		for (const name of ['imageID', 'imageId']) {
			if (typeof value[name] === 'string' && /^img(?:_v\d+)?_/i.test(value[name].trim())) add(value[name]);
		}
		Object.values(value).forEach(walk);
	};
	walk(parseCard(content));
	return keys;
}

export function cardPayload(content) {
	return {
		text: cardText(content),
		resources: cardImageKeys(content).map((key) => ({ key, resource_type: 'image' })),
	};
}

function taggedText(tag, text = '') {
	const prefix = `#${tag}`;
	const normalized = String(text ?? '');
	return normalized === prefix || normalized.startsWith(`${prefix}\n`) ? normalized : `${prefix}${normalized ? `\n${normalized}` : ''}`;
}

// The outbound card.  Body text goes in a plain_text div rather than markdown
// so an analyst's asterisks and underscores arrive as typed, and the tag leads
// the text so cardText() yields "#tag\n..." for the ingestion side.
export function buildRelayCard({ tag, text = '', imageKeys = [] }) {
	const elements = [{ tag: 'div', text: { tag: 'plain_text', content: taggedText(tag, text) } }];
	for (const key of imageKeys) elements.push({ tag: 'img', img_key: key, alt: { tag: 'plain_text', content: '' } });
	return {
		schema: '2.0',
		config: { wide_screen_mode: true, enable_forward_interaction: false },
		body: { direction: 'vertical', elements },
	};
}
