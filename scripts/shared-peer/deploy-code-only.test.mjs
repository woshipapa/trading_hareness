import assert from 'node:assert/strict';
import { execFileSync, spawnSync } from 'node:child_process';
import { copyFileSync, mkdirSync, mkdtempSync, writeFileSync } from 'node:fs';
import { tmpdir } from 'node:os';
import { dirname, join } from 'node:path';
import { fileURLToPath } from 'node:url';

// Drive the dry run (no --apply, so no ssh) against a throwaway repository and
// check which changed paths ship, which are skipped and which force a full
// release.
const script = join(dirname(fileURLToPath(import.meta.url)), 'deploy-code-only.sh');
const repo = mkdtempSync(join(tmpdir(), 'deploy-code-only-test-'));
const git = (...args) => execFileSync('git', args, { cwd: repo, encoding: 'utf8' }).trim();

function write(path, text = `${path}\n`) {
	mkdirSync(join(repo, dirname(path)), { recursive: true });
	writeFileSync(join(repo, path), text);
}

function commit(paths, message) {
	for (const path of paths) write(path, `${path} ${message}\n`);
	git('add', '-A');
	git('commit', '-q', '-m', message);
	return git('rev-parse', 'HEAD');
}

git('init', '-q');
git('config', 'user.email', 'test@example.invalid');
git('config', 'user.name', 'test');
mkdirSync(join(repo, 'scripts/shared-peer'), { recursive: true });
copyFileSync(script, join(repo, 'scripts/shared-peer/deploy-code-only.sh'));
const base = commit(['quant-service/app/main.py', 'quant-service/entrypoint.py', 'README.md'], 'base');

function dryRun(target, from = base) {
	return spawnSync('bash', ['scripts/shared-peer/deploy-code-only.sh', target, 'test-release', '--from-sha', from], {
		cwd: repo, encoding: 'utf8',
	});
}

// A normal PR: app code plus its tests, docs and an edge change.
const normal = commit([
	'quant-service/app/main.py', 'quant-service/tests/test_main.py', 'docs/RUNBOOK.md', 'AGENTS.md',
	'feishu-relay/adapter/index.mjs', 'frontend/src/App.vue', '.github/workflows/verify.yml',
	'scripts/release-sync-status.test.mjs', 'scripts/release-sync-status.sh', 'scripts/windows/start.ps1',
	'scripts/deploy-feishu-relay-edge-release.sh',
], 'normal pr');
let result = dryRun(normal);
assert.equal(result.status, 0, result.stderr);
assert.match(result.stdout, /owner runtime files:\nquant-service\/app\/main\.py\n/);
for (const skipped of ['quant-service/tests/test_main.py', 'docs/RUNBOOK.md', 'AGENTS.md', 'feishu-relay/adapter/index.mjs',
	'frontend/src/App.vue', '.github/workflows/verify.yml', 'scripts/release-sync-status.test.mjs',
	'scripts/release-sync-status.sh', 'scripts/windows/start.ps1', 'scripts/deploy-feishu-relay-edge-release.sh']) {
	assert.ok(result.stdout.includes(`\n${skipped}\n`), `${skipped} should be listed as skipped`);
}
console.log('app code with its tests, docs and edge changes is a code-only release');

// Only edge and docs changed: the owner release just records the new SHA.
const edgeOnly = commit(['feishu-relay/bridge/bridge.py', 'docs/NOTES.md'], 'edge only');
result = dryRun(edgeOnly, normal);
assert.equal(result.status, 0, result.stderr);
assert.match(result.stdout, /owner runtime files:\n {2}\(none: this release only records the new SHA\)/);
console.log('an edge-only change still lets the owner record the new SHA');

// The exported standalone component's build files never reach the owner, which
// builds from Dockerfile.peer and requirements.txt.
const standalone = commit(['quant-service/compose.standalone.yaml', 'quant-service/Dockerfile.standalone',
	'quant-service/standalone/001-seed.sql', 'quant-service/requirements.lock', 'quant-service/Makefile',
	'quant-service/component.json', 'quant-service/scripts/run-release-path-tests.sh', 'quant-service/app/main.py'],
'standalone files');
result = dryRun(standalone, edgeOnly);
assert.equal(result.status, 0, result.stderr);
for (const skipped of ['quant-service/compose.standalone.yaml', 'quant-service/Dockerfile.standalone',
	'quant-service/standalone/001-seed.sql', 'quant-service/requirements.lock', 'quant-service/Makefile',
	'quant-service/component.json', 'quant-service/scripts/run-release-path-tests.sh']) {
	assert.ok(result.stdout.includes(`\n${skipped}\n`), `${skipped} should be listed as skipped`);
}
git('reset', '-q', '--hard', edgeOnly);
console.log('standalone build files are not owner runtime');

// Anything the image or the release checkout depends on needs a full release.
for (const path of ['quant-service/migrations/versions/x.py', 'quant-service/requirements.txt',
	'quant-service/Dockerfile.peer', 'deploy/shared-peer/compose.yaml', 'scripts/peer-session-guard.sh']) {
	const target = commit([path, 'quant-service/app/main.py'], `full ${path}`);
	result = dryRun(target, edgeOnly);
	assert.equal(result.status, 1, `${path} must refuse the fast path`);
	assert.match(result.stderr, new RegExp(`full release required for: ${path.replaceAll('.', '\\.')}`));
	git('reset', '-q', '--hard', edgeOnly);
}
console.log('migrations, dependencies, images, compose and guard scripts still need a full release');

// An applied migration whose comments or docstrings changed ships no schema
// change; a code change in it still does.
const migration = 'quant-service/migrations/versions/m1.py';
const q = '"""';
write(migration, `${q}Old note.${q}\nrevision = "m1"\ndown_revision = None\n\ndef upgrade():\n    ${q}Create t.${q}\n    op.execute("CREATE TABLE t()")  # keep\n`);
git('add', '-A');
git('commit', '-q', '-m', 'migration m1');
const withMigration = git('rev-parse', 'HEAD');
write(migration, `${q}New note.${q}\n# a comment\nrevision = "m1"\ndown_revision = None\n\ndef upgrade():\n    ${q}Create table t.${q}\n    op.execute("CREATE TABLE t()")\n`);
git('add', '-A');
git('commit', '-q', '-m', 'migration m1 docstring');
result = dryRun(git('rev-parse', 'HEAD'), withMigration);
assert.equal(result.status, 0, result.stderr);
assert.match(result.stdout, /quant-service\/migrations\/versions\/m1\.py \(comments or docstrings only\)/);
write(migration, `${q}New note.${q}\nrevision = "m1"\ndown_revision = None\n\ndef upgrade():\n    op.execute("CREATE TABLE t(id int)")\n`);
git('add', '-A');
git('commit', '-q', '-m', 'migration m1 ddl');
result = dryRun(git('rev-parse', 'HEAD'), withMigration);
assert.equal(result.status, 1);
assert.match(result.stderr, /full release required for: quant-service\/migrations\/versions\/m1\.py/);
console.log('a docstring-only migration edit is skipped; a DDL edit still needs a full release');

result = dryRun(base, base);
assert.equal(result.status, 1);
assert.match(result.stderr, /target SHA has no changes/);
console.log('an identical SHA is refused');
