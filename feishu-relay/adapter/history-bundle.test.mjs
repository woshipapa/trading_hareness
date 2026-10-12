import assert from 'node:assert/strict';
import test from 'node:test';
import { crc32, inflateRawSync } from 'node:zlib';
import {
	beijingClock, beijingDate, bundleBaseName, bundleEntries, bundleEntry, bundleRange, cardNeedsImageLookup, createZipWriter, renderJsonl, renderTranscript, unnamedSendersByChat,
	resolveRouteChatIds, sniffImageExtension, writeHistoryBundle,
} from './history-bundle.mjs';

// Reads back what the writer produced, the way an unzip tool would: from the
// end-of-central-directory record, never from our own bookkeeping.
function readZip(buffer) {
	const end = buffer.lastIndexOf(Buffer.from([0x50, 0x4b, 0x05, 0x06]));
	assert.ok(end >= 0, 'end of central directory present');
	const count = buffer.readUInt16LE(end + 10);
	let cursor = buffer.readUInt32LE(end + 16);
	assert.equal(cursor + buffer.readUInt32LE(end + 12), end, 'central directory ends where the trailer starts');
	const files = new Map();
	for (let index = 0; index < count; index += 1) {
		assert.equal(buffer.readUInt32LE(cursor), 0x02014b50);
		assert.equal(buffer.readUInt16LE(cursor + 4) >> 8, 3, 'made by Unix, so unzip keeps UTF-8 names');
		assert.equal(buffer.readUInt32LE(cursor + 38) >>> 16, 0o100644, 'regular-file mode');
		const flags = buffer.readUInt16LE(cursor + 8), method = buffer.readUInt16LE(cursor + 10), crc = buffer.readUInt32LE(cursor + 16);
		const compressed = buffer.readUInt32LE(cursor + 20), size = buffer.readUInt32LE(cursor + 24), nameLength = buffer.readUInt16LE(cursor + 28);
		const local = buffer.readUInt32LE(cursor + 42);
		const name = buffer.subarray(cursor + 46, cursor + 46 + nameLength).toString('utf8');
		assert.equal(buffer.readUInt32LE(local), 0x04034b50);
		assert.equal(flags & 0x0800, 0x0800, 'UTF-8 name flag');
		const dataStart = local + 30 + buffer.readUInt16LE(local + 26) + buffer.readUInt16LE(local + 28);
		const stored = buffer.subarray(dataStart, dataStart + compressed);
		const data = method === 8 ? inflateRawSync(stored) : Buffer.from(stored);
		assert.equal(data.length, size);
		assert.equal(crc32(data) >>> 0, crc, `crc of ${name}`);
		files.set(name, data);
		cursor += 46 + nameLength + buffer.readUInt16LE(cursor + 30) + buffer.readUInt16LE(cursor + 32);
	}
	return files;
}

const collector = () => { const chunks = []; return { write: async (chunk) => { chunks.push(chunk); }, buffer: () => Buffer.concat(chunks) }; };
const JPEG = Buffer.from([0xff, 0xd8, 0xff, 0xe0, 1, 2, 3, 4]);
const PNG = Buffer.concat([Buffer.from([0x89]), Buffer.from('PNG\r\n\x1a\n', 'latin1'), Buffer.from([9, 9])]);
// 2026-10-10 16:54:03 Beijing
const T0 = Date.UTC(2026, 9, 10, 8, 54, 3);

test('a date range is inclusive Beijing days and rejects malformed or reversed input', () => {
	const range = bundleRange('2026-10-10', '2026-10-12');
	assert.equal(beijingDate(range.fromMs), '2026-10-10');
	assert.equal(beijingClock(range.fromMs), '00:00:00');
	assert.equal(beijingDate(range.toMs), '2026-10-12');
	assert.equal(beijingClock(range.toMs), '23:59:59');
	assert.equal(range.toMs - range.fromMs, 3 * 86_400_000 - 1);
	assert.throws(() => bundleRange('2026-10-12', '2026-10-10'), /不能早于/);
	assert.throws(() => bundleRange('2026-02-30', '2026-03-01'), /不是有效日期/);
	assert.throws(() => bundleRange('10-12', '2026-10-12'), /YYYY-MM-DD/);
	assert.throws(() => bundleRange('2025-01-01', '2026-10-12'), /最多/);
});

test('the bundle name is the group name plus the range, safe on every filesystem', () => {
	assert.equal(bundleBaseName('小杰夜报B', '2026-10-10', '2026-10-12'), '小杰夜报B_2026-10-10_2026-10-12');
	assert.equal(bundleBaseName('立伟08，严禁偷盗群信息', '2026-10-10', '2026-10-12'), '立伟08，严禁偷盗群信息_2026-10-10_2026-10-12');
	assert.equal(bundleBaseName(' a/b\\c:d*e?"f<g>h|. ', '2026-10-10', '2026-10-10'), 'a_b_c_d_e__f_g_h__2026-10-10_2026-10-10');
	assert.equal(bundleBaseName('', '2026-10-10', '2026-10-10'), '群消息_2026-10-10_2026-10-10');
});

test('entries keep text, links and images in the order they were posted', () => {
	const text = bundleEntry({ source_message_id: 'm1', source_create_time: T0, message: { msg_type: 'text', sender: { sender_id: '7681' }, body: { content: JSON.stringify({ text: '链接: https://pan.example.com/s/1?pwd=q7sx 提取码: q7sx' }) } } });
	assert.deepEqual(text.blocks, [{ kind: 'text', text: '链接: https://pan.example.com/s/1?pwd=q7sx 提取码: q7sx' }]);
	assert.equal(text.sender_id, '7681');
	const post = bundleEntry({ source_message_id: 'm2', source_create_time: T0, message: { msg_type: 'post', sender: { id: 'ou_1', sender_name: '用户408129', sender_i18n_names: { zh_cn: '大力' } }, body: { content: JSON.stringify({ title: '午评', content: [
		[{ tag: 'text', text: '看这张图：' }],
		[{ tag: 'img', image_key: 'img_a', larkagentx_resource: { image_id: 'img_a', key_hex: 'k', iv_hex: 'i' } }],
		[{ tag: 'text', text: '原文 ' }, { tag: 'a', text: '研报', href: 'https://example.com/r' }, { tag: 'at', user_name: '张三' }],
	] }) } } });
	assert.equal(post.sender_name, '大力');
	assert.deepEqual(post.blocks.map((block) => block.kind === 'image' ? `image:${block.ref.key}` : block.text), ['午评\n看这张图：', 'image:img_a', '原文 研报 https://example.com/r@张三']);
	assert.deepEqual(post.blocks[1].ref.larkagentx, { image_id: 'img_a', key_hex: 'k', iv_hex: 'i' });
	const image = bundleEntry({ source_message_id: 'm3', source_create_time: T0, message: { msg_type: 'image', body: { content: JSON.stringify({ image_key: 'img_v3_x' }) } } });
	assert.deepEqual(image.blocks, [{ kind: 'image', ref: { key: 'img_v3_x', larkagentx: null } }]);
	const card = bundleEntry({ source_message_id: 'm4', source_create_time: T0, message: { msg_type: 'interactive', body: { content: JSON.stringify({ schema: '2.0', body: { elements: [{ tag: 'markdown', content: '卡片正文' }] } }) } } });
	assert.deepEqual(card.blocks, [{ kind: 'text', text: '卡片正文' }]);
});

test('image type is read from the bytes, not trusted from a name', () => {
	assert.equal(sniffImageExtension(JPEG), 'jpg');
	assert.equal(sniffImageExtension(PNG), 'png');
	assert.equal(sniffImageExtension(Buffer.from('GIF89a..')), 'gif');
	assert.equal(sniffImageExtension(Buffer.concat([Buffer.from('RIFF'), Buffer.alloc(4), Buffer.from('WEBP')])), 'webp');
	assert.equal(sniffImageExtension(Buffer.from('not an image')), null);
});

test('the zip writer produces an archive a standard reader accepts, with UTF-8 names', async () => {
	const out = collector();
	const zip = createZipWriter(out.write, { now: () => T0 });
	await zip.addFile('小杰夜报B_2026-10-10_2026-10-10/images/a.jpg', JPEG);
	await zip.addFile('小杰夜报B_2026-10-10_2026-10-10/transcript.md', '# 标题\n'.repeat(200), { compress: true });
	await zip.addFile('小杰夜报B_2026-10-10_2026-10-10/empty.txt', '');
	await zip.finish();
	const files = readZip(out.buffer());
	assert.deepEqual([...files.keys()], ['小杰夜报B_2026-10-10_2026-10-10/images/a.jpg', '小杰夜报B_2026-10-10_2026-10-10/transcript.md', '小杰夜报B_2026-10-10_2026-10-10/empty.txt']);
	assert.deepEqual(files.get('小杰夜报B_2026-10-10_2026-10-10/images/a.jpg'), JPEG);
	assert.equal(files.get('小杰夜报B_2026-10-10_2026-10-10/transcript.md').toString('utf8'), '# 标题\n'.repeat(200));
	assert.equal(zip.bytesWritten, out.buffer().length);
	await assert.rejects(() => zip.addFile('late.txt', 'x'), /已结束/);
});

function sampleRows() {
	return [
		{ source_message_id: 'm2', source_chat_id: '769', source_create_time: T0 + 61_000, message: { msg_type: 'image', sender: { sender_id: '7681' }, body: { content: JSON.stringify({ image_key: 'img_b', larkagentx_resource: { image_id: 'img_b' } }) } } },
		{ source_message_id: 'm1', source_chat_id: '769', source_create_time: T0, message: { msg_type: 'post', sender: { sender_id: '7681' }, body: { content: JSON.stringify({ zh_cn: { title: '', content: [[{ tag: 'text', text: '今天的图' }], [{ tag: 'img', image_key: 'img_a' }], [{ tag: 'img', image_key: 'img_gone' }]] } }) } } },
		{ source_message_id: 'm3', source_chat_id: '769', source_create_time: T0 + 86_400_000, message: { msg_type: 'text', sender: { sender_id: '7999' }, body: { content: JSON.stringify({ text: '第二天' }) } } },
		{ source_message_id: 'm4', source_chat_id: '769', source_create_time: T0 + 5, message: { msg_type: 'text', sender: { sender_id: '7999' }, body: { content: JSON.stringify({ text: '' }) } } },
	];
}

test('a bundle holds the transcript, the structured lines and every image under one folder, in time order', async () => {
	const out = collector();
	const asked = [];
	const summary = await writeHistoryBundle({
		title: '小杰夜报B', from: '2026-10-10', to: '2026-10-11', rows: sampleRows(), write: out.write, now: () => T0,
		fetchImage: async (ref, row) => {
			asked.push([ref.key, row.source_message_id]);
			if (ref.key === 'img_gone') throw new Error('LarkAgentX 图片资源 HTTP 404');
			return ref.key === 'img_a' ? JPEG : PNG;
		},
	});
	assert.deepEqual(summary, { base: '小杰夜报B_2026-10-10_2026-10-11', messages: 3, images: 2, failed_images: 1, bytes: out.buffer().length });
	assert.deepEqual(asked, [['img_a', 'm1'], ['img_gone', 'm1'], ['img_b', 'm2']]);
	const files = readZip(out.buffer());
	const root = '小杰夜报B_2026-10-10_2026-10-11';
	assert.deepEqual([...files.keys()].sort(), [`${root}/export-errors.txt`, `${root}/images/20261010-165403_0001_1.jpg`, `${root}/images/20261010-165504_0002_1.png`, `${root}/messages.jsonl`, `${root}/transcript.md`].sort());
	assert.deepEqual(files.get(`${root}/images/20261010-165403_0001_1.jpg`), JPEG);
	const transcript = files.get(`${root}/transcript.md`).toString('utf8');
	assert.match(transcript, /^# 小杰夜报B\n/);
	assert.match(transcript, /消息 3 条，图片 3 张（其中 1 张未能导出/);
	assert.ok(transcript.includes('## 2026-10-10\n\n[16:54:03] 7681\n今天的图\n![图片](images/20261010-165403_0001_1.jpg)\n[图片未能导出：LarkAgentX 图片资源 HTTP 404]\n\n[16:55:04] 7681\n![图片](images/20261010-165504_0002_1.png)\n\n## 2026-10-11\n\n[16:54:03] 7999\n第二天\n'));
	const lines = files.get(`${root}/messages.jsonl`).toString('utf8').trim().split('\n').map((line) => JSON.parse(line));
	assert.deepEqual(lines.map((line) => line.seq), [1, 2, 3]);
	assert.deepEqual(lines[0], { seq: 1, message_id: 'm1', time: '2026-10-10T16:54:03+08:00', sender_id: '7681', sender_name: null, type: 'post', text: '今天的图', images: ['images/20261010-165403_0001_1.jpg'], missing_images: 1 });
	assert.deepEqual(lines[1].images, ['images/20261010-165504_0002_1.png']);
	// every image path the transcript and the lines mention exists in the archive
	for (const path of [...transcript.matchAll(/!\[图片\]\(([^)]+)\)/g)].map((match) => match[1])) assert.ok(files.has(`${root}/${path}`), path);
	assert.match(files.get(`${root}/export-errors.txt`).toString('utf8'), /2026-10-10 16:54:03 消息 m1 图片 2：LarkAgentX 图片资源 HTTP 404/);
});

test('limits degrade to listed errors instead of failing the whole export', async () => {
	const out = collector();
	const summary = await writeHistoryBundle({
		title: 'g', from: '2026-10-10', to: '2026-10-11', rows: sampleRows(), write: out.write, now: () => T0,
		fetchImage: async () => Buffer.concat([JPEG, Buffer.alloc(64)]),
		limits: { maxImages: 1, maxImageBytes: 1024, maxTotalBytes: 1024 * 1024 },
	});
	assert.equal(summary.images, 1);
	assert.equal(summary.failed_images, 2);
	const files = readZip(out.buffer());
	assert.match(files.get('g_2026-10-10_2026-10-11/export-errors.txt').toString('utf8'), /超过单次导出 1 张图片的上限/);
	const empty = collector();
	const none = await writeHistoryBundle({ title: 'g', from: '2026-10-10', to: '2026-10-10', rows: [], write: empty.write, fetchImage: async () => JPEG });
	assert.equal(none.messages, 0);
	assert.deepEqual([...readZip(empty.buffer()).keys()].sort(), ['g_2026-10-10_2026-10-10/messages.jsonl', 'g_2026-10-10_2026-10-10/transcript.md']);
});

test('the transcript and the lines agree on senders with names', () => {
	const entries = [{ message_id: 'a', time_ms: T0, sender_id: 'ou_1', sender_name: '大力', type: 'text', blocks: [{ kind: 'text', text: '有名字' }] }];
	assert.ok(renderTranscript({ title: 't', from: '2026-10-10', to: '2026-10-10', entries }).includes('[16:54:03] 大力\n有名字'));
	assert.equal(JSON.parse(renderJsonl(entries)).sender_name, '大力');
});

test('a renamed route exports its own group, not the group that used to hold its key', () => {
	// relay_old was 小杰交流 and was renamed to 小杰夜报B; 小杰交流 now lives on relay_new.
	const routes = [
		{ key: 'relay_old', chatName: '小杰夜报B', chatId: 'oc_b_group' },
		{ key: 'relay_new', chatName: '小杰交流', chatId: 'oc_talk' },
		{ key: 'xiaojie', chatName: '小杰夜报～', chatId: '' },
	];
	const numericIdsByKey = new Map([['relay_old', ['7694']], ['relay_new', ['7678']], ['xiaojie', ['7665']]]);
	assert.deepEqual(resolveRouteChatIds({ route: routes[0], routes, numericIdsByKey, seenUnderKey: ['7678', 'oc_talk', '7694', 'oc_b_group'] }), ['7694', 'oc_b_group']);
	// the renamed-away group still finds its history, which sits under the other key, by chat id
	assert.deepEqual(resolveRouteChatIds({ route: routes[1], routes, numericIdsByKey, seenUnderKey: ['7678'] }), ['7678', 'oc_talk']);
	// an older id nobody else claims stays with the route it was recorded under
	assert.deepEqual(resolveRouteChatIds({ route: routes[2], routes, numericIdsByKey, seenUnderKey: ['7665', 'oc_legacy'] }), ['7665', 'oc_legacy']);
});

const KEY = 'ab'.repeat(32), IV = 'cd'.repeat(12);
const clientCard = (text) => JSON.stringify({ schema: '2.0', body: { elements: [{ tag: 'img', property: { imageID: '5' } }, { tag: 'markdown', property: { elements: [{ tag: 'plain_text', property: { content: text } }] } }] } });
const wsCardRow = (id, text, extra = {}) => ({ source_message_id: id, source_chat_id: '7684', source_create_time: T0, message: { msg_type: 'interactive', sender: { sender_id: '7001' }, body: { content: clientCard(text) }, ...extra } });

test('card pictures come from the stored keys, else the bridge spool, else the official image keys', () => {
	// 1. keys stored with the row (cards relayed from now on)
	const stored = bundleEntry(wsCardRow('101', '研报摘要', { larkagentx_images: [{ image_id: 'img_stored', key_hex: KEY, iv_hex: IV }] }), { cardImages: new Map([['101', [{ image_id: 'img_spool', key_hex: KEY, iv_hex: IV }]]]) });
	assert.deepEqual(stored.blocks, [{ kind: 'text', text: '研报摘要' }, { kind: 'image', ref: { key: 'img_stored', larkagentx: { image_id: 'img_stored', key_hex: KEY, iv_hex: IV } } }]);
	// 2. an older row: keys looked up in the bridge spool
	const spooled = bundleEntry(wsCardRow('102', '调研纪要'), { cardImages: new Map([['102', [{ image_id: 'img_spool', key_hex: KEY, iv_hex: IV }, { image_id: 'img_spool', key_hex: KEY, iv_hex: IV }, { image_id: 'bad', key_hex: 'x', iv_hex: 'y' }]]]) });
	assert.deepEqual(spooled.blocks.filter((block) => block.kind === 'image').map((block) => block.ref.key), ['img_spool']);
	// nothing known: the internal element number is not an image key, so no bogus image
	assert.deepEqual(bundleEntry(wsCardRow('103', '只有文字能导出')).blocks, [{ kind: 'text', text: '只有文字能导出' }]);
	// 3. a card read through the official API carries portable keys, fetched by message id
	const official = bundleEntry({ source_message_id: 'om_1', source_chat_id: 'oc_1', source_create_time: T0, message: { msg_type: 'interactive', sender: { id: 'ou_1', sender_name: '杨宁' },
		body: { content: JSON.stringify({ schema: '2.0', body: { elements: [{ tag: 'img', img_key: 'img_v3_official', alt: { tag: 'plain_text', content: '' } }, { tag: 'markdown', content: '盘前要点' }] } }) } } });
	assert.deepEqual(official.blocks, [{ kind: 'text', text: '盘前要点' }, { kind: 'image', ref: { key: 'img_v3_official', larkagentx: null } }]);
});

test('the client-upgrade banner picture is never exported, in either card shape', () => {
	const wsBanner = wsCardRow('104', '请升级至最新版本客户端，以查看内容', { larkagentx_images: [{ image_id: 'img_banner', key_hex: KEY, iv_hex: IV }] });
	assert.deepEqual(bundleEntry(wsBanner, { cardImages: new Map([['104', [{ image_id: 'img_banner', key_hex: KEY, iv_hex: IV }]]]) }).blocks, []);
	const officialBanner = { source_message_id: 'om_2', source_chat_id: 'oc_1', source_create_time: T0, message: { msg_type: 'interactive', body: { content: JSON.stringify({ title: null, elements: [[{ tag: 'img', image_key: 'img_v3_banner' }, { tag: 'text', text: 'Upgrade to the latest app version to view the content' }]] }) } } };
	assert.deepEqual(bundleEntry(officialBanner).blocks, []);
	assert.deepEqual(bundleEntries([wsBanner, officialBanner]), []);
});

test('only WebSocket-era cards that show a picture and lack stored keys are looked up', () => {
	assert.equal(cardNeedsImageLookup(wsCardRow('105', 'x')), true);
	assert.equal(cardNeedsImageLookup(wsCardRow('106', 'x', { larkagentx_images: [{ image_id: 'i', key_hex: KEY, iv_hex: IV }] })), false);
	assert.equal(cardNeedsImageLookup({ source_message_id: 'om_3', source_chat_id: 'oc_1', message: { msg_type: 'interactive', body: { content: clientCard('x') } } }), false);
	assert.equal(cardNeedsImageLookup({ source_message_id: '107', source_chat_id: '7684', message: { msg_type: 'interactive', body: { content: JSON.stringify({ schema: '2.0', body: { elements: [{ tag: 'markdown', content: '纯文字卡片' }] } }) } } }), false);
	assert.equal(cardNeedsImageLookup({ source_message_id: '108', source_chat_id: '7684', message: { msg_type: 'text', body: { content: '{"text":"imageID"}' } } }), false);
});

test('resolved sender names fill in only where the message carries none', async () => {
	const rows = [
		{ source_message_id: '201', source_chat_id: '7684', source_create_time: T0, message: { msg_type: 'text', sender: { sender_id: '7001' }, body: { content: JSON.stringify({ text: '甲说' }) } } },
		{ source_message_id: '202', source_chat_id: '7684', source_create_time: T0 + 1000, message: { msg_type: 'text', sender: { sender_id: '7002' }, body: { content: JSON.stringify({ text: '乙说' }) } } },
		{ source_message_id: 'om_9', source_chat_id: 'oc_1', source_create_time: T0 + 2000, message: { msg_type: 'text', sender: { id: 'ou_9', sender_name: '已有名字' }, body: { content: JSON.stringify({ text: '丙说' }) } } },
	];
	assert.deepEqual([...unnamedSendersByChat(rows)], [['7684', ['7001', '7002']]]);
	const out = collector();
	await writeHistoryBundle({ title: 'g', from: '2026-10-10', to: '2026-10-10', rows, write: out.write, fetchImage: async () => JPEG, senderNames: new Map([['7001', '小刘老师'], ['ou_9', '不该覆盖']]) });
	const files = readZip(out.buffer());
	const transcript = files.get('g_2026-10-10_2026-10-10/transcript.md').toString('utf8');
	assert.ok(transcript.includes('[16:54:03] 小刘老师\n甲说'));
	assert.ok(transcript.includes('[16:54:04] 7002\n乙说'));
	assert.ok(transcript.includes('[16:54:05] 已有名字\n丙说'));
	assert.match(transcript, /发送者有名字时显示名字，否则显示为平台 ID/);
	const lines = files.get('g_2026-10-10_2026-10-10/messages.jsonl').toString('utf8').trim().split('\n').map((line) => JSON.parse(line));
	assert.deepEqual(lines.map((line) => [line.sender_id, line.sender_name]), [['7001', '小刘老师'], ['7002', null], ['ou_9', '已有名字']]);
});

test('a card with a picture is packed with its text first and the picture after it', async () => {
	const out = collector();
	const summary = await writeHistoryBundle({
		title: '调研纪要', from: '2026-10-10', to: '2026-10-10', rows: [wsCardRow('301', '核心结论：景气度上行')], write: out.write,
		cardImages: new Map([['301', [{ image_id: 'img_card', key_hex: KEY, iv_hex: IV }]]]),
		fetchImage: async (ref) => { assert.deepEqual(ref, { key: 'img_card', larkagentx: { image_id: 'img_card', key_hex: KEY, iv_hex: IV } }); return PNG; },
	});
	assert.equal(summary.images, 1);
	const files = readZip(out.buffer());
	assert.ok(files.get('调研纪要_2026-10-10_2026-10-10/transcript.md').toString('utf8').includes('[16:54:03] 7001\n核心结论：景气度上行\n![图片](images/20261010-165403_0001_1.png)\n'));
	assert.deepEqual(files.get('调研纪要_2026-10-10_2026-10-10/images/20261010-165403_0001_1.png'), PNG);
});
