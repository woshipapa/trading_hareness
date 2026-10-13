# Owner releases: one image, everything else hot

Workstream W012. Written 2026-10-13 (Asia/Shanghai). Revision 2, same day, after the plan audit (eleven
objections, all accepted; the list is at the end).

## Goal

The owner quant service keeps one image. A release rebuilds it only when the image's own inputs change;
every other change, including the ones that today demand a "full release" (compose, scripts that run on the
host, migration files, paths the classifier does not know), ships on the existing image without a build.

The user's words, 2026-10-13: "对于后续owner侧的这个内存问题，你看看能否统一都用一套镜像，以后都是代码上的hotfix啊";
"这里的发布规则更改，保证以后除了非常必要的需要重新构建其他全是热部署。可以搞一个按你的设计去压缩的".

Why now: the owner host was replaced on 2026-10-13 by one with 894 MB of memory (docs/work/FACTS.md F022). A full
release builds the image on that host. On the same day a two-line change of two operator scripts turned the next
release into a full one and had to be reverted (commit e8cb8dbf).

## What exists (read 2026-10-13; file:line)

- The image (`quant-service/Dockerfile.peer`): `python:3.12-slim`, no system packages; `pip install` of
  `quant-service/requirements.txt` from a wheelhouse build context that lives outside git (:11-14); COPY of
  `quant-service/app`, `tests`, `alembic.ini`, `migrations`, `entrypoint.py`, `run_server.py`,
  `database_bootstrap.py`, and two tools, `scripts/audit-fuyao-capabilities.py` and
  `scripts/import-stock-brain-portfolio.py` (:16-24). The two tunnel services build from
  `deploy/shared-peer/Dockerfile.ssh-tunnel`, which bakes `deploy/shared-peer/ssh-tunnel-entrypoint.sh`.
- The overlay: the compose `command:` runs `/app/hotfix/current` when `QUANT_HOTFIX_ENABLED` is true and the tree
  is there (`deploy/shared-peer/compose.yaml:126-142`, `deploy/shared-peer/compose.intraday-owner.yaml:152-182`). The code-only
  release (`scripts/shared-peer/deploy-code-only.sh`) ships `quant-service/app`, `entrypoint.py`, `run_server.py`,
  `database_bootstrap.py`, `alembic.ini` and `quant-service/migrations` (:71-74), switches `hotfix/current`,
  recreates the main service and then the scheduler with `--no-build --pull never`, and proves the container
  serves the new source (:144-177). Both services start with `QUANT_SKIP_MIGRATIONS=true`: nothing migrates at
  start.
- The host checkout: `~/trading_hareness` is a symlink into
  `~/.local/share/trading-hareness/releases/<id>/trading_hareness` (`scripts/shared-peer/activate-peer-release.sh:8-11,
  214-223`). Compose reads its files there; the systemd guard and backfill units run
  `scripts/peer-session-guard.sh` and `scripts/backfill-full-market-daily.sh` through that symlink; the overlay
  tree itself lives under it (`hotfix/quant-service`, env `QUANT_HOTFIX_HOST_DIR`). Nothing but a full release
  moves the symlink, so a changed compose file or host script reaches the host only by a full release.
- The full release (`scripts/shared-peer/deploy-full-release.sh`): archive of the whole commit, the schema gate
  (:73-132), activation into a new tree, `docker compose build quant-research` on the host (:223-226), ordered
  recreate, proofs, rollback.
- The classifier (`scripts/shared-peer/classify-owner-paths.sh`): `runtime`, `skip`, `full`; a changed migration
  is `full` (:50-55) and so is every path no rule names (:91).
- Migrations are applied by the owner on the Windows workstation before any code switch (decision 0002,
  `docs/RELEASE_SYNC_47.md` stage D). `scripts/release_plan.py:90-105` and the full release's gate compare the
  owner database's revision (from `/health`) with the target's head through
  `quant-service/scripts/migration_lineage.py`.
- Tests that pin today's rules: `scripts/shared-peer/deploy-code-only.test.mjs` (:101-110 the five "full" cases),
  `scripts/test_release_unit_isolation.py` (:83-91), `scripts/test_hot_update_paths.py`,
  `scripts/test_release_cli.py` (:53-66), `scripts/test_deploy_full_release.py`,
  `config/components.json` (`hot_update_paths`, `full_release_required_when`).
- The rule in words: `AGENTS.md` ("Component boundaries and hot update" rule 1; "47 Release Model" fail-closed
  bullet), `docs/UNIFIED_RELEASE_MODEL_47.md:14-15,63-65`, `docs/RELEASE_SYNC_47.md` F5.

## Design

### Kinds of path (the classifier)

| kind | paths | what a release does |
|---|---|---|
| `runtime` | as today | overlay, as today |
| `migration` | a migration file whose code changed; the migration support files (`quant-service/migrations/env.py`, `script.py.mako`, `quant-service/alembic.ini` moves here from `runtime`) | ships in the overlay as today; the release goes on only when the schema gate passes (below) |
| `tree` | an explicit list: `deploy/shared-peer/compose.yaml`, `deploy/shared-peer/compose.intraday-owner.yaml`, `deploy/shared-peer/systemd/*`, and the scripts that run on the host (`scripts/peer-session-guard.sh`, `scripts/backfill-full-market-daily.sh`, `scripts/shared-peer/release-lock.sh`, `activate-peer-release.sh`, `ensure-batch-tunnel.sh`, `verify-owner-cutover.py`, `switch-owner-tree.sh`) | a new host tree without a build (below), then the overlay |
| `image` | `quant-service/requirements.txt`, `quant-service/Dockerfile.peer`, `.dockerignore`; a compose change whose `build:` section differs (dockerfile, context, additional contexts, args) | the hot path refuses and names them; the image procedure applies |
| `tunnel-image` | `deploy/shared-peer/Dockerfile.ssh-tunnel`, `deploy/shared-peer/ssh-tunnel-entrypoint.sh`; a compose change to a tunnel's `build:` | refused by every release: no release rebuilds or recreates the tunnels today (`deploy-full-release.sh:175-180, 222-226`); the message says it is done by hand with the owner |
| `skip` | as today, plus: the two tools the image copies (`scripts/audit-fuyao-capabilities.py`, `scripts/import-stock-brain-portfolio.py`; workstation tools by `scripts/CATALOG.md`, refreshed when an image is next built), `scripts/data/*` (evidence files), `deploy/intraday-edge/*` (the retired edge runtime), `config/secrets/set-local-secret.sh` | nothing |
| `unclassified` | every path no rule names | the release refuses and says "add a rule to classify-owner-paths.sh"; it builds nothing |

`full` disappears. An unknown path still fails closed, but the answer is a one-line rule, not an image build:
shipping unknown files to the host would put there what must not be there (`config/secrets/set-local-secret.sh`
is tracked and unlisted today; so are evidence files under `scripts/data/`). A test derives the image's inputs
from the COPY lines of the two Dockerfiles and from the `build:` sections of the two compose files, and fails when
one of them is not classified `runtime`, `migration`, `image`, `tunnel-image` or `skip`: a new COPY cannot reach
`tree`. The compose `build:` comparison is done on the parsed YAML of the two commits, not on the text.

The wheelhouse and the base image are outside git: the classifier cannot see them, and the image procedure's
own checks (the wheelhouse checksum in `activate-peer-release.sh:142-146`) stay as they are.

### The tree step (inside the one hot release command)

When a `tree` path changed, `deploy-code-only.sh` also sends those files (and the list of deleted ones), and the
host does, under the guard lock:

1. preflight, before anything is changed: the tree `~/trading_hareness` points at and the release store are on the
   same filesystem (otherwise the new tree is a plain copy instead of a hard-link copy: the tree is small); the
   lock tool is called by its resolved real path, taken once before the switch and used for hold and release, so
   a release that changes `release-lock.sh` still releases the lock it took;
2. snapshot the env keys this release writes (`PEER_APP_GIT_SHA`, `PEER_APP_RELEASE`, `PEER_EXPECTED_RELEASE`,
   `PEER_APP_BUILD_CREATED_AT`, `QUANT_HOTFIX_ENABLED`) from the real file behind the `.env` symlink;
3. make the new tree `~/.local/share/trading-hareness/releases/<label>/trading_hareness` as a hard-link copy of
   the current one, then replace the changed files in it (unlink, then write: the old tree keeps the old file)
   and remove the deleted ones;
4. stage this release's overlay inside the new tree (`hotfix/quant-service/releases/<label>`, `current`);
5. validate compose from the new tree (`config --quiet`) and run the schema gate's flag check on the rendered
   config (below) before anything is switched;
6. switch `~/trading_hareness` to the new tree atomically (`ln -sfn` to a temporary name, `mv -Tf`);
7. recreate as today (tunnel health, main service, scheduler; `--no-build --pull never`) and run today's proofs,
   plus: `~/trading_hareness` resolves to the new tree; `.env` and `intraday-secrets.env` are still symlinks; and
   `docker inspect` of both recreated containers shows the hotfix mount source inside the new tree (a container
   keeps the bind source it was created with, so this is the proof the recreate took the new tree);
8. on any failure: switch the symlink back, restore the snapshot of the env keys, recreate from the previous
   tree, and verify the previous tree's `current` and both containers' mount source.

The same snapshot and restore of the env keys is added to the plain code-only path, which today leaves the new
values behind after a failed release (`deploy-code-only.sh:151-155` says so).

The host part is a script of its own, `scripts/shared-peer/switch-owner-tree.sh`, streamed from the archive like
`activate-peer-release.sh`, so that it is tested on a real directory tree in a temporary folder. Old trees made by
this step are pruned to the last three; a tree made by a full release is never pruned by it. A changed systemd
unit file is shipped like any tree file and the release prints that the unit must be reinstalled by hand: no
script installs units today.

When no `tree` path changed the release is today's code-only release (plus the env restore on failure).

### The schema gate (one script, both releases)

The gate moves out of `deploy-full-release.sh:73-132` into `scripts/shared-peer/schema-gate.sh`. It answers two
questions:

- Is the owner database at the target's head? "Behind" refuses and lists the pending revisions (the owner applies
  them on the Windows workstation, decision 0002); "at head" continues; "unknown" needs the existing explicit
  override.
- Can the peer run schema changes on its own after this release? It must not: the rendered compose config of the
  target must have `QUANT_SKIP_MIGRATIONS` true and `QUANT_LEGACY_SCHEMA_BOOTSTRAP` not true for the main service
  and for the scheduler (`quant-service/entrypoint.py:62-67,164-176`, `application_lifecycle.py:93-95`,
  `database_bootstrap.py:119-136` are the paths those flags close). A release that would open one of them is
  refused.

A hot release runs the gate when its diff holds a `migration` path or a `tree` path that is a compose file.

### The image procedure

`deploy-full-release.sh` stays as the only path that builds, renamed in the CLI (`release owner image`, with
`owner full` kept as an alias). Before `docker compose build` it reads the host's free memory and swap and
refuses under a threshold unless `RELEASE_ALLOW_BUILD_ON_HOST=1`. Building elsewhere and loading the image on the
host is the intended way and is written when first needed (BACKLOG item), not now.

### One command

`scripts/release owner code <sha> <label> --from-sha <active> [--apply]` is the hot release for `runtime`,
`migration` and `tree` together. `scripts/release plan` prints which steps a target needs, or which paths refuse
it and why (`image`, `tunnel-image`, `unclassified`).

## Steps (worktree `owner-hot-releases`, disposition merge into main; four dispatches of about 15 minutes)

The branch is merged once, after the last step, so `main` never sees a half state. Inside the branch the order
keeps every commit's tests green: nothing emits a new kind before its consumer exists.

1. `scripts/shared-peer/switch-owner-tree.sh` with tests on a temporary tree (hard links leave the old tree
   intact; the copy fallback on another filesystem; changed, added and deleted files; the overlay is in the new
   tree; a failing validation leaves the symlink where it was; rollback with the env snapshot; pruning; the pinned
   lock path) and its scripts-catalog rule (`scripts/generate_scripts_catalog.py`, `scripts/CATALOG.md`: mandatory,
   the generator rejects an unlisted script). Nothing calls the script yet; its classifier rule comes with the
   kinds in step 3.
2. `scripts/shared-peer/schema-gate.sh`: the gate extracted, with the flag check; `deploy-full-release.sh` calls
   it and its tests keep their meaning (`scripts/test_deploy_full_release.py`); tests for at head, behind,
   unknown, and a rendered config that opens a schema path.
3. In one commit set, the classifier's kinds and every consumer: `classify-owner-paths.sh` (the table above, the
   compose `build:` comparison, the COPY-derived test), `deploy-code-only.sh` (handles `migration` through the
   gate and `tree` through the switch script; refuses `image`, `tunnel-image`, `unclassified`; the env snapshot
   and restore; the dry run lists tree files and prints the compose diff), `scripts/release_plan.py` and
   `scripts/release` (the wording, `owner image`), the memory guard of the image path. Tests:
   `scripts/shared-peer/deploy-code-only.test.mjs:101-132`, `scripts/test_hot_update_paths.py:111-120`,
   `scripts/test_release_unit_isolation.py:83-90`, `scripts/test_release_cli.py:60-69`,
   `config/components.json` (`full_release_required_when`).
4. The rule in words, and the decision: `AGENTS.md` (both sections), `quant-service/AGENTS.md:11-18`,
   `docs/UNIFIED_RELEASE_MODEL_47.md`, `docs/RELEASE_SYNC_47.md` (F5 and :530-541),
   `docs/COMPONENT_CONTRACTS.md:135`, `docs/COLLAB_OWNER_PEER.md:63`;
   `docs/decisions/00NN-one-image-hot-releases.md` (the next free number), which supersedes the part of decision
   0003 that makes the full release the answer to compose, script and migration changes (0003 gets a note
   pointing to it).

## Acceptance

```
/Users/papa/.venvs/svc/bin/python -m pytest scripts -q     # test_runtime_hosts.py fails on main already; nothing else may
node scripts/shared-peer/deploy-code-only.test.mjs
node scripts/release-sync-status.test.mjs
python3 scripts/verify_component_boundaries.py --check
COMPOSE_ENV_FILES=/Users/papa/codebase/n8n/.env make -C quant-service test-release-path
git diff --check
```

## First use and verification

This change is itself all `skip` and `tree` paths. Its first real test is the first release that carries a `tree`
path: read-only before (`readlink -f ~/trading_hareness`, the tree's size, free memory), the release, then:
the symlink points at the new tree; `du` shows the new tree adds only the changed files; both health routes
report the target; `docker inspect` shows both containers' hotfix mount inside the new tree; the guard timer's next run succeeds from the new tree (`systemctl --user status` of the heal
unit); the previous tree is intact. A rollback is rehearsed once on purpose (switch back, recreate), in an
allowed window, with the user present.

## Risks

- A hard-linked tree shares inodes: anything that edits a tree file in place would change both trees. The step
  only ever unlinks and rewrites; the test asserts the old tree's bytes are unchanged after a release.
- Compose changes recreate what compose decides to recreate, tunnels included; a tunnel recreate reconnects every
  database connection. The dry run prints the compose diff so the operator sees it before `--apply`.
- A path listed as `tree` by mistake would be copied to the host. The list is explicit and short, an unknown
  path refuses, and the archive holds only the changed paths of the diff.
- The guard timer runs every two minutes through the symlink. The lock is held for the whole switch by its
  pinned real path; a timer run that starts just before the hold and reads the old tree finishes on the old tree.
- The two tools copied into the image go stale between image builds. Accepted; they are run by hand, rarely.

## Out of scope

Building the image off the host and loading it (written when first needed); installing systemd units from a
release; the edge units; the daily jobs themselves (W011).

## The audit (2026-10-13, Codex, read-only) and what changed

1. `compose.intraday-owner.yaml` was named without its directory: paths corrected.
2. The image's inputs were incomplete (compose `build:` sections, `.dockerignore`): both are `image` now; the
   wheelhouse and the base image are outside git and are said to be.
3. No release rebuilds the tunnel images: their own kind, refused, done by hand with the owner.
4. A default of `tree` would ship files that must not be on the host: unknown paths refuse (`unclassified`); `tree`
   is an explicit list; three explicit skips added.
5. Hard links need one filesystem, and a container keeps its bind source: preflight with a copy fallback; a
   `docker inspect` proof.
6. The lock helper is reached through the symlink that moves: its real path is pinned for the transaction.
7. Rollback left the env values behind: snapshot and restore, in the tree step and in the plain code-only path.
8. "At head" is not enough when a release can re-enable schema execution on the peer: the gate checks the
   rendered flags; migration support files are `migration`.
9. More places state the old rule: the nested `AGENTS.md`, three more documents, decision 0003.
10. Emitting new kinds before their consumers would silently drop files: the steps are reordered; the kinds and
    all their consumers land together.
11. The new script needs its catalog rule: mandatory, in step 1.
