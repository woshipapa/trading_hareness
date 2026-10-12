import assert from 'node:assert/strict';
import test from 'node:test';
import { isSystemMessage, isSystemRelayPlaceholder } from './message-filter.mjs';

test('filters Feishu system notices and their relayed #tag placeholder', () => {
	assert.equal(isSystemMessage({ msg_type: 'system' }), true);
	assert.equal(isSystemMessage({ msg_type: 'text' }), false);
	assert.equal(isSystemRelayPlaceholder('#quanneng\n[system]　此消息类型无法跨租户保持原组件。', 'quanneng'), true);
	assert.equal(isSystemRelayPlaceholder('#quanneng\n正常分析内容', 'quanneng'), false);
});

test('a system placeholder is still recognised behind the forwarding timestamp', () => {
	assert.equal(isSystemRelayPlaceholder('#anqiang 2026-10-12 11:45:03\n[system] 某人加入群聊', 'anqiang'), true);
	assert.equal(isSystemRelayPlaceholder('#anqiang 2026-10-12 11:45:03\n正常内容', 'anqiang'), false);
});
