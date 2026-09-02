import test from 'node:test';
import assert from 'node:assert/strict';
import { createBaiduPanMarketArchive } from './baidu-pan-market-archive.mjs';

test('archives latest watchlist and leader evidence asynchronously with idempotent keys', async () => {
	const jobs = [];
	const uploaded = [];
	const completed = [];
	const ledger = {
		async enqueueBaiduPanArchive(input) {
			if (jobs.some((item) => item.archive_key === input.archiveKey)) return null;
			const row = { archive_id: `id-${jobs.length + 1}`, archive_key: input.archiveKey, bucket: input.bucket, observed_at: input.observedAt, payload: input.payload };
			jobs.push(row);
			return row;
		},
		async claimBaiduPanArchives() { return jobs.filter((item) => !completed.includes(item.archive_id)); },
		async completeBaiduPanArchive(id, value) { completed.push(id); return { archive_id: id, ...value }; },
		async failBaiduPanArchive() { throw new Error('unexpected failure'); },
		async baiduPanArchiveStatus() { return { queue_depth: jobs.length - completed.length, completed: completed.length }; },
	};
	const baiduPan = {
		async list() { return { list: [] }; },
		async mkdir() { return { errno: 0 }; },
		async uploadReadable(input) { uploaded.push(input.remotePath); return { path: input.remotePath, fsId: uploaded.length }; },
	};
	const fetchImpl = async (url) => {
		if (url.endsWith('/api/v1/intraday/scans/latest?limit=200')) return new Response(JSON.stringify({ scan: { scan_id: 'scan-1', observed_at: '2026-08-30T08:00:00.000Z' }, signals: [] }), { status: 200 });
		if (url.endsWith('/api/v1/research/ten-day-leader-rotation/latest?limit=90')) return new Response(JSON.stringify({ run: { run_id: 'run-1', updated_at: '2026-08-30T08:00:00.000Z' }, candidates: [], intraday: { latest_batch: { scan_id: 'scan-1', observed_at: '2026-08-30T08:00:00.000Z' } } }), { status: 200 });
		return new Response(JSON.stringify({}), { status: 200 });
	};
	const archive = createBaiduPanMarketArchive({ baiduPan, ledger, quantServiceUrl: 'http://quant', enabled: true, fetchImpl, rootPath: '/archive' });
	await archive.poll();
	await archive.drain();
	assert.equal(completed.length, 2);
	assert.equal(uploaded.length, 2);
	assert.ok(uploaded.some((path) => path.includes('/watchlist/')));
	assert.ok(uploaded.some((path) => path.includes('/leader-rotation/')));
	const status = await archive.status();
	assert.equal(status.queue.completed, 2);
});

test('raw overflow uploads a bounded batch before acknowledging the cursor', async () => {
	const requests = [];
	const uploaded = [];
	const ledger = { async baiduPanArchiveStatus() { return { queue_depth: 0, completed: 0 }; } };
	const baiduPan = {
		async list() { return { list: [] }; },
		async mkdir() { return { errno: 0 }; },
		async uploadReadable(input) {
			const chunks = [];
			for await (const chunk of input.readable) chunks.push(Buffer.from(chunk));
			uploaded.push({ path: input.remotePath, bytes: Buffer.concat(chunks) });
			return { path: input.remotePath, fsId: 'raw-1' };
		},
	};
	const fetchImpl = async (url, options = {}) => {
		requests.push({ url, options });
		if (url.includes('/internal/raw-overflow/next')) return new Response(JSON.stringify({
			status: 'ready', stream_key: 'raw_market_observations:realtime_quote', state: 'cloud_overflow',
			before_offset: null,
			first_offset: { effective_at: '2026-09-01T01:00:00.000Z', observation_id: '00000000-0000-0000-0000-000000000001' },
			last_offset: { effective_at: '2026-09-01T01:00:01.000Z', observation_id: '00000000-0000-0000-0000-000000000002' },
			row_count: 2, rows: [{ observation_id: '00000000-0000-0000-0000-000000000001', payload: { price: 1 } }, { observation_id: '00000000-0000-0000-0000-000000000002', payload: { price: 2 } }],
		}), { status: 200 });
		if (url.includes('/internal/raw-overflow/ack')) return new Response(JSON.stringify({ status: 'verified' }), { status: 200 });
		return new Response(JSON.stringify({ status: 'recorded' }), { status: 200 });
	};
	const archive = createBaiduPanMarketArchive({
		baiduPan, ledger, quantServiceUrl: 'http://quant', quantWriteApiKey: 'write-key',
		rawOverflowEnabled: true, enabled: false, rawCapabilities: ['realtime_quote'],
		fetchImpl, rootPath: '/archive', rawRootPath: '/raw', rawBatchRows: 10, intervalSeconds: 30,
	});
	await archive.poll();
	await archive.drainRawOverflow();
	assert.equal(uploaded.length, 1);
	assert.equal(uploaded[0].bytes[0], 0x1f); // gzip magic byte
	assert.ok(requests.some((item) => item.url.includes('/internal/raw-overflow/ack')));
	assert.ok(requests.some((item) => item.url.includes('/internal/raw-overflow/next') && item.url.endsWith('limit=10')));
	assert.equal((await archive.status()).raw_overflow.batches_in_process, 1);
	assert.equal(requests.find((item) => item.url.includes('/internal/raw-overflow/next')).options.headers['X-Quant-Write-Key'], 'write-key');
});
