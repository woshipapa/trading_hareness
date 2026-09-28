import assert from 'node:assert/strict';
import { readFileSync } from 'node:fs';
import { fileURLToPath } from 'node:url';

const source = readFileSync(fileURLToPath(new URL('./deploy-xhs-intel-edge.sh', import.meta.url)), 'utf8');

assert.match(source, /scp -q -i "\$edge_key"/);
assert.doesNotMatch(source, /scp -q "\$\{ssh_command\[@\]\}"/);
assert.match(source, /XHS_COOKIE_SOURCE/);
assert.match(source, /a1/);
assert.match(source, /web_session/);
assert.match(source, /XHS_FEISHU_WEBHOOK_URL must be set/);
assert.match(source, /docker compose --env-file "\$runtime_env" --env-file "\$secrets_env" build xhs-collector/);

console.log('XHS deployment uploads and validates the private Cookie independently of SSH command arguments');
