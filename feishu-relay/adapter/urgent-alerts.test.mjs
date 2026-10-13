import assert from 'node:assert/strict';
import test from 'node:test';
import { createUrgentAlerts } from './urgent-alerts.mjs';

function setup(initial = null, bridgeError = null) {
	const rows = initial ? [{ value: initial }] : [];
	const calls = [];
	const ledger = {
		calls,
		async query(sql, params) {
			calls.push([sql, params]);
			if (sql.startsWith('SELECT')) return { rows };
			rows[0] = { value: params[1] };
			return { rows: [] };
		},
	};
	const bridgeCalls = [];
	const bridgePost = async (...args) => {
		bridgeCalls.push(args);
		if (bridgeError) throw new Error(bridgeError);
		return { status: 'ok', recipients: [] };
	};
	return { alerts: createUrgentAlerts({ ledger, bridgePost, now: () => new Date('2026-10-13T06:00:00Z') }), bridgeCalls, calls };
}

test('settings are persisted and returned in the configured shape', async () => {
	const { alerts, calls } = setup();
	assert.deepEqual(await alerts.routes[0].handle({}), { settings: { enabled: false, chat_id: '', user_id: '', name: '' } });
	const result = await alerts.routes[1].handle({ body: { enabled: true, chat_id: 'chat', user_id: 'person', name: '接收人' } });
	assert.deepEqual(result, { settings: { enabled: true, chat_id: 'chat', user_id: 'person', name: '接收人' } });
	assert.deepEqual(calls.at(-1)[1], ['default', { enabled: true, chat_id: 'chat', user_id: 'person', name: '接收人' }]);
});

test('notify does nothing when disabled or without a recipient', async () => {
	const disabled = setup({ enabled: false, chat_id: 'chat', user_id: 'person', name: '接收人' });
	assert.equal(await disabled.alerts.notify('disabled', '文本'), false);
	assert.equal(disabled.bridgeCalls.length, 0);
	const missing = setup({ enabled: true, chat_id: '', user_id: '', name: '' });
	assert.equal(await missing.alerts.notify('missing', '文本'), false);
	assert.equal(missing.bridgeCalls.length, 0);
});

test('notify logs and returns false when the bridge refuses', async () => {
	const { alerts, bridgeCalls } = setup({ enabled: true, chat_id: 'chat', user_id: 'person', name: '接收人' }, 'bridge refused');
	const original = console.error;
	console.error = () => {};
	try { assert.equal(await alerts.notify('refused', '文本'), false); } finally { console.error = original; }
	assert.deepEqual(bridgeCalls, [['/alerts/urgent', { chat_id: 'chat', user_id: 'person', text: '[relay 运维] 文本' }, 30_000]]);
});

test('the test route lets a bridge failure surface', async () => {
	const { alerts } = setup({ enabled: true, chat_id: 'chat', user_id: 'person', name: '接收人' }, 'bridge offline');
	await assert.rejects(() => alerts.routes[3].handle({}), /bridge offline/);
});

test('recipient route proxies the bridge response', async () => {
	const { alerts, bridgeCalls } = setup();
	assert.deepEqual(await alerts.routes[2].handle({}), { status: 'ok', recipients: [] });
	assert.deepEqual(bridgeCalls, [['/alerts/recipients', {}, 30_000]]);
});
