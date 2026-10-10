import assert from 'node:assert/strict';
import test from 'node:test';

import { matchResearchRoute, researchRouteTable } from './research-routes.mjs';

const UUID = '0123abcd-4567-89ef-0123-456789abcdef';

test('static reads answer GET only and static actions POST only', () => {
	assert.deepEqual(matchResearchRoute('GET', '/api/research/claims'), { kind: 'read', method: 'GET', upstream: '/api/v1/analyst-claims' });
	assert.equal(matchResearchRoute('POST', '/api/research/claims'), null);
	assert.deepEqual(matchResearchRoute('POST', '/api/research/pipeline/daily'), { kind: 'action', method: 'POST', upstream: '/api/v1/pipeline/daily' });
	assert.equal(matchResearchRoute('GET', '/api/research/pipeline/daily'), null);
	// quant serves this path for POST only, so there is no GET alias for it
	assert.equal(matchResearchRoute('GET', '/api/research/analyst-research/reviews/run'), null);
	assert.equal(matchResearchRoute('POST', '/api/research/analyst-research/reviews/run').kind, 'action');
});

test('template routes copy their parameters into the upstream', () => {
	const cases = [
		['GET', `/api/research/research-runs/${UUID}`, 'read', `/api/v1/research/runs/${UUID}`],
		['GET', '/api/research/datasources/capabilities/bars.minute', 'read', '/api/v1/datasources/capabilities/bars.minute'],
		['GET', '/api/research/datasources/read/tdx_public/bars.minute', 'read', '/api/v1/datasources/read/tdx_public/bars.minute'],
		['GET', '/api/research/intraday/decision-cards/000001.sz', 'read', '/api/v1/intraday/decision-cards/000001.SZ'],
		['PUT', '/api/research/paper/accounts', 'action', '/api/v1/paper/accounts'],
		['POST', `/api/research/paper/decisions/${UUID}/accept`, 'action', `/api/v1/paper/decisions/${UUID}/accept`],
		['POST', `/api/research/analyst-prompt-lab/candidates/${UUID}/label`, 'action', `/api/v1/analyst-prompt-lab/candidates/${UUID}/label`],
		['POST', '/api/research/analyst-prompt-lab/evaluate/scenario_context', 'action', '/api/v1/analyst-prompt-lab/evaluate/scenario_context'],
		['POST', '/api/research/stocks/600000.sh/study', 'action', '/api/v1/stocks/600000.SH/study'],
		['POST', `/api/research/claim-review/${UUID}`, 'action', `/api/v1/claim-review/${UUID}`],
	];
	for (const [method, path, kind, upstream] of cases) {
		assert.deepEqual(matchResearchRoute(method, path), { kind, method, upstream }, `${method} ${path}`);
	}
});

test('parameters of the wrong shape, or the wrong method, do not match', () => {
	for (const [method, path] of [
		['GET', '/api/research/research-runs/not-a-uuid'],
		['GET', '/api/research/intraday/decision-cards/000001'],
		['POST', '/api/research/analyst-prompt-lab/evaluate/anything_else'],
		['POST', '/api/research/stocks/600000.SH%2F..%2Fadmin/study'],
		['GET', `/api/research/claim-review/${UUID}`],
		['GET', '/api/research/datasources/capabilities/Bars.minute'],
		['GET', '/api/research/datasources/read/tdx-public/bars.minute'],
		['DELETE', '/api/research/paper/accounts'],
		['GET', '/api/v2/whatever'],
	]) {
		assert.equal(matchResearchRoute(method, path), null, `${method} ${path}`);
	}
});

test('every method and dashboard path appears once', () => {
	const seen = new Set();
	for (const { method, path } of researchRouteTable()) {
		const key = `${method} ${path}`;
		assert.ok(!seen.has(key), `${key} is declared twice`);
		seen.add(key);
	}
});
