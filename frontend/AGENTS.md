# Quant console maintenance boundary

This is the quant strategy and realtime-data console. It belongs to the
`quant-research` component but ships as its own release unit
`edge-quant-console`, served from 47edge.

- Hot update with `scripts/edge/deploy-quant-console-edge.sh --apply`
  (dry run without `--apply`). It builds only this SPA, publishes to
  `hotfix/quant-console/{releases,current}` on the edge and flips an atomic
  symlink. No image is rebuilt. `--list` / `--rollback <id>` are independent of
  the Feishu relay.
- Do **not** add this build back into
  `feishu-relay/scripts/edge/hotfix-feishu-relay-edge.sh`. It used to live in
  the relay's atomic release, which meant an unrelated xhs fix republished this
  console and a console change had to wait for a relay deploy.
  `scripts/test_release_unit_isolation.py` fails if that returns.
- The console and the Feishu dashboard are served **same-origin** by the relay
  adapter, by route (`/`, `/research`, `/personal` here; `/monitor`,
  `/dashboard`, `/workbench`, `/relay` there). Same-origin is deliberate — both
  apps share one API, SSE and auth boundary. Do not split the origin.
- `dist/` is gitignored. A release's dirty check must watch `frontend` (sources),
  never `frontend/dist`, or the release gets stamped clean while shipping
  uncommitted code.
- Keep API calls typed and label research-only/replay-only values visibly. Run
  `npm run typecheck && npm run build`, and `npm run api:check` after an
  intentional API contract change.

Tests this component must pass before commit (from `config/components.json`):

```
make -C quant-service test-release-path
cd frontend && npm run typecheck && npm run build
```
