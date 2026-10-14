import assert from 'node:assert/strict';
import { execFileSync, spawnSync } from 'node:child_process';
import { copyFileSync, mkdirSync, mkdtempSync, writeFileSync } from 'node:fs';
import { tmpdir } from 'node:os';
import { dirname, join } from 'node:path';
import { fileURLToPath } from 'node:url';

// Drive the dry run (no --apply; RELEASE_DB_REVISION stands in for the owner's
// database, so no ssh: the schema gate runs on every release) against a throwaway repository and check which changed
// paths ship, which are skipped and which refuse the release, and why.
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
copyFileSync(join(dirname(script), 'classify-owner-paths.sh'), join(repo, 'scripts/shared-peer/classify-owner-paths.sh'));
copyFileSync(join(dirname(script), 'schema-gate.sh'), join(repo, 'scripts/shared-peer/schema-gate.sh'));
copyFileSync(join(dirname(script), 'owner-host.sh'), join(repo, 'scripts/shared-peer/owner-host.sh'));
mkdirSync(join(repo, 'quant-service/scripts'), { recursive: true });
copyFileSync(join(dirname(script), '../../quant-service/scripts/migration_lineage.py'), join(repo, 'quant-service/scripts/migration_lineage.py'));
// The catalog says which host runs each script; the classifier reads it from the commits.
const catalogRow = ([name, host]) => `| \`${name}\` | platform | ${host} | test |`;
write('scripts/CATALOG.md', ['| Script | Owner | Runs on | Purpose |', '| --- | --- | --- | --- |', ...[
	['release-sync-status.sh', 'workstation'], ['windows/start.ps1', 'owner-windows'],
	['deploy-feishu-relay-edge-release.sh', 'workstation'], ['teacher_cycle.py', 'workstation'],
	['teacher_review_daily.py', 'workstation'], ['teacher_harness.py', 'workstation'], ['tdx-promote.py', 'workstation'],
	['licensed_minute_history_fetch.py', 'workstation'], ['peer-session-guard.sh', '47owner'],
].map(catalogRow)].join('\n') + '\n');
const migration = 'quant-service/migrations/versions/m1.py';
const q = '"""';
write(migration, `${q}Old note.${q}\nrevision = "m1"\ndown_revision = None\n\ndef upgrade():\n    ${q}Create t.${q}\n    op.execute("CREATE TABLE t()")  # keep\n`);
git('add', '-A');
git('commit', '-q', '-m', 'migration m1');
const base = commit(['quant-service/app/main.py', 'quant-service/entrypoint.py', 'README.md'], 'base');

function dryRun(target, from = base, env = {}) {
	return spawnSync('bash', ['scripts/shared-peer/deploy-code-only.sh', target, 'test-release', '--from-sha', from], {
		cwd: repo, encoding: 'utf8', env: { ...process.env, RELEASE_DB_REVISION: 'm1', ...env },
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

// The teacher cycle runs on the operator workstation.
const teacher = commit(['scripts/teacher_cycle.py', 'scripts/teacher_review_daily.py', 'scripts/teacher_harness.py',
	'quant-service/app/main.py'], 'teacher cycle');
result = dryRun(teacher, edgeOnly);
assert.equal(result.status, 0, result.stderr);
for (const skipped of ['scripts/teacher_cycle.py', 'scripts/teacher_review_daily.py', 'scripts/teacher_harness.py']) {
	assert.ok(result.stdout.includes(`\n${skipped}\n`), `${skipped} should be listed as skipped`);
}
git('reset', '-q', '--hard', edgeOnly);
console.log('the workstation teacher cycle is not owner runtime');

const tdxPromotion = commit(['scripts/tdx-promote.py', 'scripts/test_tdx_promote.py'], 'tdx promotion tool');
result = dryRun(tdxPromotion, edgeOnly);
assert.equal(result.status, 0, result.stderr);
for (const skipped of ['scripts/tdx-promote.py', 'scripts/test_tdx_promote.py']) {
	assert.ok(result.stdout.includes(`\n${skipped}\n`), `${skipped} should be listed as skipped`);
}
git('reset', '-q', '--hard', edgeOnly);
console.log('the TDX promotion tool is workstation-only and its test follows the existing test rule');

const licensedFetch = commit(['scripts/licensed_minute_history_fetch.py', 'scripts/test_licensed_minute_history_fetch.py'], 'licensed fetch tool');
result = dryRun(licensedFetch, edgeOnly);
assert.equal(result.status, 0, result.stderr);
for (const skipped of ['scripts/licensed_minute_history_fetch.py', 'scripts/test_licensed_minute_history_fetch.py']) {
	assert.ok(result.stdout.includes(`\n${skipped}\n`), `${skipped} should be listed as skipped`);
}
git('reset', '-q', '--hard', edgeOnly);
console.log('the licensed batch fetch tool is workstation-only and its test follows the existing test rule');

// What only the full release can ship: the host's own tree (compose, units,
// the scripts the host runs) and the inputs of the image.
for (const [path, reason] of [
	['deploy/shared-peer/compose.yaml', 'full release required for'], ['scripts/peer-session-guard.sh', 'full release required for'],
	['quant-service/requirements.txt', 'image build required for'], ['quant-service/Dockerfile.peer', 'image build required for'],
	['.dockerignore', 'image build required for'], ['deploy/shared-peer/ssh-tunnel-entrypoint.sh', 'tunnel image input'],
]) {
	const target = commit([path, 'quant-service/app/main.py'], `refused ${path}`);
	result = dryRun(target, edgeOnly);
	assert.equal(result.status, 1, `${path} must refuse the fast path`);
	assert.match(result.stderr, new RegExp(`${reason}: ${path.replaceAll('.', '\\.')}`));
	git('reset', '-q', '--hard', edgeOnly);
}
console.log('compose and host scripts need the full release; dependencies and the Dockerfile need the image build; a tunnel image input has no release');

// A path no rule names is not an image build: the release asks for its rule.
const unknown = commit(['newdir/thing.txt', 'scripts/new-tool.py', 'quant-service/app/main.py'], 'unknown paths');
result = dryRun(unknown, edgeOnly);
assert.equal(result.status, 1);
assert.match(result.stderr, /unclassified path: newdir\/thing\.txt \(add its rule to scripts\/shared-peer\/classify-owner-paths\.sh/);
assert.match(result.stderr, /unclassified path: scripts\/new-tool\.py/);
assert.doesNotMatch(result.stderr, /full release required/);
git('reset', '-q', '--hard', edgeOnly);
console.log('an unknown path and a script without a catalog row are refused as unclassified, not sent to a full release');

// An applied migration whose comments or docstrings changed ships no schema
// change. A migration whose code changed ships in the overlay once the owner's
// database is at the release's head, and is refused while it is behind.
const withMigration = git('rev-parse', 'HEAD');
write(migration, `${q}New note.${q}\n# a comment\nrevision = "m1"\ndown_revision = None\n\ndef upgrade():\n    ${q}Create table t.${q}\n    op.execute("CREATE TABLE t()")\n`);
git('add', '-A');
git('commit', '-q', '-m', 'migration m1 docstring');
result = dryRun(git('rev-parse', 'HEAD'), withMigration);
assert.equal(result.status, 0, result.stderr);
assert.match(result.stdout, /quant-service\/migrations\/versions\/m1\.py \(comments or docstrings only\)/);
assert.match(result.stdout, /schema: owner database m1, release head m1: at_head/);
assert.doesNotMatch(result.stdout, /migrations in the overlay/);
write('quant-service/migrations/versions/m2.py', `revision = "m2"\ndown_revision = "m1"\n\ndef upgrade():\n    op.execute("ALTER TABLE t ADD COLUMN id int")\n`);
git('add', '-A');
git('commit', '-q', '-m', 'migration m2');
const withSecond = git('rev-parse', 'HEAD');
result = dryRun(withSecond, withMigration, { RELEASE_DB_REVISION: 'm2' });
assert.equal(result.status, 0, result.stderr);
assert.match(result.stdout, /schema: owner database m2, release head m2: at_head/);
assert.match(result.stdout, /migrations in the overlay \(the schema gate passed: see the schema: line\):\nquant-service\/migrations\/versions\/m2\.py\n/);
result = dryRun(withSecond, withMigration, { RELEASE_DB_REVISION: 'm1' });
assert.equal(result.status, 6);
assert.match(result.stderr, /the owner database is at m1 but this release needs m2/);
result = dryRun(withSecond, withMigration, { RELEASE_DB_REVISION: '' });
assert.equal(result.status, 6);
assert.match(result.stderr, /cannot read the owner's database revision/);
result = dryRun(withSecond, withMigration, { RELEASE_DB_REVISION: '', RELEASE_ALLOW_SCHEMA_REVISION: 'none' });
assert.equal(result.status, 0, result.stderr);
assert.match(result.stdout, /schema: owner database unreadable, release head m2: unverified \(allowed\)/);
assert.doesNotMatch(result.stdout, /is at their head/);
console.log('a docstring-only migration edit is skipped; a new migration ships once the owner database is at its head');

// The gate looks at the state, not at the diff: a release whose diff carries no migration is still refused
// while the owner's database is behind its head (a wrong --from-sha, an earlier override, a restored database).
for (const [path, kind] of [['quant-service/alembic.ini', 'ini'], ['quant-service/migrations/env.py', 'env']]) {
	const target = commit([path], `alembic ${kind}`);
	result = dryRun(target, withSecond, { RELEASE_DB_REVISION: 'm2' });
	assert.equal(result.status, 0, result.stderr);
	const shipped = result.stdout.split('migrations in the overlay')[1]?.split('not owner runtime')[0] ?? '';
	assert.ok(shipped.includes(`\n${path}\n`), `${path} ships as a migration file`);
}
const afterSecond = commit(['quant-service/app/main.py'], 'app after m2');
result = dryRun(afterSecond, withSecond, { RELEASE_DB_REVISION: 'm1' });
assert.equal(result.status, 6);
assert.match(result.stderr, /the owner database is at m1 but this release needs m2/);
console.log('the schema gate runs on every release; alembic.ini and env.py ship as migration files');

result = dryRun(base, base);
assert.equal(result.status, 1);
assert.match(result.stderr, /target SHA has no changes/);
console.log('an identical SHA is refused');
