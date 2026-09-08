import assert from 'node:assert/strict';
import { readFileSync } from 'node:fs';

const source = readFileSync('scripts/hotfix-feishu-relay-edge.sh', 'utf8');

// The hotfix path must never publish or reference GHCR: it exists precisely
// so a small change does not need a CI build, a registry push and an image
// transfer just to try it on the edge.
assert.doesNotMatch(source, /ghcr\.io/);
assert.match(source, /docker compose --env-file "\$runtime_env" --env-file "\$secrets_env" build feishu-adapter/);
assert.match(source, /rsync -az --delete -e "\$\{rsync_ssh\[\*\]\}" \\\n {4}--exclude 'node_modules' --exclude '\*\.test\.mjs'/);
assert.match(source, /deploy\/feishu-relay-edge\/Dockerfile\.adapter/);

// A hotfix build must be unmistakably untracked: it clears any pinned GHCR
// image and stamps a synthetic "local-..." sha, never a real release label.
assert.match(source, /sed -i '\/\^FEISHU_ADAPTER_IMAGE=\/d' "\$runtime_env"/);
assert.match(source, /local_sha="local-/);
assert.match(source, /APP_RELEASE hotfix/);

// It must run the suite before syncing anything untested to the edge.
assert.match(source, /node --test \*\.test\.mjs/);

console.log('the edge hotfix path builds locally on the edge and never touches GHCR');
