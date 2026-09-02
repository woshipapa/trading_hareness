#!/usr/bin/env node

// Upload one bounded archive file from inside the edge adapter container.
// The container owns the Baidu OAuth ledger and credentials; this helper never
// accepts or prints credentials and streams only a caller-selected file.
import { createReadStream } from 'node:fs';
import { stat } from 'node:fs/promises';
import { pathToFileURL } from 'node:url';

// Baidu's create/superfile endpoints can take longer than the adapter's
// ordinary 30-second HTTP budget for 100+ MiB evidence files.  This helper is
// an isolated archive process, so extend only its request signals.
const defaultTimeout = AbortSignal.timeout.bind(AbortSignal);
AbortSignal.timeout = (milliseconds) => defaultTimeout(Math.max(Number(milliseconds) || 0, 120_000));

const [{ createLedger }, { createBaiduPanStorage }] = await Promise.all([
	import('/app/ledger.mjs'),
	import('/app/baidu-pan-storage.mjs'),
]);

const [file, remotePath] = process.argv.slice(2);
if (!file || !remotePath || process.argv.length !== 4) {
	console.error('usage: remote-pan-upload.mjs <local-file> <remote-path>');
	process.exitCode = 2;
} else {
	const size = (await stat(file)).size;
	if (!Number.isSafeInteger(size) || size <= 0 || size > 480 * 1024 * 1024) {
		throw new Error('archive part must be between 1 byte and 480 MiB');
	}
	const password = process.env.PGPASSWORD || process.env.POSTGRES_PASSWORD;
	if (!password) throw new Error('database credential is not configured in adapter');
	const connectionString = `postgresql://${encodeURIComponent(process.env.PGUSER || 'n8n')}:${encodeURIComponent(password)}@${process.env.PGHOST || '127.0.0.1'}:${process.env.PGPORT || '5432'}/${encodeURIComponent(process.env.PGDATABASE || 'n8n')}`;
	const ledger = createLedger(connectionString);
	const pan = createBaiduPanStorage({
		appKey: process.env.BAIDU_PAN_APP_KEY,
		secretKey: process.env.BAIDU_PAN_SECRET_KEY,
		redirectUri: process.env.BAIDU_PAN_REDIRECT_URI || 'oob',
		ledger,
		rootPath: '/',
	});
	const normalized = remotePath.replace(/\/+/g, '/');
	const directory = normalized.slice(0, normalized.lastIndexOf('/')) || '/';
	let current = '';
	for (const segment of directory.split('/').filter(Boolean)) {
		current += `/${segment}`;
		try {
			const parent = current.slice(0, current.lastIndexOf('/')) || '/';
			const listed = await pan.list({ dir: parent, limit: 1000 });
			const exists = (listed.list ?? []).some((item) => item?.path === current && Number(item?.isdir) === 1);
			if (!exists) await pan.mkdir(current);
		} catch (error) {
			if (!/already|exist|冲突|31066/i.test(String(error?.message ?? error))) throw error;
		}
	}
	const filename = normalized.split('/').pop();
	const listed = await pan.list({ dir: directory, limit: 1000 });
	const existing = (listed.list ?? []).find((item) => item?.server_filename === filename && Number(item?.isdir) !== 1);
	if (existing) {
		if (Number(existing.size) !== size) throw new Error(`remote file exists with different size: ${remotePath}`);
		console.log(JSON.stringify({ path: remotePath, fs_id: existing.fs_id ?? null, size, skipped: true }));
		process.exit(0);
	}
	const result = await pan.uploadReadable({
		readable: createReadStream(file),
		fileName: remotePath.split('/').pop(),
		size,
		remotePath,
	});
	console.log(JSON.stringify({ path: result.path ?? remotePath, fs_id: result.fsId ?? null, size }));
}
