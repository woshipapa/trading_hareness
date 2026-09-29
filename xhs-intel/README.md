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

## Feishu command surface

The adapter accepts `#xhs` in the bound XHS group and forwards the command to
the edge API. The edge runtime exposes the public Spider_XHS operations through
an explicit namespace allowlist:

```text
#xhs help
#xhs search AI基础设施 5
#xhs note https://www.xiaohongshu.com/explore/<note-id>
#xhs user <user-id>
#xhs comments https://www.xiaohongshu.com/explore/<note-id>
#xhs api pc.search_note {"args":["GPU"],"kwargs":{"page":1}}
#xhs api creator.get_all_posted_notes {"args":[],"kwargs":{}}
#xhs api live.get_chats {"args":[],"kwargs":{"limit":20}}
#xhs api pgy.get_user_detail {"args":["<kol-id>"],"kwargs":{}}
#xhs api qianfan.get_user_fans {"args":["<distributor-id>"],"kwargs":{}}
#xhs watch add <user-id> [备注]
#xhs watch list
#xhs watch remove <user-id>
```

The watch lane polls the first page of each configured user's posted notes every
30 minutes. XHS does not provide a dependable push event for ordinary note
publication, so this is bounded polling rather than a real-time subscription.
New note revisions use the same durable queue, local `codex-teleai` worker and
Feishu delivery ledger as the keyword lane. A profile URL is accepted by the
`watch add` command, but only its user id is persisted; signed URL parameters
are never stored.

PC collection, Creator metadata/publish methods, live and IM HTTP methods,
蒲公英 KOL methods and 千帆 distributor methods all use the checked-in public
method registry in `operations.py`. Account-changing operations require
`{"confirm":true}` in `kwargs`. Creator media must already be staged under
the edge-only `XHS_MEDIA_ROOT` directory (default
`/var/lib/xhs-collector/inbox`); Feishu messages cannot read arbitrary host
paths. Upstream login prompts and interactive category selectors remain host
operations because they require a QR/SMS or terminal interaction.

The edge API is a separate image/service deployment. The Feishu adapter and
LarkAgentX bridge can be updated through the existing source-overlay path
without rebuilding their immutable image. The XHS collector image is built on
the first deployment, or when its Python/Node source or dependencies change;
workflow, runtime environment and Cookie rotations do not require rebuilding
the adapter image. Deploy `xhs-intel/Dockerfile`, `Spider_XHS`, the edge
compose service and the workflow together with `deploy-xhs-intel-edge.sh`.

The source checkout is supplied at deployment time as `/opt/xhs` in the edge
container. Cookies and all runtime state remain outside git. Because
`Spider_XHS` is currently outside this monorepo, the deployment refuses a dirty
checkout by default and records its Git commit and deterministic source-tree
SHA-256 in `/opt/feishu-relay-edge/xhs-manifest.json` and the collector health
payload. A deliberately dirty deployment requires `XHS_ALLOW_DIRTY_SOURCE=true`
and is marked in that manifest, so it must not be described as reproducible.
