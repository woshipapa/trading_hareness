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
