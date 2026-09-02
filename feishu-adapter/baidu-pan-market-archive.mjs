import { Readable } from 'node:stream';
import { createHash } from 'node:crypto';
import { gzipSync } from 'node:zlib';

const DEFAULT_ROOT = '/apps/股票paper存储/market-realtime';
const MAX_SNAPSHOT_BYTES = 12 * 1024 * 1024;
const MAX_RAW_BATCH_BYTES = 256 * 1024 * 1024;
const DEFAULT_RAW_CAPABILITIES = ['a_share_prices_snapshot', 'realtime_quote', 'order_book_quote', 'rt_k', 'rt_min', 'rt_min_daily'];

function latestTimestamp(values) {
	return values
		.map((value) => text(value))
		.filter(Boolean)
		.sort()
		.at(-1) ?? null;
}

const SNAPSHOT_SPECS = [
	{
		bucket: 'market-events',
		path: '/api/v1/events/market?limit=500',
		source: 'quant.market.events',
		identity: (body) => {
			const items = body?.items ?? [];
			const latest = items[0];
			return latest?.event_id && latest?.occurred_at ? `${latest.event_id}:${latest.occurred_at}:${body?.total ?? items.length}` : '';
		},
		observedAt: (body) => latestTimestamp((body?.items ?? []).map((item) => item?.occurred_at ?? item?.available_at)),
	},
	{
		bucket: 'all-a-level1',
		path: '/api/v1/market/level1/latest?limit=6000',
		source: 'quant.market.level1.latest',
		identity: (body) => text(body?.snapshot_at),
		observedAt: (body) => body?.snapshot_at,
	},
	{
		bucket: 'watchlist',
		path: '/api/v1/intraday/scans/latest?limit=200',
		source: 'quant.intraday.scans.latest',
		identity: (body) => text(body?.scan?.scan_id),
		observedAt: (body) => body?.scan?.observed_at,
	},
	{
		bucket: 'leader-rotation',
		path: '/api/v1/research/ten-day-leader-rotation/latest?limit=90',
		source: 'quant.ten_day_leader_rotation.latest',
		identity: (body) => {
			const runId = text(body?.run?.run_id, 'none');
			const batchId = text(body?.intraday?.latest_batch?.scan_id, text(body?.intraday?.latest_batch?.observed_at, 'none'));
			return body?.run || body?.intraday?.latest_batch ? `${runId}:${batchId}` : '';
		},
		observedAt: (body) => body?.intraday?.latest_batch?.observed_at ?? body?.run?.updated_at,
	},
	{
		bucket: 'board-rotation',
		path: '/api/v1/intraday/board-rotations/latest',
		source: 'quant.intraday.board_rotations.latest',
		identity: (body) => latestTimestamp((body?.items ?? []).map((item) => item?.updated_at ?? item?.snapshot_minute)),
		observedAt: (body) => latestTimestamp((body?.items ?? []).map((item) => item?.updated_at ?? item?.snapshot_minute)),
	},
	{
		bucket: 'board-mining',
		path: '/api/v1/intraday/board-stock-mining/latest',
		source: 'quant.intraday.board_stock_mining.latest',
		identity: (body) => text(body?.run?.mining_run_id),
		observedAt: (body) => body?.run?.observed_at,
	},
	{
		bucket: 'limit-linkage',
		path: '/api/v1/intraday/limit-linkage/latest',
		source: 'quant.intraday.limit_linkage.latest',
		identity: (body) => text(body?.run?.linkage_run_id),
		observedAt: (body) => body?.run?.observed_at,
	},
	{
		bucket: 'sector-curves',
		path: '/api/v1/market/sectors/intraday/curves',
		source: 'quant.market.sectors.intraday.curves',
		identity: (body) => {
			const points = (body?.items ?? []).flatMap((item) => item?.points ?? []);
			const latest = latestTimestamp(points.map((point) => point?.observed_at));
			return body?.trade_date && latest ? `${body.trade_date}:${latest}` : '';
		},
		observedAt: (body) => latestTimestamp((body?.items ?? []).flatMap((item) => (item?.points ?? []).map((point) => point?.observed_at))),
	},
	{
		bucket: 'strategy-decisions',
		path: '/api/v1/strategy/decisions/latest',
		source: 'quant.strategy.decisions.latest',
		identity: (body) => text(body?.run?.run_id),
		observedAt: (body) => body?.run?.observed_at ?? body?.run?.created_at,
	},
	{
		bucket: 'strategy-health',
		path: '/api/v1/strategy/health',
		source: 'quant.strategy.health',
		identity: (body) => text(body?.observed_at),
		observedAt: (body) => body?.observed_at,
	},
	{
		bucket: 'post-close',
		path: '/api/v1/strategy/post-close/latest',
		source: 'quant.strategy.post_close.latest',
		identity: (body) => text(body?.run?.run_id ?? body?.candidate_run?.run_id),
		observedAt: (body) => body?.run?.updated_at ?? body?.candidate_run?.updated_at,
	},
	{
		bucket: 'pattern-mining',
		path: '/api/v1/strategy/pattern-mining/latest',
		source: 'quant.strategy.pattern_mining.latest',
		identity: (body) => text(body?.run?.run_id),
		observedAt: (body) => body?.run?.updated_at ?? body?.run?.created_at,
	},
	{
		bucket: 'strategy-reviews',
		path: '/api/v1/strategy/reviews/latest',
		source: 'quant.strategy.reviews.latest',
		identity: (body) => text(body?.review?.review_id),
		observedAt: (body) => body?.review?.observed_at ?? body?.review?.created_at,
	},
];

function text(value, fallback = '') {
	return String(value ?? fallback).trim();
}

function exchangeDate(value) {
	const date = new Date(value || Date.now());
	if (Number.isNaN(date.getTime())) return new Intl.DateTimeFormat('en-CA', { timeZone: 'Asia/Shanghai' }).format(new Date());
	return new Intl.DateTimeFormat('en-CA', { timeZone: 'Asia/Shanghai' }).format(date);
}

function hourPart(value) {
	const date = new Date(value || Date.now());
	if (Number.isNaN(date.getTime())) return '00';
	return new Intl.DateTimeFormat('en-GB', { timeZone: 'Asia/Shanghai', hour: '2-digit', hourCycle: 'h23' }).format(date);
}

function safeSegment(value, fallback = 'unknown') {
	const normalized = text(value, fallback).replace(/[^a-zA-Z0-9._-]+/g, '_').slice(0, 120);
	return normalized || fallback;
}

function stableBatchId(streamKey, firstOffset, lastOffset, sha256) {
	const hex = createHash('sha256').update(`${streamKey}|${firstOffset?.effective_at ?? ''}|${firstOffset?.observation_id ?? ''}|${lastOffset?.effective_at ?? ''}|${lastOffset?.observation_id ?? ''}|${sha256}`).digest('hex');
	return `${hex.slice(0, 8)}-${hex.slice(8, 12)}-5${hex.slice(13, 16)}-a${hex.slice(17, 20)}-${hex.slice(20, 32)}`;
}

function archivePath(root, bucket, observedAt, filename) {
	return `${root.replace(/\/$/, '')}/${safeSegment(bucket)}/${exchangeDate(observedAt)}/${hourPart(observedAt)}/${filename}`;
}

export function createBaiduPanMarketArchive({ baiduPan, ledger, quantServiceUrl, quantWriteApiKey = '', enabled = false, rawOverflowEnabled = false, rawCapabilities = DEFAULT_RAW_CAPABILITIES, intervalSeconds = 30, rootPath = DEFAULT_ROOT, rawRootPath = `${DEFAULT_ROOT}/raw-overflow`, fetchImpl = fetch, logger = console }) {
	const baseUrl = text(quantServiceUrl).replace(/\/$/, '');
	const intervalMs = Math.max(10, Math.min(300, Number(intervalSeconds) || 30)) * 1000;
	const archiveRoot = text(rootPath, DEFAULT_ROOT).replace(/\/$/, '') || DEFAULT_ROOT;
	const rawArchiveRoot = text(rawRootPath, `${DEFAULT_ROOT}/raw-overflow`).replace(/\/$/, '') || `${DEFAULT_ROOT}/raw-overflow`;
	const snapshotsEnabled = Boolean(enabled && baiduPan && ledger?.enqueueBaiduPanArchive && baseUrl);
	const rawStreams = [...new Set((Array.isArray(rawCapabilities) ? rawCapabilities : String(rawCapabilities || '').split(',')).map((value) => text(value)).filter(Boolean))].slice(0, 20);
	const rawEnabled = Boolean(rawOverflowEnabled && baiduPan && baseUrl && text(quantWriteApiKey) && rawStreams.length);
	const enabledFlag = snapshotsEnabled || rawEnabled;
	// Polling and uploading are separate activities. A slow cloud upload must
	// never suppress the next latest-snapshot read; the ledger is the durable
	// hand-off between the two lanes.
	let pollRunning = false;
	let drainRunning = false;
	let drainPromise = null;
	let lastPollAt = null;
	let lastSuccessAt = null;
	let lastError = null;
	let archivedInProcess = 0;
	let rawDrainRunning = false;
	let rawDrainPromise = null;
	let rawStreamIndex = 0;
	let rawBatchesInProcess = 0;
	let rawRowsInProcess = 0;
	let rawLastSuccessAt = null;
	let rawLastError = null;
	const directoryCache = new Set();

	async function fetchJson(path) {
		const response = await fetchImpl(`${baseUrl}${path}`, { headers: { accept: 'application/json' }, signal: AbortSignal.timeout(10_000) });
		let body;
		try { body = await response.json(); } catch { throw new Error(`量化服务归档读取返回无效 JSON（HTTP ${response.status}）`); }
		if (!response.ok) throw new Error(`量化服务归档读取失败（HTTP ${response.status}）`);
		return body;
	}

	async function fetchRaw(path, options = {}) {
		const response = await fetchImpl(`${baseUrl}${path}`, {
			...options,
			headers: { accept: 'application/json', 'X-Quant-Write-Key': text(quantWriteApiKey), ...(options.headers ?? {}) },
			signal: options.signal ?? AbortSignal.timeout(30_000),
		});
		let body;
		try { body = await response.json(); } catch { throw new Error(`原始溢出归档接口返回无效 JSON（HTTP ${response.status}）`); }
		if (!response.ok) throw new Error(`原始溢出归档接口失败（HTTP ${response.status}）`);
		return body;
	}

	async function ensureDirectory(path) {
		const normalized = text(path).replace(/\/+$/, '') || '/';
		if (directoryCache.has(normalized)) return;
		const parts = normalized.split('/').filter(Boolean);
		let current = '';
		for (const part of parts) {
			current += `/${part}`;
			if (directoryCache.has(current)) continue;
			const parent = current.slice(0, current.lastIndexOf('/')) || '/';
			let exists = false;
			try {
				const listed = await baiduPan.list({ dir: parent, limit: 1000 });
				exists = (listed.list ?? []).some((item) => item?.path === current && Number(item?.isdir) === 1);
			} catch { /* mkdir below remains the recovery path for protected app dirs */ }
			if (!exists) {
				try { await baiduPan.mkdir(current); }
				catch (error) {
					if (!/31066|already|exist|冲突/i.test(String(error?.message ?? error))) throw error;
				}
			}
			directoryCache.add(current);
		}
	}

	async function enqueueSnapshot(bucket, identity, observedAt, payload) {
		const serialized = JSON.stringify({ schema: 'market-realtime-archive-v1', bucket, observed_at: observedAt, exchange_date: exchangeDate(observedAt), ...payload });
		const bytes = Buffer.byteLength(serialized);
		if (bytes > MAX_SNAPSHOT_BYTES) throw new Error(`实时研究快照超过 ${Math.floor(MAX_SNAPSHOT_BYTES / 1024 / 1024)} MiB 上限`);
		const archiveKey = `market:${bucket}:${identity}`;
		const row = await ledger.enqueueBaiduPanArchive({ archiveKey, bucket, observedAt, exchangeDate: exchangeDate(observedAt), payload: JSON.parse(serialized) });
		return row ? { archiveId: row.archive_id, bucket, bytes, observedAt, payload: serialized } : null;
	}

	async function uploadJob(job) {
		const observedAt = job.observed_at ?? new Date().toISOString();
		const bucket = safeSegment(job.bucket, 'unknown');
		const directory = `${archiveRoot}/${bucket}/${exchangeDate(observedAt)}/${hourPart(observedAt)}`;
		await ensureDirectory(directory);
		const filename = `${safeSegment(job.archive_key, 'snapshot')}.json`;
		const remotePath = `${directory}/${filename}`;
		const content = Buffer.from(JSON.stringify(job.payload ?? {}));
		const result = await baiduPan.uploadReadable({ readable: Readable.from([content]), fileName: filename, size: content.length, remotePath });
		await ledger.completeBaiduPanArchive(job.archive_id, { remotePath: result.path ?? remotePath, remoteFsId: result.fsId ?? null });
		archivedInProcess += 1;
	}

	async function drain() {
		if (!snapshotsEnabled) return;
		if (drainPromise) return drainPromise;
		drainRunning = true;
		drainPromise = (async () => {
			try {
				const jobs = await ledger.claimBaiduPanArchives({ workerId: 'baidu-pan-market-archive', limit: 16, leaseSeconds: 300 });
				for (const job of jobs) {
					try { await uploadJob(job); }
					catch (error) { await ledger.failBaiduPanArchive(job.archive_id, { errorMessage: String(error?.message ?? error), retryable: true }); logger.warn(`百度网盘研究快照归档失败：${error?.message ?? error}`); }
				}
			} finally {
				drainRunning = false;
				drainPromise = null;
			}
		})();
		return drainPromise;
	}

	async function drainRawOverflow() {
		if (!rawEnabled) return;
		if (rawDrainPromise) return rawDrainPromise;
		rawDrainRunning = true;
		rawDrainPromise = (async () => {
			const capability = rawStreams[rawStreamIndex++ % rawStreams.length];
			const streamKey = `raw_market_observations:${capability}`;
			try {
				const batch = await fetchRaw(`/api/v1/internal/raw-overflow/next?stream_key=${encodeURIComponent(streamKey)}&limit=500`);
				if (batch?.status !== 'ready' || !Array.isArray(batch.rows) || !batch.rows.length) return;
				const lines = batch.rows.map((row) => JSON.stringify(row)).join('\n') + '\n';
				const content = gzipSync(Buffer.from(lines, 'utf8'));
				if (content.length <= 0 || content.length > MAX_RAW_BATCH_BYTES) throw new Error(`原始溢出批次超过 ${Math.floor(MAX_RAW_BATCH_BYTES / 1024 / 1024)} MiB 上限`);
				const sha256 = createHash('sha256').update(content).digest('hex');
				const observedAt = batch.last_offset?.effective_at ?? new Date().toISOString();
				// The ID/path is content-addressed. If the upload succeeds but the
				// ACK response is lost, a retry reuses the same remote object and
				// idempotency key instead of leaking a duplicate Baidu file.
				const batchId = stableBatchId(streamKey, batch.first_offset, batch.last_offset, sha256);
				const filename = `batch-${batchId}-${sha256.slice(0, 12)}.jsonl.gz`;
				const directory = `${rawArchiveRoot}/${safeSegment(capability, 'unknown')}/${exchangeDate(observedAt)}/${hourPart(observedAt)}`;
				await ensureDirectory(directory);
				const remotePath = `${directory}/${filename}`;
				const result = await baiduPan.uploadReadable({ readable: Readable.from([content]), fileName: filename, size: content.length, remotePath });
				await fetchRaw('/api/v1/internal/raw-overflow/ack', {
					method: 'POST',
					headers: { 'content-type': 'application/json' },
					body: JSON.stringify({
						batch_id: batchId, stream_key: streamKey, before_offset: batch.before_offset ?? null,
						first_offset: batch.first_offset, last_offset: batch.last_offset,
						row_count: Number(batch.row_count) || batch.rows.length, compressed_bytes: content.length,
						sha256, remote_path: result.path ?? remotePath, remote_fs_id: result.fsId ?? null,
					}),
				});
				rawBatchesInProcess += 1;
				rawRowsInProcess += Number(batch.row_count) || batch.rows.length;
				rawLastSuccessAt = new Date().toISOString();
				rawLastError = null;
			} catch (error) {
				rawLastError = String(error?.message ?? error).slice(0, 420);
				try { await fetchRaw('/api/v1/internal/raw-overflow/failure', { method: 'POST', headers: { 'content-type': 'application/json' }, body: JSON.stringify({ stream_key, error: rawLastError }) }); } catch { /* preserve original error */ }
				logger.warn(`百度网盘原始溢出批次归档失败：${rawLastError}`);
			} finally {
				rawDrainRunning = false;
				rawDrainPromise = null;
			}
		})();
		return rawDrainPromise;
	}

	async function poll() {
		if (!enabledFlag || pollRunning) return;
		pollRunning = true;
		lastPollAt = new Date().toISOString();
		try {
			if (snapshotsEnabled) {
				const results = await Promise.all(SNAPSHOT_SPECS.map(async (spec) => ({ spec, body: await fetchJson(spec.path) })));
				for (const { spec, body } of results) {
					const identity = text(spec.identity(body));
					if (!identity) continue;
					await enqueueSnapshot(spec.bucket, identity, spec.observedAt(body), { source: spec.source, data: body });
				}
			}
			if (snapshotsEnabled) lastSuccessAt = new Date().toISOString();
			lastError = null;
		} catch (error) {
			lastError = String(error?.message ?? error).slice(0, 420);
			logger.warn(`百度网盘研究快照轮询失败：${lastError}`);
		} finally {
			pollRunning = false;
			// Do not make the polling cadence wait on Baidu I/O. A later poll
			// (or this best-effort kick) continues draining the durable queue if
			// the current upload batch is still in flight.
			void drain().catch((error) => logger.warn(`百度网盘研究快照排空失败：${error?.message ?? error}`));
			void drainRawOverflow().catch((error) => logger.warn(`百度网盘原始溢出排空失败：${error?.message ?? error}`));
		}
	}

	return {
		enabled: enabledFlag,
		intervalMs,
		poll,
		drain,
		drainRawOverflow,
		status: async () => ({
			enabled: enabledFlag,
			running: pollRunning || drainRunning || rawDrainRunning,
			poll_running: pollRunning,
			drain_running: drainRunning,
			interval_seconds: intervalMs / 1000,
			last_poll_at: lastPollAt,
			last_success_at: lastSuccessAt,
			last_error: lastError,
			archived_in_process: archivedInProcess,
			raw_overflow: {
				enabled: rawEnabled, running: rawDrainRunning, capabilities: rawStreams,
				last_success_at: rawLastSuccessAt, last_error: rawLastError,
				batches_in_process: rawBatchesInProcess, rows_in_process: rawRowsInProcess,
				max_batch_bytes: MAX_RAW_BATCH_BYTES, root_path: rawArchiveRoot,
			},
			queue: ledger.baiduPanArchiveStatus ? await ledger.baiduPanArchiveStatus() : null,
			root_path: archiveRoot,
		}),
	};
}

export { archivePath };
