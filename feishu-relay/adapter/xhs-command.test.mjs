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

test('enforces the optional chat binding', () => {
	assert.equal(parseXhsCommand({ message: { chat_id: 'oc_other', content: '#xhs status' } }, 'oc_bound'), null);
	assert.equal(parseXhsCommand({ message: { chat_id: 'oc_bound', content: '#xhs status' } }, 'oc_bound').command, 'status');
});
