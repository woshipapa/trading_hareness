// Every local module the adapter imports must be in the image, or the image
// starts and dies on the first missing "./x.mjs".  This has shipped twice
// before (content-filter, the itougu relay modules); the Dockerfiles name
// their files explicitly, so the check is a set comparison, not a build.
import assert from 'node:assert/strict';
import fs from 'node:fs';
import path from 'node:path';
import test from 'node:test';
import { fileURLToPath } from 'node:url';

const here = path.dirname(fileURLToPath(import.meta.url));
const repo = path.resolve(here, '..');

function localImports(file, seen = new Set()) {
	const source = fs.readFileSync(file, 'utf8');
	for (const match of source.matchAll(/from\s+'\.\/([^']+\.mjs)'/g)) {
		const dep = path.join(path.dirname(file), match[1]);
		if (seen.has(dep)) continue;
		seen.add(dep);
		localImports(dep, seen);
	}
	return seen;
}

function copiedModules(dockerfile) {
	const text = fs.readFileSync(dockerfile, 'utf8').replace(/\\\n/g, ' ');
	return new Set([...text.matchAll(/feishu-adapter\/([A-Za-z0-9_-]+\.mjs)/g)].map((m) => m[1]));
}

const required = [...localImports(path.join(here, 'index.mjs'))].map((file) => path.basename(file)).sort();

for (const dockerfile of ['feishu-adapter/Dockerfile', 'deploy/feishu-relay-edge/Dockerfile.adapter']) {
	test(`${dockerfile} copies every module index.mjs reaches`, () => {
		const copied = copiedModules(path.join(repo, dockerfile));
		const missing = required.filter((name) => !copied.has(name));
		assert.deepEqual(missing, [], `missing from ${dockerfile}: ${missing.join(', ')}`);
	});
}
