# XHS intelligence bridge

This directory contains the small cross-host service used by the XHS daily
workflow.

* `edge_api.py` runs on 47edge. It owns XHS cookies, collection, SQLite state,
  deduplication and Feishu delivery. It never receives a model key.
* `local_worker.py` runs on the workstation. It uses the Paper-KB
  `codex_provider.py` contract, including its local endpoint, key rotation and
  model fallback chain.
* The workstation supervisor owns an SSH local forward from `127.0.0.1:18790`
  to the edge API. If the workstation is offline, the edge queue retains
  `pending`/`processing` jobs and leases them again after expiry.

The edge API is a complete image/service deployment. It is not compatible with
the Feishu adapter source-overlay hotfix path: deploy `xhs-intel/Dockerfile`,
`Spider_XHS`, the edge compose service and the workflow together with
`deploy-xhs-intel-edge.sh`.

The source checkout is supplied at deployment time as `/opt/xhs` in the edge
container. Cookies and all runtime state remain outside git.
