import assert from 'node:assert/strict';
import test from 'node:test';
import { normalizeLarkAgentXMessage } from './larkagentx-ingress.mjs';

test('normalizes an inbound LarkAgentX text message into the adapter event contract', () => {
	const event = normalizeLarkAgentXMessage({
		msg_id: 'om_larkx_1', chat_id: 'oc_source', from_id: 'ou_sender', chat_type: 2,
		msg_type_name: 'TEXT', content: '  #liwei\n正文  ', create_time: 1720000000,
	}, { now: () => 1730000000 });

	assert.equal(event.event_id, 'larkagentx:om_larkx_1');
	assert.equal(event.source, 'larkagentx');
	assert.equal(event.message.chat_type, 'group');
	assert.equal(event.message.message_type, 'text');
	assert.deepEqual(JSON.parse(event.message.content), { text: '#liwei\n正文' });
	assert.equal(event.sender.sender_id.open_id, 'ou_sender');
});

test('fails closed when an inbound message has no stable identity or content', () => {
	assert.throws(() => normalizeLarkAgentXMessage({ chat_id: 'oc_1', from_id: 'ou_1', content: 'x' }), /message_id/);
	assert.throws(() => normalizeLarkAgentXMessage({ msg_id: 'om_1', chat_id: 'oc_1', from_id: 'ou_1' }), /文本内容/);
});
