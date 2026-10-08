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

## Web console

The collector serves its operational console at
`http://127.0.0.1:18790/xhs/`. Keep the listener on loopback and reach the edge
instance through the existing SSH tunnel. The page receives a process-scoped,
HTTP-only runtime session; the collector token, XHS Cookie and Feishu webhook
remain on the server. It covers pipeline status, recommendation decisions, topic
versions, following candidates, the watch list, the complete registered
Spider_XHS operation surface and the XHS-specific Feishu delivery ledger.
Operations use the same allowlist and explicit mutation confirmation as the
Feishu command lane. Results are sanitized before rendering or optional Feishu
delivery.

The `单篇解析` view accepts an official note URL, copied share text, or an
`xhslink.com` short URL. Edge resolves and validates each redirect, fetches the
note with the existing Cookie, and queues a `single_note_analysis` job for the
Mac worker. The canonical note URL is persisted without signed query material.
Results remain in the console by default; an explicit checkbox puts the final
analysis into the existing Feishu delivery ledger. Identical note revisions
reuse the same AI result.

The dashboard is dependency-free static source under `dashboard/`. Both the
immutable image and the XHS source overlay include it. Future HTML, CSS,
JavaScript or Python-only changes use `scripts/hotfix-xhs-intel-edge.sh`;
Dockerfile and runtime dependency changes require an image release.

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

## Recommendation and topic lanes

The daily recommendation lane is separate from keyword/watch collection. It
uses the bounded `homefeed_recommend` category, collects at most 50 notes, and
creates a `classify_recommendations` job before any summary job. The Mac worker
must return strict JSON decisions covering every candidate; only `include`
items reach the existing Feishu delivery ledger. `review` and `exclude` rows
remain durable for auditing and threshold tuning.

The initial Topic taxonomy is seeded in `config.py` and copied into versioned
SQLite policy rows. Adding a topic is a data/config change; a run stores its
policy hash so old results remain reproducible. The following sync is read-only
and stores an external account snapshot in `following_accounts`. It is not the
same thing as `watch_users`, which is the internal monitoring list, and it does
not call the platform follow mutation API.

Useful Feishu commands are:

```text
#xhs intel scan recommendations 50
#xhs intel recommendation latest
#xhs intel topic list
#xhs intel following sync
#xhs intel following list
```

The n8n workflow schedules following snapshot sync at 07:40, the existing
keyword lane at 08:00, and recommendation filtering at 08:20. n8n only starts
the Edge jobs; the Edge delivery loop remains the single Feishu sender.

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
compose service and the workflow together with
`xhs-intel/scripts/deploy-xhs-intel-edge.sh`.

The source checkout is supplied at deployment time as `/opt/xhs` in the edge
container. Cookies and all runtime state remain outside git. Because
`Spider_XHS` is currently outside this monorepo, the deployment refuses a dirty
checkout by default and records its Git commit and deterministic source-tree
SHA-256 in `/opt/feishu-relay-edge/xhs-manifest.json` and the collector health
payload. A deliberately dirty deployment requires `XHS_ALLOW_DIRTY_SOURCE=true`
and is marked in that manifest, so it must not be described as reproducible.

The deployed compatibility fixes use upstream base `ebb6c4f` and patched commit
`83132b3`. The exact external Git history is mirrored in this repository's
`vendor/spider-xhs-20261008` branch because the deployment account cannot write
to `cv-cat/Spider_XHS`. This branch is provenance only and must not be merged
into the monorepo mainline.

## Standalone collector runtime

The collector image can be built from this directory alone. The external
`Spider_XHS` checkout is an explicit runtime mount and must be pinned by both
commit and tree digest:

```bash
export XHS_SOURCE_ROOT=/srv/Spider_XHS
export XHS_COOKIE_FILE=/etc/xhs/xhs-cookie
export XHS_COLLECTOR_TOKEN='local-token'
export XHS_SOURCE_GIT_SHA='pinned-commit'
export XHS_SOURCE_TREE_SHA256='pinned-tree-sha256'
docker compose -f compose.standalone.yaml up --build
curl http://127.0.0.1:18790/health
```

The standalone image does not copy `Spider_XHS`, Feishu credentials, or a
webhook into the build context. `/v1/run` remains blocked without a valid XHS
cookie; `/health` is available for runtime verification. `local_worker.py` is a
separate optional worker and communicates with this API over its token-protected
HTTP contract.

The standalone image installs `requirements.lock`; update it deliberately when
the collector dependency set changes. The legacy integrated image may continue to
use `requirements.txt` during the transition, but it is not the independent
runtime acceptance path.
