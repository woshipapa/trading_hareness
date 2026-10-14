# Repository agent guide

## Scope

This repository runs a market-research platform, not an order execution system.
All analyst scoring, regression, calibration, pattern mining and recommendations
are evidence-only until an explicit promotion record says otherwise. Never add a
provider response directly to a live threshold or order path.

## Working notes (read first)

The state of ongoing work lives in `docs/work/`, kept under the `dev-notes` skill so that a new agent can
continue without the previous conversation.

- At session start read `docs/work/STATUS.md` (generated; never edit it), then the record of the line you were
  asked about under `docs/work/streams/`. `docs/work/README.md` holds this project's specifics: the commands, the
  dates (the notes use Asia/Shanghai; this machine's clock and `git log` are US Pacific), what only the user runs.
- Load the `dev-notes` skill before starting, planning, dispatching, merging, releasing or closing a line of
  work, when the user changes a requirement or asks to record something, and at wrap-up or hand-off.
- New work, follow-ups and data to backfill get an id in a record or in `docs/work/BACKLOG.md`; a fact about the
  live systems goes into `docs/work/FACTS.md` with its date and its probe. A sentence in the chat is not a record.

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
- Batch reads of the licensed interface (thousands of read calls, such as minute
  history) go through `scripts/licensed_minute_history_fetch.py` or keep to its
  limits: 6 requests in flight by default, never more than 8, not between 09:00
  and 15:10 Asia/Shanghai on a session. Measured 2026-10-13: the path tops out
  near 7.4 calls a second at 8 in flight, gains nothing beyond and is refused by
  the owner's gateway at 16 (`docs/work/FACTS.md` F027). More than 5,000 calls
  still need the user's explicit consent. Do not raise the service's own worker
  settings for a batch: the ceiling is not there.

## Agent workflow

1. Read the relevant module, router, migration and existing test before editing.
2. Add a pure-function test first, then a repository/HTTP test when a route or
   persistence path changes.
3. Use `apply_patch`; do not rewrite generated artifacts or expose `.env` values.
4. Run `make -C quant-service test-release-path`,
   `cd frontend && npm run typecheck && npm run build`, and `git diff --check`.
   That target mounts the working tree as the source overlay the release
   actually runs from. Do **not** verify a source change with
   `docker compose exec -T quant-research ...`: that executes the code baked
   into the image, so every change shipped by overlay is silently untested
   (measured 2026-10-08: 2290 tests via `exec` against 2336 via the overlay).
5. For a scheduler change, verify database rows, latest status endpoint and one
   real adapter request; a unit test alone does not prove the published route.
6. Run `node scripts/verify-api-contract.mjs` after adding or renaming a route;
   it checks the running OpenAPI document instead of trusting a source-only map.
7. Run `cd frontend && npm run api:generate` after an intentional API contract
   change; `npm run api:check` verifies the checked-in generated type is current.
8. Read `docs/ARCHITECTURE.md` before a cross-domain change; it is the concise
   ownership map, while this file remains the operational checklist. Decisions
   that constrain the design (data ownership, migrations, releases, write keys,
   Tushare retirement, fail-closed research, host registry) are recorded in
   `docs/decisions/`; add a record when you make one. Dated status reports,
   recaps and audits belong in `docs/archive/` once their conclusions are
   folded into a living document.
   `scripts/CATALOG.md` says what every script is for, which component owns it
   and which host runs it; a new script needs a rule in
   `scripts/generate_scripts_catalog.py`, then regenerate the catalog.
9. Syncing or releasing: since 2026-10-15 the relay stack (feishu-relay, xhs, itougu, the paper services) and the
   owner quant service run on ONE host, `116.62.30.225` (a server under another Alibaba Cloud account, SSH port 22; the
   quant service moved there 2026-10-14 17:00-17:08), as two independent release units with their own Docker daemon,
   directories and credentials. The old edge host `47.114.113.152` keeps only services that are not this repo's
   (stock-reports, sub2api) and the retired quant writer. Relay-side scripts log in as `root` with
   `~/.ssh/relay_release_ed25519`; owner-side tools use `OWNER_PEER_HOST` (`stockpeer`); the previous hosts `118.178.133.37` (2026-10-13) and `47.110.79.189:3535` no longer run it, have Docker
   blocked, and must not be restored) follows `docs/RELEASE_SYNC_47.md` step by step. Start and end
   with the read-only `scripts/release-sync-status.sh`. The edge relay and owner
   quant service are independent release units: use `--edge-source-sha` and
   `--owner-source-sha` when their runtime SHAs differ; never force one SHA into
   both checks. Ordinary source changes use the component overlay paths described
   in [`docs/UNIFIED_RELEASE_MODEL_47.md`](docs/UNIFIED_RELEASE_MODEL_47.md).
   Never run `scripts/deploy-intraday-edge-release.sh` (the edge quant writer is
   retired).
10. Keep XHS source provenance explicit. `Spider_XHS` is currently outside this
    repository, so an XHS release must record its commit or content digest. Do
    not claim a reproducible release until that source is pinned or brought into
    the repository. XHS cookies, collector tokens and Feishu webhooks stay in
    ignored host env files.

## Credential management (read this before touching tokens or env vars)

All tokens, API keys, secrets and certificates are centrally managed in
`config/secrets/`. **Do not scatter credentials in ad-hoc `.env` files, `scp`
them manually, or hard-code values in source.** Read
`config/secrets/README.md` for the full directory layout.

### Single source of truth

`.env.local` is the only file you edit. `.env.owner` and `.env.edge` are
**derived** from it by `env-split.py` — never edit them by hand. Owner compose
uses `PEER_*` prefixed names (the mapping is declared in `build_owner()`);
edge compose uses the original names.

### When you add or change a credential

1. Add `KEY=value` to `config/secrets/.env.local`.
2. Add `KEY=` (empty template) to `config/secrets/env.example`.
3. Add the key to `env-split.py`: in `PASSTHROUGH` for edge, in
   `build_owner()` for owner — so it flows to the right environment.
4. Regenerate and push:
   ```
   cd config/secrets
   python3 env-split.py              # regenerate .env.owner + .env.edge
   bash sync-secrets.sh all          # rsync to both remotes
   ```
5. Restart the affected service on the remote to pick up the new value.
6. Update `docs/凭据地图.md` (gitignored) if the key is a new category.

### Hard rules

- **Never echo, log, persist or commit a secret value.** Variable names are
  fine; values are not. Test code must use fixture values, never `.env.local`.
- **Never `scp` or hand-copy an env file to a remote.** Always use
  `sync-secrets.sh` — it sets 600 permissions and targets the correct path.
- **Never create a new `.env` file outside `config/secrets/`.** The root `.env`
  and the remote compose `.env` files are **symlinks** into `config/secrets/`;
  do not replace them with regular files.
- **Tracked files:** `README.md`, `env.example`, `env-split.py`. Everything
  else under `config/secrets/` is gitignored. The `.gitignore` pattern is
  `config/secrets/*` with `!` exceptions for these three.

## Component boundaries and hot update (read this before editing)

`config/components.json` is the machine-readable owner map; it wins over memory
and over anything a summary told you. Run
`python3 scripts/verify_component_boundaries.py <changed paths>` to see which
components a change touches, and `--check` to validate the boundaries.

| You are editing | Component / unit | Hot update with |
|---|---|---|
| `quant-service/app/**` (strategies, rules, realtime, providers) | `quant-research` / `owner-quant` | `scripts/shared-peer/deploy-code-only.sh` |
| `quant-service/migrations/**` | `quant-research` / `owner-schema` | the owner applies the migration first; then `scripts/shared-peer/deploy-code-only.sh` ships it, and its schema gate refuses while the owner database is behind the release head |
| `frontend/**` (quant strategy + realtime console) | `quant-research` / `edge-quant-console` | `scripts/edge/deploy-quant-console-edge.sh` |
| `feishu-relay/**` | `feishu-relay` / `edge-relay` | `feishu-relay/scripts/edge/hotfix-feishu-relay-edge.sh` |
| `xhs-intel/**` | `xhs-intel` / `edge-xhs` | `xhs-intel/scripts/hotfix-xhs-intel-edge.sh` |

Rules that are enforced by tests, not by convention:

1. **Ordinary source changes never rebuild an image.** Use the unit's script
   above. A hot-update script must not contain `docker build`/`docker pull`, and
   must pass `--no-build --pull never` when it recreates a container.
   Dependency and Dockerfile changes (the image's inputs) and changes to the
   host's own tree (owner compose, systemd units, the scripts the host runs)
   fail closed and require the full procedure in `docs/RELEASE_SYNC_47.md`. A
   migration ships hot once the owner database is at the release head. A path
   no rule names is refused as `unclassified`: add its rule to
   `scripts/shared-peer/classify-owner-paths.sh` (a script: its rule in
   `scripts/generate_scripts_catalog.py`, whose "Runs on" column decides); it
   is never a reason to build an image.
2. **Release units are independent.** An edge deploy must never name the owner
   host and the owner release must never name the edge host. The owner
   code-only path classifies `feishu-relay/*` and `frontend/*` as
   not-owner-runtime; unknown paths fail closed.
3. **Do not cross a component boundary.** No cross-component Python imports, no
   `./`/`../` references into another component, and no SQL against another
   component's schema unless it is declared in `foreign_data_contracts` *and*
   you keep it at the single declared site. Shared databases are declared in
   `shared_runtime_databases`. Removing a coupling means deleting its entry too.
4. **A deploy must prove the new code is executing, not merely staged.** Either
   the service reports `runtime_source` in its own health payload, or the script
   checks PID 1's argv / compares the served source digest. "The file exists" is
   not evidence. Note `docker exec env` cannot see variables exported by the
   entrypoint shell; read `/proc/<pid>/environ` instead.
5. **A dirty-worktree check must watch sources, not build output.** `*/dist`
   directories are gitignored, so `git diff -- frontend/dist` always reports
   clean and the release would be stamped as clean while shipping uncommitted
   code.

Before commit, run: the component's own tests, `make -C quant-service
test-release-path` for quant changes, `python3 -m pytest scripts -q` (the
boundary, isolation and hot-update guards), and `git diff --check`. Commit only
your own files; this repository often has more than one agent working in it.

Each component directory has its own `AGENTS.md` with the local detail. Read it.

## 47 Release Model

Routine source changes use the existing runtime images and a versioned source
overlay. Do not rebuild or pull an image for ordinary Python, Node, bridge,
adapter, relay, dashboard, frontend, data-processing, or research-strategy
code changes when the required runtime dependencies are already in the active
image.

- Start with `scripts/release plan [<sha>]` (read-only): it reads what each host
  runs, says per host whether the change is a code-only overlay, a full release
  (and which paths force it) or nothing, lists migrations the owner must apply
  first, and checks the no-restart windows (`config/release-windows.json`).
  `scripts/release` is also the entry point for every release verb (`owner
  full|code`, `edge image|hotfix|workflows`, `status`, `window`).
- Quant owner source changes use
  `scripts/shared-peer/deploy-code-only.sh <target_sha> <release_label> --from-sha <active_sha> --apply`
  (`scripts/release owner code ...`); the path rules live in
  `scripts/shared-peer/classify-owner-paths.sh`.
- Feishu edge source changes use
  `feishu-relay/scripts/edge/hotfix-feishu-relay-edge.sh --apply`.
- Both paths must source the exact Git SHA, stage a retained release, atomically
  switch the `current` pointer, reuse the existing image with `--no-build
  --pull never`, verify health and runtime provenance, and keep the previous
  release available for rollback.
- Never copy secrets, cookies, tokens, `*.env`, or `*-secrets.env` into a
  source release. Update only non-secret version metadata in the remote
  runtime environment. All credentials live in `config/secrets/` (gitignored);
  see `config/secrets/README.md` for structure and `env.example` for the
  complete key list. Push credentials to remotes with
  `bash config/secrets/sync-secrets.sh {owner|edge|all}`, never ad-hoc `scp`.
- A fast source release must fail closed when `requirements.txt`,
  `package.json`, a lockfile, Dockerfile, compose file, system package or
  supervisor/systemd contract changes. Those changes require the complete
  image procedure in `docs/RELEASE_SYNC_47.md`. The owner classifier
  (`scripts/shared-peer/classify-owner-paths.sh`) names six kinds: `runtime`
  and `migration` ship in the overlay (a migration only through
  `scripts/shared-peer/schema-gate.sh`), `skip` never reaches the owner,
  `full` is the host's tree, `image` is an input of the image,
  `tunnel-image` is an input of the tunnel image (no release rebuilds it), and
  `unclassified` asks for a rule and builds nothing. The code-only path
  enforces this; the full release does not read the classification.
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
