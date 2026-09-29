import assert from 'node:assert/strict';
import test from 'node:test';
import { crossSiteWriteRejection } from './cross-site-guard.mjs';

const post = (headers) => ({ method: 'POST', headers });

test('server-to-server writes carry no browser labels and pass', () => {
	// n8n, the bridge, the quant alert path and Node fetch send no Origin.
	assert.equal(crossSiteWriteRejection(post({ host: '127.0.0.1:18300', 'content-type': 'application/json' })), null);
	assert.equal(crossSiteWriteRejection(post({ host: '127.0.0.1:18300', 'sec-fetch-mode': 'cors' })), null);
});

test('the dashboard posting to itself passes, through nginx or directly', () => {
	assert.equal(crossSiteWriteRejection(post({
		host: '100.101.102.103', origin: 'http://100.101.102.103:18301', 'sec-fetch-site': 'same-origin',
	})), null);
	assert.equal(crossSiteWriteRejection(post({ host: 'localhost:18300', origin: 'http://localhost:18300' })), null);
});

test('a page on another site cannot write, even as a text/plain form', () => {
	assert.equal(crossSiteWriteRejection(post({
		host: '100.101.102.103', origin: 'https://evil.example', 'sec-fetch-site': 'cross-site', 'content-type': 'text/plain',
	})), 'cross_site_request');
	// Older browsers without Sec-Fetch-Site still send Origin on a cross-origin POST.
	assert.equal(crossSiteWriteRejection(post({ host: '100.101.102.103', origin: 'https://evil.example' })), 'cross_origin_request');
	assert.equal(crossSiteWriteRejection(post({ host: '100.101.102.103', origin: 'null' })), 'opaque_origin');
	assert.equal(crossSiteWriteRejection(post({ host: '100.101.102.103', origin: 'not a url' })), 'invalid_origin');
});

test('another port on the same host is a different origin', () => {
	assert.equal(crossSiteWriteRejection(post({
		host: '100.101.102.103', origin: 'http://100.101.102.103:5678', 'sec-fetch-site': 'same-site',
	})), 'cross_site_request');
});

test('reads are never refused', () => {
	for (const method of ['GET', 'HEAD', 'OPTIONS']) {
		assert.equal(crossSiteWriteRejection({ method, headers: { origin: 'https://evil.example', 'sec-fetch-site': 'cross-site' } }), null);
	}
	for (const method of ['PUT', 'DELETE', 'PATCH']) {
		assert.equal(crossSiteWriteRejection({ method, headers: { host: 'a', origin: 'https://evil.example' } }), 'cross_origin_request');
	}
});
