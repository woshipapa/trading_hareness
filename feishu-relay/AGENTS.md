# Feishu Relay maintenance boundary

- Adapter source and tests live in `adapter/`.
- LarkAgentX bridge source and tests live in `bridge/`.
- Edge deployment and rollback scripts live in `scripts/edge/`.
- Source labels live in `feishu-relay/config/source-registry.json`; do not put credentials in it.
- Keep Feishu OAuth, Cookie, webhook URL, app secret and supervisor state outside git.
- A WebSocket receipt is at-least-once; preserve the adapter ledger and message-level idempotency.
- Run `node --test feishu-relay/adapter/*.test.mjs` and the bridge unittest suite after relay changes.
- Run `git diff --check` before commit. For a 47edge change, use `scripts/edge/hotfix-feishu-relay-edge.sh` and verify `/health` plus the bridge health endpoint.
- Component `feishu-relay` (runtime `47edge`, release units `edge-relay` and
  `edge-workflows`). `config/components.json` is the authority.
- **Hot update, never an image rebuild** for adapter/bridge/dashboard source:
  `scripts/edge/hotfix-feishu-relay-edge.sh --apply`. It fails closed only when
  `adapter/package.json`'s **dependency** fields change (`scripts` and other
  unrelated fields do not count) — then publish an immutable image release.
- This unit no longer builds or publishes the quant console. `frontend/` belongs
  to `quant-research` and ships via `scripts/edge/deploy-quant-console-edge.sh`.
  Do not re-add `quant-frontend-dist` here; the isolation guard fails if you do.
- Never name the owner host (`47.110.79.189`, `stockpeer`, `OWNER_PEER_*`) in an
  edge script. Edge deploys must not be able to reach 47owner.
- The dirty-worktree check watches `feishu-relay` sources only. Never point it at
  a gitignored `*/dist` directory — `git diff` always reports those clean.
- `deploy-xhs-intel-edge.sh` still lives here but releases the `xhs-intel`
  component; its ordinary Python hot update is `xhs-intel/scripts/hotfix-xhs-intel-edge.sh`.

- Tests this component must pass before commit (from `config/components.json`):

```
npm install --prefix feishu-relay/adapter --no-audit --no-fund
node --test feishu-relay/adapter/*.test.mjs
python3 -m unittest discover -s feishu-relay/bridge -p 'test_*.py' -q
cd feishu-relay/dashboard && npm run typecheck && npm test -- --run && npm run build
```
