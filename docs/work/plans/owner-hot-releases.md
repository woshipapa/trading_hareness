# Owner releases: one image, everything else hot

Workstream W012. Written 2026-10-13 (Asia/Shanghai).

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
  is there (`deploy/shared-peer/compose.yaml:126-142`, `compose.intraday-owner.yaml:152-182`). The code-only
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

### Five kinds of path (the classifier)

| kind | paths | what a release does |
|---|---|---|
| `runtime` | as today | overlay, as today |
| `migration` | a migration file whose code changed | ships in the overlay as today; the release goes on only when the owner database is at the target's head (the existing gate), otherwise it stops and prints what the owner must apply first |
| `tree` | `deploy/shared-peer/compose*.yaml`, scripts that run on the host, the systemd unit files, and every path no rule names | a new host tree without a build (below), then the overlay |
| `image` | `quant-service/requirements.txt`, `quant-service/Dockerfile.peer`, `deploy/shared-peer/Dockerfile.ssh-tunnel`, `deploy/shared-peer/ssh-tunnel-entrypoint.sh` | the hot path refuses and names them; the image procedure applies |
| `skip` | as today, plus the two tools the image copies (`scripts/audit-fuyao-capabilities.py`, `scripts/import-stock-brain-portfolio.py`: they are refreshed when an image is next built) | nothing |

`full` disappears. The default for an unknown path changes from `full` to `tree`: a path the Dockerfiles do not
consume cannot need an image. A test derives the image's inputs from the COPY lines of the two Dockerfiles and
fails when one of them is not classified `runtime`, `image` or `skip`, so a new COPY cannot slip into `tree`.

### The tree step (inside the one hot release command)

When a `tree` path changed, `deploy-code-only.sh` also sends those files (and the list of deleted ones), and the
host does, under the guard lock it already takes:

1. make the new tree `~/.local/share/trading-hareness/releases/<label>/trading_hareness` as a hard-link copy of
   the tree `~/trading_hareness` points at (no second copy of the bytes), then replace the changed files in it
   (unlink, then write: the old tree keeps the old file) and remove the deleted ones;
2. stage this release's overlay inside the new tree (`hotfix/quant-service/releases/<label>`, `current`);
3. validate compose from the new tree (`config --quiet`) before anything is switched;
4. switch `~/trading_hareness` to the new tree atomically (`ln -sfn` to a temporary name, `mv -Tf`);
5. recreate as today (tunnel health, main service, scheduler; `--no-build --pull never`) and run today's proofs,
   plus: `~/trading_hareness` resolves to the new tree, and `.env` / `intraday-secrets.env` are still symlinks;
6. on any failure switch the symlink back to the previous tree and recreate from it.

The host part is a script of its own, `scripts/shared-peer/switch-owner-tree.sh`, streamed from the archive like
`activate-peer-release.sh`, so that it is tested on a real directory tree in a temporary folder. Old trees made by
this step are pruned to the last three; a tree made by a full release is never pruned by it. A changed systemd
unit file is shipped like any tree file and the release prints that the unit must be reinstalled by hand: no
script installs units today.

When no `tree` path changed the release is exactly today's code-only release.

### Migrations

The schema gate moves out of `deploy-full-release.sh` into one script both releases call. A hot release whose
diff holds a `migration` path runs it; "behind" refuses and lists the pending revisions (the owner applies them
on the Windows workstation, as decision 0002 says); "at head" continues. Nothing on the peer host ever runs a
migration.

### The image procedure

`deploy-full-release.sh` stays as the only path that builds, renamed in the CLI (`release owner image`, with
`owner full` kept as an alias). Before `docker compose build` it reads the host's free memory and swap and
refuses under a threshold unless `RELEASE_ALLOW_BUILD_ON_HOST=1`. Building elsewhere and loading the image on the
host is the intended way and is written when first needed (BACKLOG item), not now.

### One command

`scripts/release owner code <sha> <label> --from-sha <active> [--apply]` is the hot release for `runtime`,
`migration` and `tree` together. `scripts/release plan` prints which of the three steps a target needs, or that
it needs an image and why.

## Steps (worktree `owner-hot-releases`, disposition merge into main; four dispatches of about 15 minutes)

1. The classifier and its tests: the five kinds, the default, the COPY-derived test;
   `deploy-code-only.test.mjs`, `test_release_unit_isolation.py`, `test_hot_update_paths.py`,
   `config/components.json` follow.
2. The schema gate as a shared script; `deploy-full-release.sh` calls it (its tests unchanged in meaning);
   `deploy-code-only.sh` calls it for `migration` paths; tests for "at head", "behind", "unknown".
3. `switch-owner-tree.sh` with tests on a temporary tree (hard links leave the old tree intact; changed, added and
   deleted files; the overlay is in the new tree; a failing validation leaves the symlink where it was; rollback;
   pruning); `deploy-code-only.sh` sends the tree files and calls it; the dry run lists the tree files.
4. `release_plan.py` and `scripts/release` (the wording, `owner image`); the memory guard of the image path;
   `AGENTS.md` (both sections), `docs/UNIFIED_RELEASE_MODEL_47.md`, `docs/RELEASE_SYNC_47.md` F5,
   `docs/decisions/00NN-one-image-hot-releases.md` (the next free number), the scripts catalog if a rule is
   needed for the new script.

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
report the target; the guard timer's next run succeeds from the new tree (`systemctl --user status` of the heal
unit); the previous tree is intact. A rollback is rehearsed once on purpose (switch back, recreate), in an
allowed window, with the user present.

## Risks

- A hard-linked tree shares inodes: anything that edits a tree file in place would change both trees. The step
  only ever unlinks and rewrites; the test asserts the old tree's bytes are unchanged after a release.
- Compose changes recreate what compose decides to recreate, tunnels included; a tunnel recreate reconnects every
  database connection. The dry run prints the compose diff so the operator sees it before `--apply`.
- The default `tree` could carry a file that should not be on the host. The archive holds only the changed paths
  of the diff, and `.env`, secrets and gitignored files are never in git.
- The two tools copied into the image go stale between image builds. Accepted; they are run by hand, rarely.

## Out of scope

Building the image off the host and loading it (written when first needed); installing systemd units from a
release; the edge units; the daily jobs themselves (W011).
