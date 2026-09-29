import assert from 'node:assert/strict';
import { createHash } from 'node:crypto';
import { execFileSync, spawnSync } from 'node:child_process';
import { chmodSync, mkdtempSync, readFileSync, writeFileSync } from 'node:fs';
import { tmpdir } from 'node:os';
import { join } from 'node:path';

// Drive the read-only inventory with a fake ssh that answers as each host
// would, so the comparison logic is exercised without touching either host.
const sha = execFileSync('git', ['rev-parse', 'HEAD'], { encoding: 'utf8' }).trim();
const packageHash = createHash('sha256').update(readFileSync('feishu-relay/adapter/package.json')).digest('hex');
const heads = execFileSync('python3', ['-c', `
import pathlib, re
revisions, parents = set(), set()
for path in pathlib.Path('quant-service/migrations/versions').glob('*.py'):
    text = path.read_text(encoding='utf-8')
    revision = re.search(r'^revision(?:\\s*:[^=\\n]+)?\\s*=\\s*"([^"]+)"', text, re.M)
    down = re.search(r'^down_revision(?:\\s*:[^=\\n]+)?\\s*=\\s*(.+)$', text, re.M)
    if revision:
        revisions.add(revision.group(1))
    if down:
        parents.update(re.findall(r'"([^"]+)"', down.group(1)))
print(",".join(sorted(revisions - parents)))
`], { encoding: 'utf8' }).trim();

function run({ edge, owner }) {
	const dir = mkdtempSync(join(tmpdir(), 'release-sync-test-'));
	writeFileSync(join(dir, 'edge.out'), edge);
	writeFileSync(join(dir, 'owner.out'), owner);
	writeFileSync(join(dir, 'key'), 'not a real key');
	const fakeSsh = join(dir, 'ssh');
	writeFileSync(fakeSsh, `#!/usr/bin/env bash
cat > /dev/null
case " $* " in *" -p 3535 "*) cat "${dir}/owner.out" ;; *) cat "${dir}/edge.out" ;; esac
`);
	chmodSync(fakeSsh, 0o755);
	return spawnSync('bash', ['scripts/release-sync-status.sh', '--sha', sha], {
		encoding: 'utf8',
		env: { ...process.env, PATH: `${dir}:${process.env.PATH}`, RELAY_EDGE_SSH_KEY: join(dir, 'key'), OWNER_PEER_SSH_KEY: join(dir, 'key') },
	});
}

const health = (release, extra = {}) => JSON.stringify({ status: 'ok', build: { git_sha: sha, release }, ...extra });
const convergedEdge = [
  `@adapter_health ${health('edge-2026.09.26-sync')}`,
  `@bridge_health ${JSON.stringify({ status: 'ok', release: `hotfix-20260926T010203Z-${sha.slice(0, 12)}-4242`, runtime_source: 'source-overlay' })}`,
  '@adapter_image_id sha256:base-image-id',
  `@adapter_package_hash ${packageHash}`,
	`@xhs_health ${JSON.stringify({ status: 'ok', collector: 'Spider_XHS', cookie_configured: true, release: { xhs_git_sha: 'ebb6c4fbeaedf1237190ebc0c8e3ae8b7ddd030e', xhs_source_tree_sha256: 'a'.repeat(64) } })}`,
	`@xhs_manifest ${JSON.stringify({ component: 'edge-xhs', xhs_source_dirty: false })}`,
	`@hotfix_current releases/hotfix-20260926T010203Z-${sha.slice(0, 12)}-4242`,
	`@runtime_env FEISHU_ADAPTER_IMAGE=ghcr.io/woshipapa/trading-hareness-feishu-adapter:${sha}`,
	'@runtime_env FEISHU_ADAPTER_HOTFIX_ENABLED=false',
	'@retired_quant inactive disabled',
].join('\n');
const convergedOwner = [
	'@release_link /home/stockpeer/.local/share/trading-hareness/releases/x/trading_hareness',
	`@main_health ${health('owner-2026.09.26-sync')}`,
	`@scheduler_health ${health('owner-2026.09.26-sync')}`,
	'@main_profile intraday_edge',
	`@alembic ${heads}`,
	'@intraday_secrets present',
].join('\n');

const ok = run({ edge: convergedEdge, owner: convergedOwner });
assert.equal(ok.status, 0, ok.stdout + ok.stderr);
assert.match(ok.stdout, /ALL CHECKS PASSED/);
console.log('both hosts on the expected sha pass every check');

const overlayEdge = convergedEdge
	.replace(`@adapter_health ${health('edge-2026.09.26-sync')}`,
		`@adapter_health ${health(`hotfix-20260926T010203Z-${sha.slice(0, 12)}-4242`, { runtime_source: 'source-overlay' })}`)
	.replace(`FEISHU_ADAPTER_IMAGE=ghcr.io/woshipapa/trading-hareness-feishu-adapter:${sha}`,
		'FEISHU_ADAPTER_IMAGE=ghcr.io/woshipapa/trading-hareness-feishu-adapter:base-runtime')
	.replace('FEISHU_ADAPTER_HOTFIX_ENABLED=false', 'FEISHU_ADAPTER_HOTFIX_ENABLED=true');
const overlayOk = run({ edge: overlayEdge, owner: convergedOwner });
assert.equal(overlayOk.status, 0, overlayOk.stdout + overlayOk.stderr);
assert.match(overlayOk.stdout, /source-overlay/);
console.log('a clean source overlay reusing an older base image passes every check');

const drifted = run({
	edge: convergedEdge
		.replace(`@adapter_health ${health('edge-2026.09.26-sync')}`,
			`@adapter_health ${health('hotfix-x', { runtime_source: 'source-overlay' })}`)
		.replace('FEISHU_ADAPTER_HOTFIX_ENABLED=false', 'FEISHU_ADAPTER_HOTFIX_ENABLED=true')
		.replace(`-${sha.slice(0, 12)}-4242`, `-${sha.slice(0, 12)}-dirty-4242`)
		.replace('@retired_quant inactive disabled', '@retired_quant active enabled'),
	owner: convergedOwner
		.replace('@main_profile intraday_edge', '@main_profile research')
		.replace(`@alembic ${heads}`, '@alembic 20260918_0105')
		.replace('@intraday_secrets present', '@intraday_secrets missing'),
});
assert.equal(drifted.status, 1);
for (const check of ['adapter release', 'bridge release',
	'retired quant-intraday-edge', 'main runtime profile', 'alembic revision', 'intraday-secrets.env']) {
	assert.match(drifted.stdout, new RegExp(`${check}\\s+\\S.*FAIL`), `${check} should fail:\n${drifted.stdout}`);
}
console.log('overlay, dirty bridge, a live retired writer, research profile, unknown alembic lineage and missing secrets all fail');

const unreachable = run({ edge: '', owner: '' });
assert.equal(unreachable.status, 1);
console.log('a host that returns nothing is a failure, not a pass');
