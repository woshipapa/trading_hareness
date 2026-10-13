import assert from 'node:assert/strict';
import test from 'node:test';
import { createFeatureRoutes } from './feature-routes.mjs';

function response() {
	return {
		status: null,
		body: null,
		writeHead(status) { this.status = status; },
		end(body) { this.body = body; },
	};
}

test('dispatches string and regexp routes with params and query', async () => {
	const seen = [];
	const dispatch = createFeatureRoutes({
		routes: [
			{ method: 'GET', path: '/plain/:id', handle: async ({ params, query }) => { seen.push([params.id, query.get('q')]); return { ok: true }; } },
			{ method: 'POST', path: /^\/items\/(?<item>[^/]+)$/, handle: async ({ params, body }) => ({ item: params.item, body }) },
		],
		readJsonBody: async () => ({ value: 3 }), token: 'secret',
	});
	const getResponse = response();
	assert.equal(await dispatch({ method: 'GET', url: '/plain/abc?q=needle', headers: {} }, getResponse), true);
	assert.equal(getResponse.status, 200);
	assert.deepEqual(seen, [['abc', 'needle']]);
	const postResponse = response();
	await dispatch({ method: 'POST', url: '/items/one', headers: {} }, postResponse);
	assert.deepEqual(JSON.parse(postResponse.body), { item: 'one', body: { value: 3 } });
});

test('internal routes require the injected bridge token', async () => {
	const dispatch = createFeatureRoutes({ routes: [{ method: 'GET', path: '/internal', internal: true, handle: async () => ({ ok: true }) }], readJsonBody: async () => ({}), token: 'secret' });
	const refused = response();
	assert.equal(await dispatch({ method: 'GET', url: '/internal', headers: { 'x-larkagentx-token': 'wrong' } }, refused), true);
	assert.equal(refused.status, 403);
	const accepted = response();
	await dispatch({ method: 'GET', url: '/internal', headers: { 'x-larkagentx-token': 'secret' } }, accepted);
	assert.equal(accepted.status, 200);
});

test('handler errors become 400 and unmatched requests are reported unhandled', async () => {
	const dispatch = createFeatureRoutes({ routes: [{ method: 'POST', path: '/error', handle: async () => { throw new Error('broken'); } }], readJsonBody: async () => ({}), token: 'secret' });
	const failed = response();
	assert.equal(await dispatch({ method: 'POST', url: '/error', headers: {} }, failed), true);
	assert.equal(failed.status, 400);
	assert.deepEqual(JSON.parse(failed.body), { status: 'error', message: 'broken' });
	assert.equal(await dispatch({ method: 'GET', url: '/missing', headers: {} }, response()), false);
});
