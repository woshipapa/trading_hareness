# XHS intelligence bridge

This directory contains the small cross-host service used by the XHS daily
workflow.

* `edge_api.py` runs on 47edge. It owns XHS cookies, collection, SQLite state,
  deduplication and Feishu delivery. It never receives a model key.
* `local_worker.py` runs on the workstation. It uses the Paper-KB
  `codex_provider.py` contract, including its local endpoint, key rotation and
  model fallback chain. Jobs are routed by complexity: screening jobs and
  small watch summaries lead with `gpt-5.6-luna`/medium, while large
  summaries, single-note analyses and the daily digest lead with
  `gpt-5.6-sol`/high; both chains fall back through the Paper-KB default
  chain. Tune with `XHS_AI_SIMPLE_MODEL`/`XHS_AI_SIMPLE_EFFORT`,
  `XHS_AI_COMPLEX_MODEL`/`XHS_AI_COMPLEX_EFFORT`,
  `XHS_AI_SIMPLE_SUMMARY_MAX_NOTES` (default 3), or disable with
  `XHS_AI_MODEL_ROUTING=0`.
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
Mac worker. The canonical note URL in payloads carries no signed query
material; the newest per-note share token is persisted separately in the
`note_links` table (owner decision 2026-10-09), so `/xhs/open/<id>` redirects
and Feishu 原文直链 keep working across restarts for as long as the upstream
honors the token, with the durable local preview as the no-token fallback.
Cookies and session keys are never persisted.
Results remain in the console by default; an explicit checkbox puts the final
analysis into the existing Feishu delivery ledger. Identical note revisions
reuse the same AI result. The local worker runs separate `interactive` and
`batch` claim lanes in one supervised process, so a long recommendation batch
does not block a submitted article.

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

## Topic collection, screening and the daily digest

The daily keyword lane is topic driven. Every enabled topic carries its own
`search_keywords` (editable in the console topic dialog; a topic without them
falls back to its display name), and `/v1/topics/run` searches one bounded page
per keyword of every enabled topic — there is no artificial daily query budget;
the per-request delay and the collector's stop-on-risk-control behavior remain
the protection for the logged-in session. Notes and runs are tagged
`topic:<slug>:<keyword>`. `/v1/run` with an explicit keyword list stays
available for manual calls.

Keyword/topic notes then pass the same local AI screening as the
recommendation lane (`classify_notes` jobs against the live, console-edited
topic policy); every decision is durable in `note_topics` and only `include`
notes reach a summary. Watch-user notes come from hand-approved authors and
keep their direct, timely summary.

`/v1/digest/run` queues one idempotent `daily_digest` job per Asia/Shanghai
day (scheduled 09:30 by n8n). It gathers the last ~26 h of include-screened
keyword notes, include recommendations and watch notes, deduplicated by
revision and capped at 60, together with the previous digest text. The Mac
worker writes a per-topic Markdown digest with 今日导读 / 与昨日对比 / 待核验 /
继续跟踪 sections; the result is delivered through the existing Feishu ledger
and browsable (with history) in the console's 每日简报 view. Useful commands:
`#xhs digest latest`, `#xhs digest 2026-10-08`, `#xhs intel digest run`,
`#xhs intel scan topics`.

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

The n8n workflow schedules following snapshot sync at 07:40, candidate
screening at 07:50, the topic keyword lane at 08:00, recommendation filtering
at 08:20, and the daily learning digest at 09:30. n8n only starts the Edge
jobs; the Edge delivery loop remains the single Feishu sender.

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
