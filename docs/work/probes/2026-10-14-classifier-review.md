# Review of a3783f22 (owner path classifier, schema gate)

Reviewed commit: `a3783f227cf7983add76443ea37daad3522363d8`, parent `39bcc7ee`, worktree
`/Users/papa/codebase/.codex-wt/n8n-owner-hot-a`.

The branch moved twice while I was reading: `3288e1ed` and `da300c04` now sit on top. Neither touches the
classifier, the gate, the two deploy scripts, `release_plan.py`, the catalog or the tests; they change
`config/components.json`, some docs (host names in `docs/RELEASE_SYNC_47.md`, `OPERATIONS.md`) and
`scripts/sync-xiaojie-message-features.sh`. Line numbers below are those of `a3783f22`. The named tests were
run on the moved tree (node test: all 10 cases pass; pytest: 67 passed); the files they exercise are
byte-identical to `a3783f22`.

Evidence I produced (same scratchpad directory): `old.tsv` / `new.tsv` (whole tree, old and new rules),
`full2skip.txt` (the 212 paths that moved from `full` to `skip`), `exp1.sh` + `exp1.out` (classifier and dry-run
cases in a throwaway repository), `exp2.sh` + `exp2.out` (planner verdicts).

Whole-tree transitions, old rules to new: full→full 23, full→image 3, full→migration 116, full→skip 212,
runtime→migration 1 (`quant-service/alembic.ini`), runtime→runtime 579, skip→skip 1290, plus the non-ASCII docs
file (full→skip, the quoting fix).

## Findings

### 1. HIGH (verified) — `scripts/fill-longhu-sector-membership.py` is now `skip`, and the owner host runs it from its tree

- Where: `scripts/shared-peer/classify-owner-paths.sh:39-43` (`*) echo skip`),
  `scripts/generate_scripts_catalog.py:121` (`fill-*.py` → "quant container"), `scripts/CATALOG.md:127`.
- The script's docstring (`scripts/fill-longhu-sector-membership.py:8-12`) and `OPERATIONS.md:374-377` ("在 peer 上
  刷新") give the procedure: on the peer, `docker cp scripts/fill-longhu-sector-membership.py
  trading-hareness-peer-quant-research-1:/tmp/fill.py`, then `docker exec`. The file is taken from the host's
  tree. It imports `app.longhu_sector_membership`, `app.longhu_vendor_source`,
  `app.sector_membership_repository`, which ship by overlay.
- Input: a commit that changes this script (alone or with the app modules it imports). Old rules: `full`. New
  rules: `skip`; `release_plan.py` prints "nothing reaches the owner runtime" (`exp2.out` P5). The host keeps the
  old copy, and the operator following OPERATIONS.md runs it against new app code.
- Cause: `script_kind` treats every label that does not start with `47owner` as skip. The labels in the catalog
  today are workstation (104), owner-windows (20), "workstation (some on the edge)" (18), "workstation and CI"
  (14), "never run" (6), "no host runs it" (1), 47owner (10), "47owner (inside the container)" (3) and "quant
  container" (1). The one label outside the known sets is this one. `scripts/cutover/*`, which reach the
  container the same way, are labelled "47owner (inside the container)" and come out `full`.
- Smallest fix: label `fill-*.py` `47owner (copied into the container)` in the generator, and make
  `script_kind` name the labels it skips (`workstation*`, `owner-windows`, `never run`, `no host runs it`) with
  `*) echo unclassified`, so a new or mistyped label is refused instead of skipped.
- Suspected, same pattern: `scripts/backfill-eastmoney-hot-rank-history.py:12` and
  `scripts/probe-public-sources.py:9` say "docker exec trading-hareness-peer-quant-research-scheduler-1 python
  /tmp/…" without saying where the file comes from; both are labelled workstation. If they are copied from the
  host's tree, they have the same problem.

### 2. HIGH (verified by run, `exp1.out` E2a/E2b; needs a mislabelled row first) — correcting a catalog row never ships the script

- Where: `scripts/shared-peer/classify-owner-paths.sh:34-44, 113-114`.
- Input: commit 1 changes `scripts/host.sh` while its row says workstation → `skip`, released code-only. Commit 2
  corrects the row to 47owner and leaves the script alone. The diff holds only `scripts/CATALOG.md` and
  `scripts/generate_scripts_catalog.py`, both `skip`, so the plan says nothing reaches the owner. No later diff
  contains the script, so the host's copy stays at the version before commit 1.
- This commit corrects one row that way (`backfill-full-market-daily.sh`). It is harmless there only because the
  old rules had every such script as `full`. It will bite the fix for finding 1 if the script changes between
  this commit and the relabel.
- The reverse case (E10: a 47owner script changed and relabelled workstation in one commit) comes out `skip`;
  the host keeps the old copy.
- Smallest fix: compare the two catalogs. For every script whose "Runs on" starts with `47owner` at one commit
  and not at the other, print `full\tscripts/<name>` even when the script is not in the diff.
- The same gap exists for any rule edit in `classify-owner-paths.sh` that moves a path from skip to full: the
  host's tree is at the last full release, but paths are classified against the last code-only SHA.

### 3. MEDIUM (verified by reading; the verdict predates the commit, the claim is new) — the tunnel image's inputs are `full`, and a full release does not rebuild or recreate a tunnel

- Where: `scripts/shared-peer/classify-owner-paths.sh:89-91` ("the tunnel image"), `docs/RELEASE_SYNC_47.md:547`
  ("隧道镜像"), `scripts/release_plan.py:88` ("the image is rebuilt with it").
- Input: a change to `deploy/shared-peer/ssh-tunnel-entrypoint.sh` or `Dockerfile.ssh-tunnel`. Code-only says
  "full release required for"; the planner says "FULL release … it ships the host's tree; the image is rebuilt
  with it" (`exp2.out` P1). `deploy-full-release.sh` builds only `quant-research` (:169) and brings `db-tunnel`
  up with `--no-build` and no recreate (:122); `db-batch-tunnel` is not touched. The file reaches the tree, the
  running tunnels keep the old entrypoint, and every check passes.
- `docs/work/plans/owner-hot-releases.md:66` already names a `tunnel-image` kind for this ("refused by every
  release … done by hand with the owner").
- Smallest fix: an arm before `deploy/shared-peer/*` for those two files, refused with that message; or at least
  remove "tunnel image" from the three texts.

### 4. MEDIUM (verified by run, `exp1.out` E1; predates the commit, on a line it edits) — a moved file is classified by its new name only

- Where: `scripts/shared-peer/classify-owner-paths.sh:28`.
- `git diff --name-only` detects renames and prints only the destination. `git mv scripts/moved.sh docs/moved.sh`
  (a 47owner script) gives `skip docs/moved.sh`; `git mv quant-service/app/old.py quant-service/tests/old.py`
  gives one `skip` line, so the planner reports nothing for the owner although the app tree changed. The comment
  at :32-33 ("a script deleted by the target is in the catalog of the commit it comes from") holds for a
  deletion and not for a move.
- Smallest fix: add `--no-renames` on :28.

### 5. MEDIUM (verified by run, `exp1.out` E4/E4b) — the dry run says "the owner database is at their head" when the gate passed on an override

- Where: `scripts/shared-peer/deploy-code-only.sh:65`.
- Input: `RELEASE_DB_REVISION='' RELEASE_ALLOW_SCHEMA_REVISION=none` (or an unknown revision named exactly). The
  gate prints "schema: owner database unreadable … unverified (allowed)", and three lines later the dry run
  prints "migrations in the overlay (the owner database is at their head):".
- Smallest fix: drop the parenthesis, or say "(schema gate passed; see the schema: line)".

### 6. MEDIUM (verified by run, `exp1.out` E11; not a regression) — the gate is keyed on the diff, not on the state

- Where: `scripts/shared-peer/deploy-code-only.sh:56`.
- Input: target head m2, owner database at m1, `--from-sha` a commit that already contains m2. The diff holds no
  migration path, the gate is not run, and the dry run exits 0. With a from-sha before m2 the same release is
  refused with exit 6.
- Ways to get there: a wrong `--from-sha` (nothing compares it with the host's `PEER_APP_GIT_SHA`); an earlier
  release let through with `RELEASE_ALLOW_SCHEMA_REVISION`; a database restored by the owner.
- The old code-only path had the same blind spot, but every migration then forced a full release, which always
  ran the gate. With migrations shipping hot, full releases become rare and that recurring check goes with them.
- Smallest fix: run the gate on every code-only release (drop `[[ -z "$migration_files" ]] ||`). The cost is one
  ssh read per run, dry run included.

### 7. MEDIUM (verified by reading plus the classifier output) — the COPY test accepts `skip` for anything the image copies

- Where: `scripts/test_owner_path_classes.py:35-43` (allowed set on :41), `quant-service/Dockerfile.peer:23-24`.
- `scripts/audit-fuyao-capabilities.py` and `scripts/import-stock-brain-portfolio.py` are copied into the image
  and are `skip` (catalog: workstation; old rules: `full`). The plan records this as deliberate (:67, "refreshed
  when an image is next built"). I found no caller of the image's copies anywhere in the tree, so today the
  effect is two stale unused files.
- The test cannot tell deliberate from accidental. A future `COPY scripts/x.py` whose name matches a broad
  generator glob (`import-*.py`, `verify-*`, `backfill-*`, `generate*`) is `skip`, and the test passes while the
  image's copy goes stale. A COPY source that matches no tracked path also passes (empty set).
- Smallest fix: assert the exact set of skipped COPY sources (`quant-service/tests` and the two tools), and that
  every source matches at least one path.

### 8. MEDIUM (verified by reading) — behavioural cases the tests do not have

- `test_a_script_the_hosts_units_run_is_part_of_the_hosts_tree` (`scripts/test_owner_path_classes.py:28-33`) ties
  only `ExecStart=` scripts. Not tied: `shared-peer/release-lock.sh` (called by `peer-session-guard.sh:35` and by
  the remote halves of both deploy scripts), `shared-peer/activate-peer-release.sh` (run by the full release),
  and the scripts run by hand on the host (`cutover/*`, `verify-owner-cutover.py`, finding 1's script).
  Relabelling any of them gives `skip` with every test green.
- No case for a catalog label other than workstation, owner-windows or 47owner. That is where finding 1 sits.
- No case for the from-commit catalog (a deleted script), for `quant-service/alembic.ini` or
  `quant-service/migrations/env.py` reaching the gate, or for `.dockerignore` → `image`. Moving `alembic.ini`
  back to `runtime` fails no test.
- `test_every_tracked_path_has_a_rule` classifies the empty tree to HEAD. It cannot see: a path that exists only
  at the from commit (deleted or moved away); results that depend on the pair of commits (docstring-only
  migration, from-catalog lookup); a wrong `skip` (it asserts only "not unclassified"); and the classes of path
  in finding 9, none of which is tracked today.
- `scripts/test_hot_update_paths.py:112-126` and `scripts/test_release_unit_isolation.py:83-93` assert on source
  text; the mjs test covers the same refusals and the gate by behaviour, so these are a second line only.

### 9. LOW (verified by run, `exp1.out` E6) — some paths under `scripts/` are refused with a remedy that cannot work, others are skipped by glob accident

- `scripts/pkg/__init__.py`, `scripts/shared-peer/test_helper.sh`, `scripts/test_fixtures/sample.json` come out
  `unclassified`, and the message (`deploy-code-only.sh:48`, `release_plan.py:84-85`) says a script gets its row
  from the generator. The generator leaves these out on purpose (`generate_scripts_catalog.py:145`: any
  `(^|/)test_`, any `__init__.py`), so the row can never appear.
- `scripts/test_dir/host_tool.py` is `skip` with no catalog row, because `*` in a `case` pattern crosses `/`
  (`scripts/test_*.py`, :81). The same holds for `deploy/*.conf` (:106): `deploy/other/x.conf` is `skip` although
  no rule names `deploy/other/`.
- Smallest fix: skip exactly what the generator leaves out (`scripts/test_*`, `scripts/*/test_*`,
  `scripts/*/__init__.py`), and name the three gateway `.conf` files instead of `deploy/*.conf`.

### 10. LOW (verified by reading) — documents that say what the scripts do not do

- `docs/decisions/0002-forward-only-owner-migrations.md:31` ("deploy-code-only.sh 遇到迁移文件的实质改动直接拒绝")
  and `docs/decisions/0003-owner-full-release.md:23-24` ("迁移、依赖和镜像的变更必须走全量发布") are now false, and
  no new decision record replaces them. `docs/RELEASE_SYNC_47.md:652` still lists a migration change under the
  complete release.
- "An unclassified path is refused / nothing is built for it" (`classify-owner-paths.sh:19-21`,
  `AGENTS.md:172, 240`, `release_plan.py:82-85`) holds for the code-only path only.
  `deploy-full-release.sh` never runs the classifier: `release owner full <sha> <label> --apply` ships and builds
  a commit that holds unclassified paths.
- At `a3783f22` itself, `config/components.json:361-363`, `docs/COMPONENT_CONTRACTS.md` and
  `quant-service/AGENTS.md` still said a migration needs the full release. `3288e1ed` fixes them; merge the two
  together.
- The planner prints the `release owner code … --apply` line (`release_plan.py:96`) also when its own schema line
  says the database is behind (`exp2.out` P2); that command exits 6.
- `deploy-code-only.sh:4-8` (usage) does not say that the dry run now reads the owner's `/health` over ssh when
  the diff carries a migration; the full release's header says so for its own check.

### 11. LOW (verified by reading) — style

- `classify-owner-paths.sh:34`: `2>/dev/null || true` on both `git show`. After :28 the only failure left is "no
  catalog at this commit", and the result is every script reported as "unclassified: add its rule" (E16) with
  the real cause hidden.
- `deploy-code-only.sh:41-49`: one loop written three times.
- The owner host, port and key defaults are now in four places (`schema-gate.sh:20-22`,
  `deploy-code-only.sh:73-75`, `deploy-full-release.sh:69-71`, `release_plan.py:27-28`).

## Checked and found sound

- Full release behaviour: the gate body in `schema-gate.sh` is identical to the removed block except the trap
  line (diffed); exit codes 6 and 2, messages and streams are unchanged; the child's trap removes its own temp
  directory and the parent's `cleanup` still removes the archive; `|| exit $?` passes the child's code through;
  `scripts/test_deploy_full_release.py` passes.
- `[[ -z "$migration_files" ]] || bash … || exit $?` parses as intended (E11, E15): no gate without a migration
  path, the gate's exit code otherwise.
- The three refusal loops with zero paths of a kind, and with all three kinds at once (E9): each path named once,
  exit 1.
- Every changed path under `quant-service/migrations/` reaches the gate unless only comments or docstrings
  changed: added, deleted, moved, mode-only (skip), syntax error (migration), non-`.py` under `versions/`
  (migration), `alembic.ini`, `env.py`, `contracts/*` (E6, E13, E14, E15, `new.tsv`).
- Catalog parsing: `|` in a purpose (the generator replaces it, and the purpose is the last column), names with a
  space or glob characters, subdirectory names, exact match in awk, a script in neither catalog (unclassified),
  a deleted 47owner script (full, from the from-commit's catalog), no catalog at either commit (unclassified),
  the non-ASCII path (E3, E5, E16).
- Arm order: `versions/*.py` before `quant-service/migrations/*`; `deploy/shared-peer/*` before `deploy/*.conf`;
  `scripts/data/*` before the catalog arm (the generator leaves the same directory out); `*.md` under
  `deploy/shared-peer/` and `quant-service/migrations/` is skipped, which is harmless.
- The other paths that moved from `full` to `skip`: `scripts/marketdata/*` (edge and workstation; nothing in the
  host's tree calls them), `scripts/run-post-close-pipeline.sh` (the Mac's launchd), `scripts/data/*` (named only
  in comments), `config/*` (the image copies no `config/`, compose mounts none), `deploy/intraday-edge/*`,
  `deploy/post-close/*`, `workflows/*` (the peer's n8n service is behind a compose profile and mounts no
  workflows), `launchd/*`, root `compose.yaml`, `quant-service/Dockerfile`, the `.ps1` scripts, the workstation
  release tooling. The host's own scripts call only `shared-peer/release-lock.sh`, which is `full`.
- `Dockerfile.peer` COPY sources other than the two tools: app, entrypoint, run_server, database_bootstrap
  (runtime), alembic.ini and migrations (migration), requirements.txt (image), tests (skip; nothing in the app
  imports `tests`). Build context `../..` makes the root `.dockerignore` the right image input.
- Planner verdicts for mixed diffs: unclassified with image or full → NOT RELEASABLE and no full command;
  migration with full → FULL plus the schema line; migration alone → code-only with the "at their head first"
  note (`exp2.out`, `scripts/test_release_cli.py`).
