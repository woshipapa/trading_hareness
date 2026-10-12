import assert from 'node:assert/strict';
import test from 'node:test';
import { completedAssetLookupSql, deliveryRetryDelaySeconds, ingestionStatusBySourceSql, observabilitySql, relayRouteFromRow, relayRouteSeedSql, removedRouteMarker } from './ledger.mjs';

test('only treats an asset as reusable after its parent remote batch completed', () => {
	const sql = completedAssetLookupSql();
	assert.match(sql, /JOIN ingestion_jobs j ON j\.job_id = a\.job_id/);
	assert.match(sql, /a\.state = 'completed'/);
	assert.match(sql, /j\.status = 'completed'/);
	assert.match(sql, /j\.remote_batch_id IS NOT NULL/);
});

test('delivery outbox retry delay is bounded exponential backoff', () => {
	assert.equal(deliveryRetryDelaySeconds(1), 10);
	assert.equal(deliveryRetryDelaySeconds(2), 20);
	assert.equal(deliveryRetryDelaySeconds(6), 300);
	assert.equal(deliveryRetryDelaySeconds(99), 300);
});

test('operator-paused work is reported separately from actionable failures', () => {
	const sql = observabilitySql();
	assert.match(sql, /AS paused/);
	assert.match(sql, /error_class='operator_pause'/);
	assert.match(sql, /AS delivery_outbox_paused/);
	assert.match(sql, /coalesce\(j\.error_class,''\) <> 'operator_pause'/);
});

test('a completed source can retain its latest resolved transport error without reviving it', () => {
	const sql = ingestionStatusBySourceSql();
	assert.match(sql, /latest_failure_error/);
	assert.match(sql, /status IN \('failed','retryable_failed'\)/);
	assert.match(sql, /coalesce\(error_class,''\) <> 'operator_pause'/);
});

test('a removed route is not materialized again from the configuration on the next start', () => {
	const sql = relayRouteSeedSql();
	assert.match(sql, /WHERE NOT EXISTS \(SELECT 1 FROM feishu_group_relay_route_state WHERE state_key=\$7\)/);
	assert.match(sql, /ON CONFLICT\(source_key\) DO NOTHING/);
	assert.equal(removedRouteMarker('xiaojie'), 'removed:xiaojie');
});

test('a route row carries whether it was archived because the account left its group', () => {
	const live = relayRouteFromRow({ source_key: 'relay_x', chat_id: '', chat_name: '小杰夜报B', route_tag: 'xiaojie', enabled: true, target_chat_ids: ['oc_t'], archived_at: null, archived_reason: null, archived_chat_ids: [] });
	assert.deepEqual([live.archivedAt, live.archivedReason, live.archivedChatIds, live.enabled], [null, null, [], true]);
	const at = new Date('2026-10-12T07:18:02Z');
	const gone = relayRouteFromRow({ source_key: 'relay_dy', chat_id: '', chat_name: '调研纪要', route_tag: 'diaoyan', enabled: false, target_chat_ids: null, archived_at: at, archived_reason: 'left', archived_chat_ids: [7665379050021673304n.toString()] });
	assert.deepEqual([gone.archivedAt, gone.archivedReason, gone.archivedChatIds, gone.enabled, gone.targetChatIds], [at, 'left', ['7665379050021673304'], false, []]);
	// a stale reason on a row that is no longer archived is not reported
	assert.equal(relayRouteFromRow({ source_key: 'k', enabled: true, archived_at: null, archived_reason: 'left' }).archivedReason, null);
});
