# XHS intel maintenance boundary

Component `xhs-intel` (runtime `47edge`, release unit `edge-xhs`).

- **Hot update, never an image rebuild** for ordinary Python or dashboard changes:
  `xhs-intel/scripts/hotfix-xhs-intel-edge.sh --apply` (dry run without
  `--apply`). It runs this component's tests, publishes `*.py` and
  `dashboard/*.html|css|js` as a versioned
  overlay to `hotfix-xhs/{releases,current}`, recreates only `xhs-collector`
  with `--no-build --pull never`, verifies that PID 1 is actually running the
  overlay, and restores the previous release if the health gate fails.
- It fails closed and sends you to the image path
  (`xhs-intel/scripts/deploy-xhs-intel-edge.sh`) when
  `requirements.txt` differs from the running image, when `Dockerfile` changes,
  or when the external Spider_XHS source changes.
- Spider_XHS is outside this repository. Any XHS release must record its commit
  or content digest; do not claim a reproducible release until it is pinned.
- Cookies, collector tokens and Feishu webhooks stay in ignored host env files,
  never in a source release and never in `config/`.
- Do not import from `app` (quant) or `feishu-relay`, and do not reference their
  directories; `scripts/verify_component_boundaries.py --check` enforces it.
- Tests: `python3 -m unittest discover -s . -p 'test_*.py' -q` and
  `node --check dashboard/app.js`. Test files are
  excluded from the overlay — keep them named `test_*.py` so the publisher's
  rsync filter keeps them out of the runtime.

Tests this component must pass before commit (from `config/components.json`):

```
python3 -m unittest discover -s xhs-intel -p 'test_*.py' -q
node --check xhs-intel/dashboard/app.js
node xhs-intel/scripts/deploy-xhs-intel-edge.test.mjs
```
