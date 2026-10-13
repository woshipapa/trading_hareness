import assert from 'node:assert/strict';
import test from 'node:test';
import { createUrgentAlerts } from './urgent-alerts.mjs';

function setup(initial = null, bridgeError = null, recipients = []) {
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
		return args[0] === '/alerts/recipients' ? { recipients } : { status: 'ok' };
	};
	return { alerts: createUrgentAlerts({ ledger, bridgePost, now: () => new Date('2026-10-13T06:00:00Z') }), bridgeCalls, calls, ledger };
}

test('settings reject coerced booleans, malformed ids, long names, and unknown recipients', async () => {
	const { alerts } = setup();
	await assert.rejects(() => alerts.routes[1].handle({ body: { enabled: 'false', chat_id: '', user_id: '', name: '' } }), /enabled/);
	await assert.rejects(() => alerts.routes[1].handle({ body: { enabled: false, chat_id: 'chat', user_id: 'person', name: '' } }), /数字/);
	await assert.rejects(() => alerts.routes[1].handle({ body: { enabled: false, chat_id: ' 123', user_id: '456', name: '' } }), /数字/);
	await assert.rejects(() => alerts.routes[1].handle({ body: { enabled: false, chat_id: '', user_id: '', name: 'x'.repeat(201) } }), /名称/);
});

test('saving a recipient verifies the pair with the bridge', async () => {
	const { alerts, bridgeCalls } = setup();
	await assert.rejects(() => alerts.routes[1].handle({ body: { enabled: true, chat_id: '123', user_id: '456', name: '接收人' } }), /接收人/);
	assert.deepEqual(bridgeCalls, [['/alerts/recipients', {}, 30_000]]);

	const verified = setup(null, null, [{ chat_id: '123', user_id: '456', name: '接收人' }]);
	await verified.alerts.routes[1].handle({ body: { enabled: true, chat_id: '123', user_id: '456', name: '接收人' } });
	const disabled = setup();
	await disabled.alerts.routes[1].handle({ body: { enabled: false, chat_id: '', user_id: '', name: '' } });
});

test('settings are persisted and returned in the configured shape', async () => {
	const { alerts, calls } = setup(null, null, [{ chat_id: '123', user_id: '456', name: '接收人' }]);
	assert.deepEqual(await alerts.routes[0].handle({}), { settings: { enabled: false, chat_id: '', user_id: '', name: '' } });
	const result = await alerts.routes[1].handle({ body: { enabled: true, chat_id: '123', user_id: '456', name: '接收人' } });
	assert.deepEqual(result, { settings: { enabled: true, chat_id: '123', user_id: '456', name: '接收人' } });
	assert.deepEqual(calls.at(-1)[1], ['default', { enabled: true, chat_id: '123', user_id: '456', name: '接收人' }]);
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
	assert.deepEqual(await alerts.routes[2].handle({}), { recipients: [] });
	assert.deepEqual(bridgeCalls, [['/alerts/recipients', {}, 30_000]]);
});
