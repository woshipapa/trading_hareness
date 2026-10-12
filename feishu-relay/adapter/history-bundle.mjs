// A source group's messages over a date range, packed for a reader that was
// not in the group: a chronological transcript, one JSON line per message and
// the image files themselves, with every image referenced by a relative path.
//
// The input is relay ledger rows (the source message as it was relayed, with
// the image crypto metadata the bridge archive redacts).  Everything here is
// pure or takes its I/O as arguments, and the zip writer is hand-rolled: an
// npm dependency would turn a source hot update into an image release.
import { crc32, deflateRawSync } from 'node:zlib';
import { cardText } from './card-content.mjs';

const BEIJING_OFFSET_MS = 8 * 3_600_000;
const DAY_MS = 86_400_000;
export const MAX_BUNDLE_DAYS = 366;
export const DEFAULT_BUNDLE_LIMITS = { maxImages: 3_000, maxImageBytes: 20 * 1024 * 1024, maxTotalBytes: 1_500 * 1024 * 1024 };

const pad = (value, width = 2) => String(value).padStart(width, '0');

// Beijing wall-clock fields of an instant, read off a shifted UTC date.
function beijingParts(ms) {
	const shifted = new Date(Number(ms) + BEIJING_OFFSET_MS);
	return {
		year: shifted.getUTCFullYear(), month: shifted.getUTCMonth() + 1, day: shifted.getUTCDate(),
		hour: shifted.getUTCHours(), minute: shifted.getUTCMinutes(), second: shifted.getUTCSeconds(),
	};
}

export function beijingDate(ms) { const p = beijingParts(ms); return `${p.year}-${pad(p.month)}-${pad(p.day)}`; }
export function beijingClock(ms) { const p = beijingParts(ms); return `${pad(p.hour)}:${pad(p.minute)}:${pad(p.second)}`; }

/** Inclusive Beijing-day range: from 00:00:00.000 of `from` to 23:59:59.999 of `to`. */
export function bundleRange(from, to) {
	const parse = (value, label) => {
		const match = /^(\d{4})-(\d{2})-(\d{2})$/.exec(String(value ?? '').trim());
		if (!match) throw new Error(`${label}必须是 YYYY-MM-DD`);
		const [year, month, day] = match.slice(1).map(Number);
		const utcMidnight = Date.UTC(year, month - 1, day);
		const check = new Date(utcMidnight);
		if (check.getUTCFullYear() !== year || check.getUTCMonth() !== month - 1 || check.getUTCDate() !== day) throw new Error(`${label}不是有效日期`);
		return utcMidnight - BEIJING_OFFSET_MS;
	};
	const fromMs = parse(from, '开始日期');
	const lastDayStart = parse(to, '结束日期');
	if (lastDayStart < fromMs) throw new Error('结束日期不能早于开始日期');
	if ((lastDayStart - fromMs) / DAY_MS + 1 > MAX_BUNDLE_DAYS) throw new Error(`时间范围最多 ${MAX_BUNDLE_DAYS} 天`);
	return { from: String(from).trim(), to: String(to).trim(), fromMs, toMs: lastDayStart + DAY_MS - 1 };
}

/** "<group name>_<from>_<to>", safe as a file and folder name on every OS. */
export function bundleBaseName(chatName, from, to) {
	const name = String(chatName ?? '').replace(/[\u0000-\u001f\u007f<>:"/\\|?*]/gu, '_').replace(/\s+/gu, ' ').replace(/^[. ]+|[. ]+$/gu, '').slice(0, 60) || '群消息';
	return `${name}_${from}_${to}`;
}

function parseJson(value) {
	if (value && typeof value === 'object') return value;
	try { return JSON.parse(String(value ?? '')); } catch { return {}; }
}

function imageRef(node) {
	const key = String(node?.image_key ?? node?.img_key ?? '').trim();
	if (!key) return null;
	const larkagentx = node.larkagentx_resource && typeof node.larkagentx_resource === 'object' ? node.larkagentx_resource : null;
	return { key, larkagentx };
}

function anchorText(node) {
	const label = String(node.text ?? '').trim();
	const href = String(node.href ?? '').trim();
	if (!href) return label;
	return label && label !== href && !label.includes(href) ? `${label} ${href}` : (label || href);
}

// Rich text is rows of inline nodes.  An image closes the line it sits in and
// becomes its own block, so the transcript shows it where it was posted.
function postBlocks(body) {
	const post = Array.isArray(body?.content) ? body : (body?.zh_cn ?? body?.en_us ?? body?.ja_jp ?? Object.values(body ?? {}).find((value) => Array.isArray(value?.content)) ?? {});
	const blocks = [];
	const lines = [];
	const flush = () => {
		const text = lines.join('\n').replace(/\n{3,}/gu, '\n\n').replace(/^\n+|\n+$/gu, '');
		if (text) blocks.push({ kind: 'text', text });
		lines.length = 0;
	};
	if (String(post.title ?? '').trim()) lines.push(String(post.title).trim());
	for (const row of Array.isArray(post.content) ? post.content : []) {
		let line = '';
		for (const node of Array.isArray(row) ? row : []) {
			const tag = String(node?.tag ?? '').toLowerCase();
			if (tag === 'img') {
				const ref = imageRef(node);
				if (!ref) continue;
				if (line) lines.push(line);
				line = '';
				flush();
				blocks.push({ kind: 'image', ref });
			} else if (tag === 'a') line += anchorText(node);
			else if (tag === 'at') line += `@${String(node.user_name ?? node.user_id ?? '').trim()}`;
			else if (tag === 'media') line += '[视频]';
			else if (tag === 'emotion') line += `[${String(node.emoji_type ?? '表情')}]`;
			else if (typeof node?.text === 'string') line += node.text;
		}
		lines.push(line);
	}
	flush();
	return blocks;
}

function senderOf(message) {
	const sender = message?.sender ?? {};
	const name = String(sender.sender_i18n_names?.zh_cn ?? sender.sender_name ?? sender.name ?? '').trim();
	const id = String(sender.sender_id ?? sender.id ?? '').trim();
	return { sender_id: id, sender_name: name };
}

/** One ledger row as an ordered list of text and image blocks. */
export function bundleEntry(row) {
	const message = row.message ?? {};
	const type = String(message.msg_type ?? '').toLowerCase() || 'unknown';
	const body = parseJson(message.body?.content);
	let blocks = [];
	if (type === 'text') blocks = [{ kind: 'text', text: String(body.text ?? '').trim() }];
	else if (type === 'post') blocks = postBlocks(body);
	else if (type === 'interactive') blocks = [{ kind: 'text', text: cardText(message.body?.content).trim() }];
	else if (type === 'image') { const ref = imageRef(body); blocks = ref ? [{ kind: 'image', ref }] : []; }
	else blocks = [{ kind: 'text', text: `[${type}]` }];
	return {
		message_id: String(row.source_message_id ?? message.message_id ?? ''),
		source_chat_id: String(row.source_chat_id ?? ''),
		time_ms: Number(row.source_create_time),
		...senderOf(message),
		type,
		blocks: blocks.filter((block) => block.kind === 'image' || block.text),
	};
}

export function entryImageCount(entry) { return entry.blocks.filter((block) => block.kind === 'image').length; }

export function sniffImageExtension(bytes) {
	if (bytes.length >= 3 && bytes[0] === 0xff && bytes[1] === 0xd8 && bytes[2] === 0xff) return 'jpg';
	if (bytes.length >= 8 && bytes.subarray(1, 4).toString('latin1') === 'PNG') return 'png';
	if (bytes.length >= 6 && bytes.subarray(0, 3).toString('latin1') === 'GIF') return 'gif';
	if (bytes.length >= 12 && bytes.subarray(0, 4).toString('latin1') === 'RIFF' && bytes.subarray(8, 12).toString('latin1') === 'WEBP') return 'webp';
	if (bytes.length >= 2 && bytes[0] === 0x42 && bytes[1] === 0x4d) return 'bmp';
	return null;
}

const entryText = (entry) => entry.blocks.filter((block) => block.kind === 'text').map((block) => block.text).join('\n');
const senderLabel = (entry) => entry.sender_name || entry.sender_id || '未知发送者';

export function renderTranscript({ title, from, to, entries, failedImages = 0 }) {
	const images = entries.reduce((total, entry) => total + entryImageCount(entry), 0);
	const named = entries.some((entry) => entry.sender_name);
	const lines = [
		`# ${title}`, '',
		`- 时间范围：${from} 至 ${to}（北京时间，含首尾两天）`,
		`- 消息 ${entries.length} 条，图片 ${images} 张${failedImages ? `（其中 ${failedImages} 张未能导出，见 export-errors.txt）` : ''}`,
		'- 每条消息的格式：`[时:分:秒] 发送者`，下一行起是正文；图片以相对路径引用，文件在 images/ 目录',
		`- 发送者${named ? '有名字时显示名字，否则' : ''}显示为平台 ID；同一个 ID 始终是同一个人`,
		'- 结构化数据见 messages.jsonl（每行一条消息，字段含 time、sender_id、type、text、images）',
	];
	let day = null;
	for (const entry of entries) {
		const entryDay = beijingDate(entry.time_ms);
		if (entryDay !== day) { lines.push('', `## ${entryDay}`); day = entryDay; }
		lines.push('', `[${beijingClock(entry.time_ms)}] ${senderLabel(entry)}`);
		for (const block of entry.blocks) {
			if (block.kind === 'text') lines.push(block.text);
			else if (block.path) lines.push(`![图片](${block.path})`);
			else lines.push(`[图片未能导出：${block.error ?? '未知原因'}]`);
		}
	}
	return `${lines.join('\n')}\n`;
}

export function renderJsonl(entries) {
	return entries.map((entry, index) => {
		const p = beijingParts(entry.time_ms);
		const images = entry.blocks.filter((block) => block.kind === 'image');
		return JSON.stringify({
			seq: index + 1, message_id: entry.message_id,
			time: `${p.year}-${pad(p.month)}-${pad(p.day)}T${pad(p.hour)}:${pad(p.minute)}:${pad(p.second)}+08:00`,
			sender_id: entry.sender_id, sender_name: entry.sender_name || null, type: entry.type,
			text: entryText(entry),
			images: images.filter((block) => block.path).map((block) => block.path),
			...(images.some((block) => !block.path) ? { missing_images: images.filter((block) => !block.path).length } : {}),
		});
	}).join('\n') + (entries.length ? '\n' : '');
}

function dosDateTime(ms) {
	const p = beijingParts(ms);
	const year = Math.min(Math.max(p.year, 1980), 2107);
	return { time: (p.hour << 11) | (p.minute << 5) | (p.second >> 1), date: ((year - 1980) << 9) | (p.month << 5) | p.day };
}

/** A streaming zip writer: each file is sent as soon as it is added.
 * `write(buffer)` must resolve once the chunk is accepted (backpressure). */
export function createZipWriter(write, { now = Date.now } = {}) {
	const entries = [];
	let offset = 0;
	let finished = false;
	const emit = async (buffer) => { offset += buffer.length; await write(buffer); };
	return {
		get bytesWritten() { return offset; },
		async addFile(name, data, { compress = false } = {}) {
			if (finished) throw new Error('zip 已结束');
			const nameBytes = Buffer.from(String(name).replace(/\\/gu, '/'), 'utf8');
			const raw = Buffer.isBuffer(data) ? data : Buffer.from(String(data ?? ''), 'utf8');
			const body = compress ? deflateRawSync(raw) : raw;
			// No zip64 here: stay inside the classic format's limits.
			if (entries.length >= 65_534 || offset + body.length + nameBytes.length + 30 > 0xffff_0000) throw new Error('资料包超过 zip 的 4GB / 65535 个文件上限，请缩小时间范围');
			const { time, date } = dosDateTime(now());
			const entry = { nameBytes, method: compress ? 8 : 0, time, date, crc: crc32(raw) >>> 0, compressed: body.length, size: raw.length, offset };
			const header = Buffer.alloc(30);
			header.writeUInt32LE(0x04034b50, 0); header.writeUInt16LE(20, 4); header.writeUInt16LE(0x0800, 6); header.writeUInt16LE(entry.method, 8);
			header.writeUInt16LE(time, 10); header.writeUInt16LE(date, 12); header.writeUInt32LE(entry.crc, 14);
			header.writeUInt32LE(entry.compressed, 18); header.writeUInt32LE(entry.size, 22); header.writeUInt16LE(nameBytes.length, 26); header.writeUInt16LE(0, 28);
			entries.push(entry);
			await emit(Buffer.concat([header, nameBytes]));
			await emit(body);
		},
		async finish() {
			if (finished) return;
			finished = true;
			const start = offset;
			const records = entries.map((entry) => {
				const record = Buffer.alloc(46);
				// "Made by" Unix (3), not DOS: Info-ZIP on macOS and Linux recodes a
				// DOS-made entry's name from an OEM code page and mangles UTF-8.
				record.writeUInt32LE(0x02014b50, 0); record.writeUInt16LE((3 << 8) | 20, 4); record.writeUInt16LE(20, 6); record.writeUInt16LE(0x0800, 8); record.writeUInt16LE(entry.method, 10);
				record.writeUInt16LE(entry.time, 12); record.writeUInt16LE(entry.date, 14); record.writeUInt32LE(entry.crc, 16);
				record.writeUInt32LE(entry.compressed, 20); record.writeUInt32LE(entry.size, 24); record.writeUInt16LE(entry.nameBytes.length, 28);
				record.writeUInt32LE((0o100644 << 16) >>> 0, 38); // regular file, rw-r--r--
				record.writeUInt32LE(entry.offset, 42);
				return Buffer.concat([record, entry.nameBytes]);
			});
			const directory = Buffer.concat(records);
			const end = Buffer.alloc(22);
			end.writeUInt32LE(0x06054b50, 0); end.writeUInt16LE(entries.length, 8); end.writeUInt16LE(entries.length, 10);
			end.writeUInt32LE(directory.length, 12); end.writeUInt32LE(start, 16);
			await emit(Buffer.concat([directory, end]));
		},
	};
}

/** Pack `rows` into a zip, fetching images through `fetchImage(ref, row)`.
 *
 * A failed image never fails the export: the transcript says so in place and
 * export-errors.txt lists every one, because by the time an image fails the
 * response is already streaming. */
export async function writeHistoryBundle({ title, from, to, rows, fetchImage, write, concurrency = 4, limits = DEFAULT_BUNDLE_LIMITS, now = Date.now }) {
	const base = bundleBaseName(title, from, to);
	const entries = rows.map(bundleEntry).filter((entry) => entry.blocks.length).sort((a, b) => a.time_ms - b.time_ms);
	const zip = createZipWriter(write, { now });
	const errors = [];
	const jobs = [];
	const rowsById = new Map(rows.map((row) => [String(row.source_message_id ?? row.message?.message_id ?? ''), row]));
	entries.forEach((entry, index) => {
		let position = 0;
		for (const block of entry.blocks) if (block.kind === 'image') jobs.push({ entry, block, row: rowsById.get(entry.message_id) ?? null, seq: index + 1, position: (position += 1) });
	});
	let exported = 0;
	for (let start = 0; start < jobs.length; start += concurrency) {
		const batch = jobs.slice(start, start + concurrency);
		const results = await Promise.allSettled(batch.map(async (job, offset) => {
			if (start + offset >= limits.maxImages) throw new Error(`超过单次导出 ${limits.maxImages} 张图片的上限`);
			const bytes = await fetchImage(job.block.ref, job.row);
			if (!Buffer.isBuffer(bytes) || !bytes.length) throw new Error('图片内容为空');
			if (bytes.length > limits.maxImageBytes) throw new Error(`图片超过 ${Math.floor(limits.maxImageBytes / 1024 / 1024)} MiB`);
			return bytes;
		}));
		for (const [offset, result] of results.entries()) {
			const job = batch[offset];
			const where = `${beijingDate(job.entry.time_ms)} ${beijingClock(job.entry.time_ms)} 消息 ${job.entry.message_id} 图片 ${job.position}`;
			if (result.status === 'rejected') {
				job.block.error = String(result.reason?.message ?? result.reason ?? '未知错误').slice(0, 160);
				errors.push(`${where}：${job.block.error}`);
				continue;
			}
			if (zip.bytesWritten + result.value.length > limits.maxTotalBytes) {
				job.block.error = '资料包超过体积上限，请缩小时间范围';
				errors.push(`${where}：${job.block.error}`);
				continue;
			}
			const extension = sniffImageExtension(result.value) ?? 'bin';
			const path = `images/${beijingDate(job.entry.time_ms).replaceAll('-', '')}-${beijingClock(job.entry.time_ms).replaceAll(':', '')}_${pad(job.seq, 4)}_${job.position}.${extension}`;
			await zip.addFile(`${base}/${path}`, result.value);
			job.block.path = path;
			exported += 1;
		}
	}
	await zip.addFile(`${base}/transcript.md`, renderTranscript({ title, from, to, entries, failedImages: errors.length }), { compress: true });
	await zip.addFile(`${base}/messages.jsonl`, renderJsonl(entries), { compress: true });
	if (errors.length) await zip.addFile(`${base}/export-errors.txt`, `${errors.join('\n')}\n`, { compress: true });
	await zip.finish();
	return { base, messages: entries.length, images: exported, failed_images: errors.length, bytes: zip.bytesWritten };
}

/** Every chat id under which the ledger may hold this route's group.
 *
 * A group has an oc_ id and a numeric WebSocket id, and older rows may carry a
 * previous id.  The route key alone cannot be trusted as the group's identity:
 * renaming a route in the dashboard leaves the old group's rows under the same
 * key (2026-10-10, 小杰交流 → 小杰夜报B).  So: the ids known to belong to this
 * route, plus ids seen under its key that no other route claims. */
export function resolveRouteChatIds({ route, routes = [], numericIdsByKey = new Map(), seenUnderKey = [] }) {
	const known = (item) => new Set([String(item.chatId ?? '').trim(), ...(numericIdsByKey.get(item.key) ?? [])].filter(Boolean));
	const own = known(route);
	const claimedByOthers = new Set(routes.filter((item) => item.key !== route.key).flatMap((item) => [...known(item)]));
	for (const chatId of seenUnderKey) {
		const id = String(chatId ?? '').trim();
		if (id && !claimedByOthers.has(id)) own.add(id);
	}
	return [...own].sort();
}
