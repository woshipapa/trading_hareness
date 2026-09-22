import assert from 'node:assert/strict';
import test from 'node:test';
import { resolveFrontendAssetPath } from './frontend-assets.mjs';

const files = new Set([
	'/quant/index.html',
	'/feishu/index.html',
	'/quant/assets/quant.js',
	'/feishu/assets/feishu.js',
]);
const exists = (candidate) => files.has(candidate);

test('selects the quant shell for the root and research routes', () => {
	assert.equal(resolveFrontendAssetPath('/', '/feishu', '/quant', exists), '/quant/index.html');
	assert.equal(resolveFrontendAssetPath('/research', '/feishu', '/quant', exists), '/quant/index.html');
	assert.equal(resolveFrontendAssetPath('/assets/quant.js', '/feishu', '/quant', exists), '/quant/assets/quant.js');
});

test('selects the standalone Feishu shell for relay routes', () => {
	assert.equal(resolveFrontendAssetPath('/monitor', '/feishu', '/quant', exists), '/feishu/index.html');
	assert.equal(resolveFrontendAssetPath('/workbench', '/feishu', '/quant', exists), '/feishu/index.html');
	assert.equal(resolveFrontendAssetPath('/relay', '/feishu', '/quant', exists), '/feishu/index.html');
	assert.equal(resolveFrontendAssetPath('/assets/feishu.js', '/feishu', '/quant', exists), '/feishu/assets/feishu.js');
});
