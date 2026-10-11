import assert from 'node:assert/strict';
import test from 'node:test';
import { existsSync, readFileSync } from 'node:fs';

// The source tree keeps the registry in feishu-relay/config; the image ships it
// next to index.mjs (/app/source-registry.json), where CI also runs these tests.
const sourceRegistry = new URL('../config/source-registry.json', import.meta.url);
const registryFile = existsSync(sourceRegistry) ? sourceRegistry : new URL('./source-registry.json', import.meta.url);

test('xiaojie analyst route is registered for tagged summary ingestion', () => {
	const registry = JSON.parse(readFileSync(registryFile, 'utf8'));
	const route = registry.routes.find((item) => item.tag === 'xiaojie');
	assert.deepEqual(route, {
		tag: 'xiaojie', label: '小杰', topic_key: 'market', publisher_key: 'xiaojie', remote_analyst_id: 'xiaojie', enabled: true,
	});
});

test('sanshu and jinli are registered so their tagged relays reach summary ingestion', () => {
	const registry = JSON.parse(readFileSync(registryFile, 'utf8'));
	assert.deepEqual(registry.routes.find((item) => item.tag === 'sanshu'), {
		tag: 'sanshu', label: '节奏三叔', topic_key: 'market', publisher_key: 'sanshu', remote_analyst_id: 'sanshu', enabled: true,
	});
	assert.deepEqual(registry.routes.find((item) => item.tag === 'jinli'), {
		tag: 'jinli', label: '小锦鲤', topic_key: 'market', publisher_key: 'jinli', remote_analyst_id: 'jinli', enabled: true,
	});
});

test('every registered tag and analyst id is unique and well-formed', () => {
	const registry = JSON.parse(readFileSync(registryFile, 'utf8'));
	const tags = registry.routes.map((item) => item.tag);
	assert.equal(new Set(tags).size, tags.length);
	for (const route of registry.routes) {
		assert.match(route.tag, /^[a-z0-9][a-z0-9_-]{0,31}$/);
		assert.match(route.remote_analyst_id, /^[a-z0-9]+(?:-[a-z0-9]+)*$/);
	}
});
