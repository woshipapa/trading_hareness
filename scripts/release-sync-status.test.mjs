import assert from 'node:assert/strict';
import { execFileSync, spawnSync } from 'node:child_process';
import { chmodSync, mkdtempSync, writeFileSync } from 'node:fs';
import { tmpdir } from 'node:os';
import { join } from 'node:path';

// Drive the read-only inventory with a fake ssh that answers as each host
// would, so the comparison logic is exercised without touching either host.
const sha = execFileSync('git', ['rev-parse', 'HEAD'], { encoding: 'utf8' }).trim();
const heads = execFileSync('python3', ['-c', `
import ast, pathlib
revisions, parents = set(), set()
for path in pathlib.Path('quant-service/migrations/versions').glob('*.py'):
    tree = ast.parse(path.read_text(encoding='utf-8'), filename=str(path))
    values = {}
    for node in tree.body:
        target = node.targets[0] if isinstance(node, ast.Assign) and len(node.targets) == 1 else node.target if isinstance(node, ast.AnnAssign) else None
        if isinstance(target, ast.Name) and target.id in {'revision', 'down_revision'}:
            try:
                values[target.id] = ast.literal_eval(node.value)
            except (ValueError, SyntaxError):
                pass
    revision = values.get('revision')
    if revision:
        revisions.add(str(revision))
    down = values.get('down_revision')
    if isinstance(down, (tuple, list)):
        parents.update(str(value) for value in down if value)
    elif down:
        parents.add(str(down))
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
for (const check of ['adapter release', 'adapter runtime_source', 'runtime.env overlay', 'bridge release',
	'retired quant-intraday-edge', 'main runtime profile', 'alembic revision', 'intraday-secrets.env']) {
	assert.match(drifted.stdout, new RegExp(`${check}\\s+\\S.*FAIL`), `${check} should fail:\n${drifted.stdout}`);
}
console.log('overlay, dirty bridge, a live retired writer, research profile, unknown alembic lineage and missing secrets all fail');

const unreachable = run({ edge: '', owner: '' });
assert.equal(unreachable.status, 1);
console.log('a host that returns nothing is a failure, not a pass');
