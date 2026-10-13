import assert from 'node:assert/strict';
import test from 'node:test';
import { createSourceTags } from './source-tags.mjs';

const registry = () => ({
	version: 2, default_topic_key: 'market',
	routes: [
		{ tag: 'liwei', label: '立伟', topic_key: 'market', publisher_key: 'liwei', remote_analyst_id: 'liwei', enabled: true },
		{ tag: 'paused', label: '停用的', topic_key: 'market', publisher_key: 'paused', remote_analyst_id: 'paused', enabled: false },
	],
});

function fakeLedger(rows = []) {
	const saved = [];
	return {
		saved,
		async sourceProfiles() { return rows; },
		async saveSourceProfile(route, defaultTopicKey) { saved.push({ route: { ...route }, defaultTopicKey }); },
	};
}

test('a tag never used before is registered with the tag as publisher and analyst id', async () => {
	const ledger = fakeLedger();
	const tags = createSourceTags({ registry: registry(), ledger });
	await tags.load();
	assert.equal(tags.describe('rccs').registered, false);

	const result = await tags.register({ tag: '#RCCS', label: ' RCCS小杰夜报【禁言】 ' });

	assert.deepEqual(result, { tag: 'rccs', registered: true, enabled: true, origin: 'dashboard', label: 'RCCS小杰夜报【禁言】', created: true });
	assert.deepEqual(ledger.saved, [{ defaultTopicKey: 'market', route: { tag: 'rccs', label: 'RCCS小杰夜报【禁言】', topic_key: 'market', publisher_key: 'rccs', remote_analyst_id: 'rccs', enabled: true, origin: 'dashboard' } }]);
	// The shared map is what the summary-group handler reads.
	assert.equal(tags.routes.get('rccs').enabled, true);
	assert.equal(tags.routes.get('rccs').topic_key, 'market');
});

test('a registered tag is left as it is and nothing is written', async () => {
	const ledger = fakeLedger();
	const tags = createSourceTags({ registry: registry(), ledger });
	const result = await tags.register({ tag: 'liwei', label: '别的名字' });
	assert.deepEqual(result, { tag: 'liwei', registered: true, enabled: true, origin: 'file', label: '立伟', created: false });
	assert.equal(ledger.saved.length, 0);
});

test('a tag the registry file switches off is not switched on from the dashboard', async () => {
	const ledger = fakeLedger();
	const tags = createSourceTags({ registry: registry(), ledger });
	const result = await tags.register({ tag: 'paused' });
	assert.equal(result.enabled, false);
	assert.equal(ledger.saved.length, 0);
	await assert.rejects(() => tags.setEnabled('paused', true), /由仓库登记表声明/);
	await assert.rejects(() => tags.setEnabled('liwei', false), /由仓库登记表声明/);
});

test('dashboard tags from an earlier run are loaded; a stale file profile is not', async () => {
	const ledger = fakeLedger([
		{ source_tag: 'liwei', topic_key: 'market', publisher_key: 'other', analyst_id: 'other', label: '库里的旧值', enabled: true, config: { origin: 'dashboard' } },
		{ source_tag: 'rccs', topic_key: 'market', publisher_key: 'rccs', analyst_id: 'rccs', label: 'RCCS', enabled: true, config: { origin: 'dashboard' } },
		{ source_tag: 'removed', topic_key: 'market', publisher_key: 'removed', analyst_id: 'removed', label: '文件里删掉的', enabled: true, config: { tag: 'removed' } },
	]);
	const tags = createSourceTags({ registry: registry(), ledger });
	await tags.load();
	assert.equal(tags.routes.get('liwei').publisher_key, 'liwei');
	assert.deepEqual(tags.describe('rccs'), { tag: 'rccs', registered: true, enabled: true, origin: 'dashboard', label: 'RCCS' });
	assert.equal(tags.routes.get('rccs').remote_analyst_id, 'rccs');
	assert.equal(tags.describe('removed').registered, false);
	assert.deepEqual(tags.list().map((item) => item.tag), ['liwei', 'paused', 'rccs']);
});

test('a dashboard tag can be switched off and registering it again switches it on', async () => {
	const ledger = fakeLedger();
	const tags = createSourceTags({ registry: registry(), ledger });
	await tags.register({ tag: 'rccs', label: 'RCCS' });
	assert.equal((await tags.setEnabled('rccs', false)).enabled, false);
	assert.equal(tags.routes.get('rccs').enabled, false);
	assert.equal(ledger.saved.at(-1).route.enabled, false);
	// Switching off a tag that was never registered writes nothing.
	assert.equal((await tags.setEnabled('nobody', false)).registered, false);
	assert.equal(ledger.saved.length, 2);

	const again = await tags.register({ tag: 'rccs', label: '换个名字也不改' });
	assert.equal(again.enabled, true);
	assert.equal(again.created, false);
	assert.equal(again.label, 'RCCS');
});

test('an invalid tag is refused before anything is written', async () => {
	const ledger = fakeLedger();
	const tags = createSourceTags({ registry: registry(), ledger });
	await assert.rejects(() => tags.register({ tag: '小杰' }), /标签必须为/);
	await assert.rejects(() => tags.register({ tag: '-x' }), /标签必须为/);
	assert.equal(ledger.saved.length, 0);
});
