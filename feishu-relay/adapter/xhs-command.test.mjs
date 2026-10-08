import assert from 'node:assert/strict';
import test from 'node:test';
import { parseXhsCommand } from './xhs-command.mjs';

test('parses explicit xhs commands and defaults to status', () => {
	assert.deepEqual(parseXhsCommand({ message: { chat_id: 'oc_x', content: '#xhs 最新' }, event_id: 'om_1' }), {
		command: '最新', chat_id: 'oc_x', message_id: 'om_1',
	});
	assert.equal(parseXhsCommand({ message: { content: '普通消息' } }), null);
	assert.equal(parseXhsCommand({ message: { content: '#xhs' } }).command, 'status');
});

test('preserves sender open id for edge-side admin checks', () => {
	const parsed = parseXhsCommand({
		message: { chat_id: 'oc_x', content: '#xhs intel following approve user-123' },
		sender: { sender_id: { open_id: 'ou_admin' } },
	});
	assert.equal(parsed.sender_open_id, 'ou_admin');
});

test('enforces the optional chat binding', () => {
	assert.equal(parseXhsCommand({ message: { chat_id: 'oc_other', content: '#xhs status' } }, 'oc_bound'), null);
	assert.equal(parseXhsCommand({ message: { chat_id: 'oc_bound', content: '#xhs status' } }, 'oc_bound').command, 'status');
	assert.equal(parseXhsCommand({ larkagentx_command_lane: true, message: { chat_id: '7685299326167305463', content: '#xhs status' } }, 'oc_bound').command, 'status');
	assert.equal(parseXhsCommand({ message: { chat_id: 'oc_bound', content: '#xhs status' } }, 'oc_bound,oc_other').command, 'status');
});
