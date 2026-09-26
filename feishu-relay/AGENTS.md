# Feishu Relay maintenance boundary

- Adapter source and tests live in `adapter/`.
- LarkAgentX bridge source and tests live in `bridge/`.
- Edge deployment and rollback scripts live in `scripts/edge/`.
- Source labels live in `feishu-relay/config/source-registry.json`; do not put credentials in it.
- Keep Feishu OAuth, Cookie, webhook URL, app secret and supervisor state outside git.
- A WebSocket receipt is at-least-once; preserve the adapter ledger and message-level idempotency.
- Run `node --test feishu-relay/adapter/*.test.mjs` and the bridge unittest suite after relay changes.
- Run `git diff --check` before commit. For a 47edge change, use `scripts/edge/hotfix-feishu-relay-edge.sh` and verify `/health` plus the bridge health endpoint.
