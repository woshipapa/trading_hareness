# Quant Research Platform Architecture

> **当前部署边界（2026-09-21）**：远端 owner/peer 是唯一实时数据面、provider
> 调用面、策略扫描面和生产写入面。本地工作站只做分析、研究、回放和通过
> `15682` 拉取远端证据；不会启动实时轮询或成为第二个 writer。完整约束见
> [`DEPLOYMENT_BOUNDARIES.md`](DEPLOYMENT_BOUNDARIES.md)。

This is a market-research platform whose production data plane runs on the
remote owner/peer host. The local workstation is an analysis and read-through
client. It does not connect to a broker and does not submit orders. Every
strategy result is research evidence until its separate promotion gate is
satisfied.

## Runtime map

```text
Feishu / n8n / browser
        |
        v
feishu-relay/adapter (proxy, relay, OAuth and media boundary)
        |
        v
quant-research FastAPI
  routers -> services/orchestrators -> repositories -> PostgreSQL
                                      -> provider adapters -> external sources
        |
        +-> raw -> canonical -> features -> signals -> outcomes
```

The deployable background profiles split this map without changing the HTTP or
research contracts. On the remote owner/peer host, `intraday_edge` is the
single live-polling and Feishu-alert writer for `intraday_monitor`, fast quote,
minute profile, order book and board flow. The remote `research` scheduler owns
post-close review and replay, but never starts those five polling loops. The
local workstation is a read-through analysis client: a workstation outage
delays analysis visibility without stopping remote collection or losing retained
evidence. Any older edge-journal or local-writer path is historical recovery
material, not the current production writer.

Both profiles run the same committed source revision. The distinction is
runtime configuration and ownership, not a long-lived server branch: releases
publish a Git SHA and image/source provenance through the loopback health
endpoints, while secret environment files remain outside version control.

The opt-in `research-worker` profile is a one-shot daily model laboratory, not
an API or scheduler replica. It receives only PostgreSQL and the bounded quant
artifact volume, runs with a read-only root filesystem, 4 GiB memory and 2 CPU
limits, and has no provider, Feishu or broker credentials. Its output is an
immutable point-in-time dataset, several embargoed OOF trial receipts and a
`draft`/`trained`/`rejected` registry row with `live_effect=none`; the online
service never loads these artifacts.

The edge adapter separates the slow-changing runtime from fast bug iteration.
The verified image owns Node and `node_modules`; adapter JavaScript, frontend
assets, routing configuration and the LarkAgentX Python bridge are staged under
the retained `/opt/feishu-relay-edge/hotfix/releases/` tree. The adapter reads
its source through a read-only mount, while the bridge's stable systemd
entrypoint follows the same atomic `current` symlink. The candidate is checked
with the image's Node runtime and the supervisor venv's Python runtime before
activation. The hotfix deployer uses `docker compose up --no-build --pull
never`, restarts both processes, checks the adapter, WebSocket bridge and
webhook configuration health, keeps a bounded rollback window, and restores
the previous pointer on failure. Dependency, Node, Python environment or
base-image changes must use their immutable runtime release path.

The edge Feishu adapter also runs a separate Baidu market-archive lane. Every
30 seconds it reads the latest all-A Level-1 and strategy snapshots, commits an
idempotent job to the PostgreSQL archive ledger, and returns to the polling
cadence. A bounded uploader drains that ledger independently with leases,
retries and per-object paths under the Baidu research root. Cloud I/O cannot
hold the quant collector's provider/database executor or suppress a later
snapshot read; a non-zero queue is visible through
`/api/baidu-pan/market-archive/status` and is drained after transient failures.

`quant-service/app/main.py` is the composition root.  It owns application
lifespan, dependency assembly and router registration.  New behaviour belongs
in a focused module, then is injected from `main.py`; production modules must
not import `app.main`.

## Component releases

The repository is one source tree with several independently deployable runtime
units. Feishu relay, XHS intelligence and owner quant code therefore carry
component release identities and rollback pointers rather than pretending that
one Git SHA describes every process. The operational rules, source-overlay
boundaries and component-aware status commands are in
[`UNIFIED_RELEASE_MODEL_47.md`](UNIFIED_RELEASE_MODEL_47.md).

The logical project map is machine-readable in `config/components.json`. It
currently keeps Quant Research, Feishu Relay and XHS Intelligence as separate
ownership units inside this monorepo, while `integration` owns compose, release
scripts, manifests and cross-project workflows. A physical repository split is
allowed only after the HTTP/event contracts and component release manifests are
stable; the manifest also records the release units that can move at different
cadences (`owner-schema`, `edge-workflows`, and so on). The boundary checker
runs before any such move. `scripts/export_component.py` can then produce a
secret-free, component-scoped archive for the eventual standalone repository;
the archive is not a production release and does not change the remote hosts.

Each component also carries a local `component.json` and `Makefile`. The local
manifest repeats its release identity, declares external dependencies and names
the stable interfaces that must remain HTTP/event boundaries. The three standalone
compose files and the component-local manifests are checked together, so an
exported project can be maintained without importing this repository's business
modules. The detailed compatibility rules are in
[`COMPONENT_CONTRACTS.md`](COMPONENT_CONTRACTS.md).

## Ownership boundaries

| Concern | Location | Rule |
|---|---|---|
| HTTP request validation | `app/routers/` | Router functions validate and delegate; no provider crawling in a read route. |
| Provider transport | `app/*provider*.py`, `app/http_clients.py` | Reuse lifecycle clients and record availability. |
| Evidence semantics | `app/platform/evidence_contracts.py` | Every normalized source declares provider, capability, scope, coverage semantics and decision eligibility before strategies consume it. |
| Data placement and replay | `app/platform/data_product_registry.py` | Every strategy/runtime dataset declares time semantics, local tier, immutable cloud format, partition keys, replay role and owner hot/cold policy. The five 365-day split products must match `owner_storage.TIERED_EVIDENCE_TABLES`; cloud copies never become direct decision inputs. |
| Owner storage compatibility | `app/owner_storage.py`, `docs/OWNER_DATABASE_STORAGE.md` | Read-only detection of tablespace, all `_cold` twins, hot/cold schema parity, legacy-table placement and factor semantics; partial cutovers never enter research unions and peer never runs owner DDL. |
| Instrument registry writes | `app/instrument_registry.py` | Sort/deduplicate symbols and use one `unnest` upsert so concurrent owner writers acquire locks in the same order. |
| Adjustment-factor semantics | `app/adjustment_factor_semantics.py` | Mirrors owner `peer-contract-v2`: positive, unsuperseded Tushare rows are valid when `raw.factor_semantics` is absent or `corporate_action_cumulative`; `longhu_qfq_derived` requires the explicit cumulative semantic; identity/unknown providers fail closed. |
| Persistent projections | `app/*_repository.py`, `app/*_read_model.py` | Bound result sets; async dashboard reads use `AsyncDatabase`. |
| Timing and recovery | `app/*_scheduler.py`, `app/runtime_tasks.py`, `app/*_runtime.py` | Durable leases, idempotent run keys and explicit retry windows; runtime adapters bind scan I/O and lease ports without embedding transactional closures in the ASGI root. |
| Runtime ownership | `app/platform/runtime_task_registry.py` | Each leased task declares one owner profile, expected cadence, upstream capabilities and retained evidence datasets; startup rejects an undeclared or missing task factory. |
| Rules and research | `app/*_rules.py`, `app/*_research.py` | Keep inputs/outputs explicit and test without HTTP or database state. |
| Daily outcome settlement | `app/t1_settlement.py`, `app/t1_settlement_repository.py` | Every daily-horizon outcome (analyst claims, recommendations, post-close and leader-rotation candidates, the candidate ledger) settles through one T+1 rule: next-open entry (locked limit-up or suspended entries never settle), earliest exit the following close, blocked exits rolled forward, adjusted prices, net of `ashare_reality` costs, equal-weight A-share benchmark. Do not add another settlement query. |
| Offline model training | `app/research_model_training_worker.py`, `app/research_model_training.py` | One-shot, resource-limited, immutable artifacts and append-only trials only; no provider fetch, online load or automatic promotion. |
| Strategy contracts | `app/platform/strategy_registry.py` | Every strategy declares its model/input contract, runtime owner, retained evidence and `live_effect=none`; startup rejects missing or mismatched materialized model versions. |
| Schema | `migrations/versions/` | New production schema changes use Alembic only. |
| Legacy bootstrap | `app/database.py` | Disabled by default; only an explicit recovery operator may enable it. |
| Quant frontend | `frontend/src/` | Quant research and personal-decision console only; it has its own Vite build. |
| Feishu frontend | `feishu-relay/dashboard/src/` | LarkAgentX monitor, relay workbench and manual delivery only; it has its own Vite build. |
| Frontend transport | `frontend/src/api/http.ts`, `feishu-relay/dashboard/src/api/http.ts` | Each app keeps a typed same-origin JSON boundary and reports non-JSON proxy errors clearly. |
| Frontend lifecycle | `frontend/src/composables/`, `feishu-relay/dashboard/src/composables/` | Timers and subscriptions are owned and stopped by the mounting shell of each app. |

## Agent entry sequence

1. Read `GET /api/v1/agent/context`, its evidence/task contract catalogs, and the latest durable automation receipt.
2. Read the owning router, service, repository, migration and targeted test.
3. Preserve point-in-time boundaries: `stated_at` is replay evidence;
   `strategy_available_at` is the only strategy eligibility time.
4. Make one bounded change and add a focused test in the same domain.
5. Run backend tests, adapter tests, frontend API/type/build checks and
   `git diff --check`.
6. Verify mounted OpenAPI and `/health`; a source-only pass is not a runtime
   acceptance.

## Data and decision gates

- Missing/stale provider data, incomplete sector membership and insufficient
  statistical samples fail closed.
- The daily control plane applies to listed equities only.  Index benchmark
  rows are valid market context but do not carry equity `adj_factor` or
  `stk_limit` controls.
- `raw -> canonical -> features -> signals -> outcomes` is append/evidence
  oriented.  Replay outcomes never change live weights.
- Each layer is archived as immutable, manifest-addressed evidence. Research
  restores cloud partitions into staging and validates schema, hash, row count
  and point-in-time fields before use; strategies never query cloud objects in
  the live decision path.
- Workstation/network loss keeps the remote loops authoritative. Durable
  cursors, leases and run keys resume remote work after recovery without
  replaying completed work; local analysis catches up through the owner API.

## Frontend ownership

`frontend/src/App.vue` is the quant shell: navigation, lazy research view
registration and research dialogs. `feishu-relay/dashboard/src/App.vue` is a
separate Feishu shell and owns relay navigation, WebSocket event status and
workbench dialogs. The two apps share only the adapter's same-origin HTTP/SSE
boundary; neither imports the other's UI or composables.
Research tabs are independently lazy-loaded from `frontend/src/views/research/`;
Feishu relay views live under `feishu-relay/dashboard/src/views/`. New UI must
join the owning application instead of crossing the boundary.

The frontend has unit coverage for the JSON transport and timer lifecycle, plus
a browser smoke test for the mounted research shell. API types remain generated
from the mounted OpenAPI contract.

The legacy DDL retained in `app/database.py` is recovery-only.  It remains
isolated behind `QUANT_LEGACY_SCHEMA_BOOTSTRAP`; new migrations must never edit
that bootstrap SQL.
