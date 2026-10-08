import assert from 'node:assert/strict';
import { readFileSync } from 'node:fs';
import { fileURLToPath } from 'node:url';

const source = readFileSync(fileURLToPath(new URL('./hotfix-feishu-relay-edge.sh', import.meta.url)), 'utf8');
const compose = readFileSync(fileURLToPath(new URL('../../deploy/edge/docker-compose.yml', import.meta.url)), 'utf8');

// The hotfix path must never publish, pull or build an image: it exists
// precisely so a small source change can reuse the verified edge runtime.
assert.doesNotMatch(source, /ghcr\.io/);
assert.match(source, /--no-build --pull never --force-recreate --no-deps feishu-adapter/);
assert.match(source, /FEISHU_ADAPTER_HOTFIX_ENABLED true/);
assert.match(source, /FEISHU_ADAPTER_HOTFIX_DIR/);
assert.match(source, /project_root\/bridge/);
assert.match(source, /bridge\/source_filter\.py/);
assert.match(source, /larkagentx-group-relay.service/);
assert.match(source, /supervisor\/\.venv\/bin\/python/);
assert.match(source, /larkagentx-bridge-entrypoint\.sh/);
assert.match(source, /LARKX_BRIDGE_HOTFIX_ROOT/);
assert.match(source, /flock -w 120 9/);
assert.match(source, /compose_backup=/);
assert.match(source, /runtime_committed=true/);
assert.match(source, /chown --reference=\"\$runtime_env\"/);
assert.match(source, /runtime_source.*source-overlay/);
assert.match(source, /current\.next/);
assert.match(source, /dependency_fields_match\(\)/);
assert.match(source, /dependencies.*devDependencies.*optionalDependencies/s);
assert.match(source, /base_image_id=/);
assert.doesNotMatch(source, /docker compose[^\n]*build/);
assert.match(source, /rsync -az --delete --safe-links -e "\$\{rsync_ssh\[\*\]\}"/);

// A hotfix is explicitly marked as an overlay and keeps the base image intact
// so a failed candidate can be rolled back without a registry operation.
assert.match(source, /APP_RELEASE "hotfix-\$release_id"/);
assert.doesNotMatch(source, /FEISHU_ADAPTER_IMAGE=\/d/);
assert.match(source, /hotfix health verification failed; previous runtime restored/);

// It must run the suite before syncing anything untested to the edge.
assert.match(source, /node --test \*\.test\.mjs/);
assert.match(source, /npm run build/);
assert.match(source, /project_root\/dashboard/);
assert.match(source, /frontend-dist/);
assert.match(source, /feishu-relay-dashboard\.conf/);
assert.match(source, /nginx -t/);
assert.match(source, /systemctl reload nginx/);
assert.match(source, /unittest discover/);
assert.match(compose, /FEISHU_ADAPTER_HOTFIX_ENABLED/);
assert.match(compose, /:\/app\/hotfix:ro/);
assert.match(compose, /FEISHU_ADAPTER_SOURCE_MODE=source-overlay/);
assert.match(readFileSync(fileURLToPath(new URL('../../deploy/edge/larkagentx-bridge-entrypoint.sh', import.meta.url)), 'utf8'), /current\/bridge\/bridge\.py/);
assert.match(readFileSync(fileURLToPath(new URL('../../deploy/edge/larkagentx-group-relay-hotfix.conf', import.meta.url)), 'utf8'), /bridge-entrypoint\.sh/);

console.log('the edge hotfix path uses a validated source overlay and never builds or pulls an image');
