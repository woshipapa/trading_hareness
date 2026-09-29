import assert from 'node:assert/strict';
import { readFileSync } from 'node:fs';
import { fileURLToPath } from 'node:url';

const source = readFileSync(fileURLToPath(new URL('./deploy-xhs-intel-edge.sh', import.meta.url)), 'utf8');
const compose = readFileSync(fileURLToPath(new URL('../../deploy/edge/docker-compose.yml', import.meta.url)), 'utf8');
const workflow = JSON.parse(readFileSync(fileURLToPath(new URL('../../../workflows/xhs-intel-edge.json', import.meta.url)), 'utf8'))[0];

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
assert.match(compose, /N8N_RUNNERS_MODE: external/);
assert.match(compose, /container_name: feishu-relay-edge-n8n-runners/);
assert.match(compose, /N8N_RUNNERS_TASK_BROKER_URI: http:\/\/127\.0\.0\.1:5679/);
assert.doesNotMatch(source, /UPDATE execution_entity|status='crashed'/);
assert.equal(workflow.settings.saveDataSuccessExecution, 'all');
assert.equal(workflow.settings.saveDataErrorExecution, 'all');
assert.match(source, /XHS workflow unchanged; skipping import and restart/);
assert.match(source, /stop n8n/);
assert.match(source, /before\.json/);
assert.match(source, /import:workflow --input=\/xhs-deploy\/candidate\.json/);
assert.match(source, /\.xhs-credentials/);
assert.doesNotMatch(source, /"\$xhs_token" "\$xhs_webhook"/);
assert.match(source, /XHS workflow was not activated after n8n restart/);

console.log('XHS deployment validates the private Cookie and provisions an external n8n task runner');
