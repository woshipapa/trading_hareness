import assert from 'node:assert/strict';
import { readFileSync } from 'node:fs';

const source = readFileSync('scripts/deploy-feishu-relay-edge-release.sh', 'utf8');
assert.match(source, /git -C "\$repo_root" rev-parse --verify "\$\{release_sha\}\^\{commit\}"/);
assert.match(source, /trading-hareness-feishu-adapter:\$\{release_sha\}/);
assert.match(source, /RELAY_EDGE_IMAGE_PULL_TIMEOUT_SECONDS:-300/);
assert.match(source, /timeout "\$\{pull_timeout_seconds\}s" docker pull/);
console.log('relay release resolves abbreviated SHAs to immutable image tags');

// GHCR blob delivery to the edge host has stalled mid-pull more than once;
// a release must recover on its own instead of needing a manual local
// pull + `docker save | ssh docker load` every time it happens.
assert.match(source, /if ! "\$\{ssh_command\[@\]\}" "\$edge_host" bash -s -- "\$image_ref" "\$pull_timeout_seconds"/);
assert.match(source, /docker save "\$image_ref" \| gzip -1 \| "\$\{ssh_command\[@\]\}" "\$edge_host" 'gunzip \| docker load'/);
assert.match(source, /docker image inspect "\$image_ref" >\/dev\/null/);
console.log('a stalled edge-side pull falls back to a local pull and ssh transfer automatically');

// The hotfix overlay reports whatever APP_GIT_SHA says, so a pinned release
// has to switch the overlay off and then prove the adapter is not running it.
assert.match(source, /update_env FEISHU_ADAPTER_HOTFIX_ENABLED false/);
assert.match(source, /"runtime_source":"source-overlay"/);
console.log('a pinned image release disables the source overlay and verifies it is off');
