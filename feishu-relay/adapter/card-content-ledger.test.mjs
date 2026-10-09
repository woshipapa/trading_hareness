import assert from 'node:assert/strict';
import test from 'node:test';
import { ledgerMessageText, renderLedgerTranscript } from './card-content.mjs';

const card = JSON.stringify({ body: { elements: [
	{ tag: 'div', margin: '0px 0px -24px -99px', text: { tag: 'plain_text', content: 'decoy' } },
	{ tag: 'div', text: { tag: 'plain_text', content: 'Eric:\n临盘拉了欧盟反制' } },
] } });

test('a backfilled card renders its real text, not a placeholder', () => {
	assert.match(ledgerMessageText({ msg_type: 'interactive', body: { content: card } }), /临盘拉了欧盟反制/);
	assert.doesNotMatch(ledgerMessageText({ msg_type: 'interactive', body: { content: card } }), /decoy/);
});

test('transcript is chronological, day-grouped, UTC+8 and speaker-labelled', () => {
	const t = Date.UTC(2026, 9, 9, 16, 30); // 2026-10-10 00:30 in UTC+8
	const out = renderLedgerTranscript([
		{ source_create_time: t + 60_000, sender_name: 'B', message: { msg_type: 'text', body: { content: JSON.stringify({ text: 'later' }) } } },
		{ source_create_time: t, sender_name: 'A', message: { msg_type: 'text', body: { content: JSON.stringify({ text: 'first\nline' }) } } },
		{ source_create_time: t, message: { msg_type: 'system', body: { content: '{}' } } },
	]);
	assert.equal(out, '== 2026-10-10 ==\n00:30 A: first\n    line\n00:31 B: later');
});

test('a ledger row with only a sender id still labels the speaker', () => {
	const out = renderLedgerTranscript([{ source_create_time: Date.UTC(2026, 9, 9, 4, 0), message: { sender: { sender_id: '768' }, msg_type: 'text', body: { content: JSON.stringify({ text: 'x' }) } } }]);
	assert.match(out, /12:00 768: x/);
});
