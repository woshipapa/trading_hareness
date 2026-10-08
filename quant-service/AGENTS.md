# Quant research maintenance boundary

Component `quant-research` (runtime `47owner`). Release units: `owner-quant`
(code), `owner-schema` (migrations), `edge-quant-console` (the console in
`../frontend`). `config/components.json` is the authority.

- Strategy, rules and realtime code live in `app/`. Follow the change map in the
  root `AGENTS.md`: routers are the HTTP boundary only, `*_repository.py` are
  database projections, `*_rules.py`/`*_research.py` stay pure or research-only,
  and `app/main.py` is a composition root — no new business logic there.
- **Hot update, never an image rebuild** for ordinary `app/**` changes:
  `scripts/shared-peer/deploy-code-only.sh <sha> <label> --from-sha <active> --apply`.
  It fails closed on requirements, Dockerfile, compose, lockfile and migration
  changes — those need `docs/RELEASE_SYNC_47.md`.
- A migration is a **separate release unit**: apply and verify it on the owner
  *before* shipping code that depends on its schema. Commit migrations in their
  own commit; `quant-service/app/*` is on the code-only allowlist and
  `quant-service/migrations/**` is not.
- An app change and its tests belong in the same commit: the code-only release
  ships `quant-service/app/*` and skips `quant-service/tests/**` (it does not
  refuse them; `scripts/shared-peer/deploy-code-only.test.mjs` checks this).
- Only `app/n8n_workflow_audit.py` may name n8n's own tables
  (`public.workflow_entity`, `public.workflow_published_version`,
  `public.execution_entity`). Any other site, or any new foreign object, fails
  `scripts/verify_component_boundaries.py --check`. Declare it in
  `foreign_data_contracts` or do not do it.
- Never add a foreign key across a component boundary. The last one
  (`analyst_signals.ingestion_job_id` → the Feishu relay's ledger, with
  `ON DELETE CASCADE`) was removed in `20261008_sep0002`.
- Tests: `make -C quant-service test-release-path` runs the suite the way the
  release runs the code (working tree mounted into the existing image) against
  a database created for the run, migrated from the working tree and dropped
  afterwards; `QUANT_TESTS="tests.test_x"` selects modules. A failure is
  therefore the code's, not leftovers in the development database.
  Do **not** verify a source change with `docker compose exec -T
  quant-research ...` — that executes the image's baked copy and silently skips
  everything you just wrote.
- `frontend/` belongs to this component but is published to 47edge by
  `scripts/edge/deploy-quant-console-edge.sh`. Changing the console does not
  require a relay deploy, and a relay deploy no longer republishes the console.

Tests this component must pass before commit (from `config/components.json`):

```
make -C quant-service test-release-path
cd frontend && npm run typecheck && npm run build
```
