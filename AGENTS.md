# Repository agent guide

## Scope

This repository runs a market-research platform, not an order execution system.
All analyst scoring, regression, calibration, pattern mining and recommendations
are evidence-only until an explicit promotion record says otherwise. Never add a
provider response directly to a live threshold or order path.

## Change map

- `quant-service/app/routers/`: HTTP boundary and request validation.
- `quant-service/app/*_repository.py`: database read/write projections.
- `quant-service/app/*_scheduler.py`: timing, retry windows and idempotency only.
- `quant-service/app/*_rules.py` / `*_research.py`: pure or research-only rules.
- `quant-service/app/main.py`: composition root; do not add new business logic here.
- `quant-service/migrations/versions/`: all production schema changes (Alembic).
- `frontend/src/App.vue`: current Vue dashboard; keep API calls typed and label
  research-only/replay-only values visibly.
- `frontend/src/api/http.ts`: shared JSON/error transport; do not duplicate
  browser response parsing in a feature panel.
- `frontend/src/composables/`: lifecycle-owned polling/subscriptions.
- `frontend/src/components/`: focused feature panels; new UI must not expand
  the root dashboard shell.
- `feishu-relay/adapter/index.mjs`: browser/API proxy; route mappings need separate GET
  and POST entries.
- `quant-service/app/security.py`: shared write-boundary primitives; keep this
  module framework-light so HTTP contract tests can import it without startup.

## Time and data rules

- Exchange dates and session windows use `Asia/Shanghai`; persisted event times
  are timezone-aware UTC values.
- Prefer analyst `stated_at` only for replay evidence; `strategy_available_at`
  is the point-in-time eligibility boundary.
- Fail closed on missing bars, stale providers, incomplete sector mappings and
  insufficient samples. Do not invent Top10s, prices or regression coefficients.
- Keep author replay outcomes separate from strategy-available outcomes.

## Agent workflow

1. Read the relevant module, router, migration and existing test before editing.
2. Add a pure-function test first, then a repository/HTTP test when a route or
   persistence path changes.
3. Use `apply_patch`; do not rewrite generated artifacts or expose `.env` values.
4. Run `docker compose exec -T quant-research python -m unittest discover -s tests -q`,
   `cd frontend && npm run typecheck && npm run build`, and `git diff --check`.
5. For a scheduler change, verify database rows, latest status endpoint and one
   real adapter request; a unit test alone does not prove the published route.
6. Run `node scripts/verify-api-contract.mjs` after adding or renaming a route;
   it checks the running OpenAPI document instead of trusting a source-only map.
7. Run `cd frontend && npm run api:generate` after an intentional API contract
   change; `npm run api:check` verifies the checked-in generated type is current.
8. Read `docs/ARCHITECTURE.md` before a cross-domain change; it is the concise
   ownership map, while this file remains the operational checklist.
9. Syncing or releasing to the two 47 hosts (edge `47.114.113.152`, owner
   `47.110.79.189`) follows `docs/RELEASE_SYNC_47.md` step by step. Start and end
   with the read-only `scripts/release-sync-status.sh --sha <sha>`; never run
   `scripts/deploy-intraday-edge-release.sh` (the edge quant writer is retired).

## 47 Release Model

Routine source changes use the existing runtime images and a versioned source
overlay. Do not rebuild or pull an image for ordinary Python, Node, bridge,
adapter, relay, dashboard, frontend, data-processing, or research-strategy
code changes when the required runtime dependencies are already in the active
image.

- Quant owner source changes use
  `scripts/shared-peer/deploy-code-only.sh <target_sha> <release_label> --from-sha <active_sha> --apply`.
- Feishu edge source changes use
  `feishu-relay/scripts/edge/hotfix-feishu-relay-edge.sh --apply`.
- Both paths must source the exact Git SHA, stage a retained release, atomically
  switch the `current` pointer, reuse the existing image with `--no-build
  --pull never`, verify health and runtime provenance, and keep the previous
  release available for rollback.
- Never copy secrets, cookies, tokens, `*.env`, or `*-secrets.env` into a
  source release. Update only non-secret version metadata in the remote
  runtime environment.
- A fast source release must fail closed when `requirements.txt`,
  `package.json`, a lockfile, Dockerfile, compose file, system package,
  supervisor/systemd contract, or Alembic migration changes. Those changes
  require the complete image and/or database migration procedure in
  `docs/RELEASE_SYNC_47.md`.
- A database migration must be applied and verified before switching code that
  depends on its schema. Do not recreate an unavailable historical migration
  from memory; use an observed, idempotent repair or stop and document the
  missing lineage.
- After either deployment, verify the actual running SHA/release, image ID,
  service health, owner database lineage, WebSocket/relay state, queue and
  failure counters, and relevant source-to-target mapping. A build result or a
  single health endpoint alone is not deployment evidence.
- Keep research and teacher strategies fail-closed: registration or loading
  does not grant execution permission. Confirm promotion status, weight and
  `live_effect` remain consistent with the explicit approval record.

The detailed commands, rollback steps and immutable-image transition are in
`docs/RELEASE_SYNC_47.md`, especially sections F5 and F6. The overlay is the
default for routine code updates; an immutable image release is still required
when runtime dependencies or infrastructure contracts change.

## Review automation

Analyst daily/weekly reviews are materialized by
`analyst_market_review.py`, persisted in `quant.analyst_market_reviews`, and
triggered by `strategy_review_scheduler.py`. The frontend reads them through
`/api/research/analyst-research/reviews/latest`; the regression is descriptive
and has `live_effect=none` until the documented sample gate is met.

For maintenance triage, read `/api/research/agent/context` first and then
`/api/research/automation/runs?task_key=...`. These are secret-free context and
durable execution evidence, not a substitute for source/test inspection.
