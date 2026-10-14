# Feishu Relay maintenance boundary

- Adapter source and tests live in `adapter/`.
- LarkAgentX bridge source and tests live in `bridge/`.
- Edge deployment and rollback scripts live in `scripts/edge/`.
- Source labels live in `feishu-relay/config/source-registry.json`; do not put credentials in it.
- That file is not the whole list. A tag first used when a source group is added in the dashboard is registered
  at run time in `ingestion_source_profiles` (`adapter/source-tags.mjs`); the file wins for a tag it declares. A
  target group's custom-bot webhook entered in the dashboard is stored encrypted in
  `feishu_group_relay_webhook_targets` (`adapter/webhook-targets.mjs`) and never returned or logged. Neither needs a
  release. See `docs/decisions/0019-feishu-relay-dashboard-registrations.md`.
- A target group picked in the dashboard without a webhook address gets its bot from the bridge
  (`bridge/chat_bots.py`): the account's own session creates a custom bot named 转发机器人 through the web client's
  `open-apis/chatbot/api` calls. Only a bot with that name made by this account is ever reused, changed or removed.
  These are the web client's interfaces, not published ones. See `docs/decisions/0020-feishu-relay-creates-target-bots.md`.
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
- feishu-relay releases at any time: it is not bound by the owner's no-restart
  windows (`config/release-windows.json` lists `edge-adapter` as unrestricted,
  `docs/decisions/0014`). Do not pass `RELEASE_WINDOW_OVERRIDE` for it. A release
  still restarts the adapter and the bridge, so the WebSocket is down for about
  15 seconds.
- Never name the owner host (`116.62.30.225`, the earlier `118.178.133.37`, the pre-migration `47.110.79.189`, `stockpeer`, `OWNER_PEER_*`) in an
  edge script. Edge deploys must not be able to reach 47owner.
- The dirty-worktree check watches `feishu-relay` sources only. Never point it at
  a gitignored `*/dist` directory — `git diff` always reports those clean.
- XHS image and overlay release scripts live under `xhs-intel/scripts/`; this
  component does not own or publish the XHS runtime.

- Tests this component must pass before commit (from `config/components.json`):

```
npm install --prefix feishu-relay/adapter --no-audit --no-fund
node --test feishu-relay/adapter/*.test.mjs
python3 -m unittest discover -s feishu-relay/bridge -p 'test_*.py' -q
cd feishu-relay/dashboard && npm run typecheck && npm test -- --run && npm run build
```
