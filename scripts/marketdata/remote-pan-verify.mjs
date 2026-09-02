#!/usr/bin/env node
import { createHash } from 'node:crypto';
import { createWriteStream } from 'node:fs';
import { stat, readFile, rm } from 'node:fs/promises';
import { pipeline } from 'node:stream/promises';
import { spawn } from 'node:child_process';

const [dir, filename, manifestName] = process.argv.slice(2);
if (!dir || !filename || !manifestName || process.argv.length !== 5) throw new Error('usage: remote-pan-verify.mjs <dir> <data-file> <manifest-file>');
const [{ createLedger }, { createBaiduPanStorage }] = await Promise.all([import('/app/ledger.mjs'), import('/app/baidu-pan-storage.mjs')]);
const password = process.env.PGPASSWORD || process.env.POSTGRES_PASSWORD;
const cs = `postgresql://${encodeURIComponent(process.env.PGUSER || 'n8n')}:${encodeURIComponent(password)}@${process.env.PGHOST || '127.0.0.1'}:${process.env.PGPORT || '5432'}/${encodeURIComponent(process.env.PGDATABASE || 'n8n')}`;
const pan = createBaiduPanStorage({ appKey: process.env.BAIDU_PAN_APP_KEY, secretKey: process.env.BAIDU_PAN_SECRET_KEY, redirectUri: process.env.BAIDU_PAN_REDIRECT_URI || 'oob', ledger: createLedger(cs), rootPath: '/' });
const listed = await pan.list({ dir, limit: 1000 });
const find = (name) => (listed.list ?? []).find((x) => x?.server_filename === name && Number(x?.isdir) !== 1);
const data = find(filename); const manifest = find(manifestName);
if (!data || !manifest) throw new Error('data or manifest file not found');
const mresp = await pan.download(manifest.fs_id); const chunks=[]; for await (const c of mresp.body) chunks.push(Buffer.from(c));
const expected = JSON.parse(Buffer.concat(chunks).toString('utf8'));
const tmp = `/tmp/verify-${process.pid}.gz`; const resp = await pan.download(data.fs_id); await pipeline(resp.body, createWriteStream(tmp));
const h = createHash('sha256'); const bytes = Number((await stat(tmp)).size); const file = await readFile(tmp); h.update(file);
const gunzip = spawn('gzip', ['-t', tmp]); const code = await new Promise((resolve) => gunzip.on('close', resolve));
await rm(tmp, { force: true });
const actualSha = h.digest('hex');
const ok = bytes === Number(expected.bytes) && actualSha === expected.sha256 && code === 0 && Number(data.size) === bytes;
console.log(JSON.stringify({ ok, bytes, expected_bytes: expected.bytes, sha256: actualSha, rows: expected.rows, fs_id: data.fs_id, remote_size: data.size, gzip_ok: code === 0 }));
if (!ok) process.exitCode = 1;
