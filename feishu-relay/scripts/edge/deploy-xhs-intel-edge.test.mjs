import assert from 'node:assert/strict';
import { readFileSync } from 'node:fs';
import { fileURLToPath } from 'node:url';

const source = readFileSync(fileURLToPath(new URL('./deploy-xhs-intel-edge.sh', import.meta.url)), 'utf8');
const compose = readFileSync(fileURLToPath(new URL('../../deploy/edge/docker-compose.yml', import.meta.url)), 'utf8');

assert.match(source, /scp -q -i "\$edge_key"/);
assert.doesNotMatch(source, /scp -q "\$\{ssh_command\[@\]\}"/);
assert.match(source, /XHS_COOKIE_SOURCE/);
assert.match(source, /a1/);
assert.match(source, /web_session/);
assert.match(source, /XHS_FEISHU_WEBHOOK_URL must be set/);
assert.match(source, /XHS_FORCE_BUILD/);
assert.match(source, /docker image inspect feishu-relay-edge-xhs:local/);
assert.match(source, /docker compose --env-file "\$runtime_env" --env-file "\$secrets_env" build xhs-collector/);
assert.match(source, /N8N_RUNNERS_AUTH_TOKEN/);
assert.match(source, /n8n-runners/);
assert.match(source, /status='crashed'/);
assert.match(compose, /N8N_RUNNERS_MODE: external/);
assert.match(compose, /container_name: feishu-relay-edge-n8n-runners/);
assert.match(compose, /N8N_RUNNERS_TASK_BROKER_URI: http:\/\/127\.0\.0\.1:5679/);

console.log('XHS deployment validates the private Cookie and provisions an external n8n task runner');
