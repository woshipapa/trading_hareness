# Always-on Feishu relay edge

This deployment owns the user-OAuth group-history poller, durable relay ledger,
media retry state and the four inbound n8n webhook workflows. It binds n8n
(`127.0.0.1:5678`) and the adapter dashboard (`127.0.0.1:18300`) only to the
server loopback interface. PostgreSQL is the host's existing local instance;
the deployment uses a dedicated `n8n_relay` database and role.

Cutover order is deliberate: start the remote adapter with both pollers
disabled, restore the OAuth/ledger state and import the webhook-only workflows,
disable the local pollers, then enable the remote pollers. This preserves the
source cursors and message-ID dedupe boundary while avoiding duplicate group
forwarding. `FEISHU_RELAY_WRITER_ID` is a named writer generation recorded in
the relay ledger; it is an operational fence after a copied ledger, not a
cross-host lock (the two hosts have separate PostgreSQL instances). The remote
`relay.env` is mode 0640 and is not stored in git.

The workstation keeps a dedicated `feishu_relay_edge_ed25519` key under its
private `.ssh` directory. Both handoff scripts use it by default; another
operator can override it through `RELAY_EDGE_SSH_KEY` without placing a key in
this repository.

The summary-listener ingestion path is two phase: it durably stores the source
message, parsed content and local media before writing one
`ingestion_delivery_outbox` row. A leased worker then calls n8n. A transport
outage therefore retains the original `message_id` for retry instead of being
mistaken for a completed delivery. The failover snapshot includes this outbox
and its media files.

Only these workflows are imported: text aggregation plus media-part,
media-finalize and media-state callbacks. Local scheduled market workflows are
not copied, so this relay deployment cannot duplicate local research schedules.

The XHS edge add-on is deployed separately by
`xhs-intel/scripts/deploy-xhs-intel-edge.sh`. It keeps collection and
Feishu delivery on the edge while the local worker performs AI summarization.
The edge n8n process uses the matching `n8nio/runners` sidecar in external mode;
the runner token is generated into the protected `secrets.env` on first apply.
XHS overrides the global success-retention setting so its successful executions
remain visible for 72 hours. When success retention is disabled, n8n 2.33.7
sets `deletedAt` without saving final status or node outputs: raw rows can still
say `running` even after success. Exclude soft-deleted rows from live execution
diagnostics; never mark them crashed based on age alone. The deployer backs up
and compares the workflow, then stops n8n before importing changed definitions.

## Versioned release

The adapter source is built by the GitHub `edge-*` release workflow into an
immutable GHCR image. The remote runtime selects that image through the ignored
`FEISHU_ADAPTER_IMAGE` value in `runtime.env`; it does not build from an
uncommitted server directory. Both `/health` and the quant edge health endpoint
publish a non-secret `build` object with `git_sha`, `release`, and
`build_created_at`.

Use `feishu-relay/scripts/edge/export-edge-relay-workflows.sh` to refresh the redacted canonical
workflow JSON under `workflows/edge-relay/`, then use
`feishu-relay/scripts/edge/verify-edge-relay-workflows.sh` before a deployment. The latter is
read-only and fails on workflow drift. Both commands use the dedicated
`feishu_relay_edge_ed25519` key by default (or `RELAY_EDGE_SSH_KEY` when an
operator supplies a different key), never an interactive password prompt.
Version IDs, execution counters, timestamps and n8n static runtime state are
deliberately ignored by the drift comparison; node graphs, connections,
settings and callback URLs are not.

`feishu-relay/scripts/edge/deploy-feishu-relay-edge-release.sh <git-sha> <release-label>` is a
dry run by default. `--apply` pulls the immutable image, updates only the
non-secret release keys in remote runtime configuration, and recreates the
adapter while retaining its durable PostgreSQL relay ledger and media volume.

For a committed webhook workflow revision, use:

```bash
bash feishu-relay/scripts/edge/deploy-edge-relay-workflows.sh <git-sha> --apply
```

It exports a retained rollback copy on the edge, renders the redacted remote
archive base URL there, stops n8n before changing its persisted graph, publishes
all four workflow versions in one transaction, and then verifies the live
export against the committed source. It refuses an uncommitted worktree or a
workflow containing the obsolete Docker-only callback address.

The remote archive control plane has one Gunicorn worker, so a stuck upload can
otherwise exhaust all of its request threads. Install the version-controlled
watchdog after a release with:

```bash
bash feishu-relay/scripts/edge/install-edge-import-watchdog.sh --apply
```

Every minute it expects the deliberately unsupported `GET /api/v1/imports/batches`
to return `401`, `403`, or `405`; a timeout or unexpected response restarts only
`stock-reports-import.service`. It neither creates an import batch nor touches
the durable relay ledger.

### GHCR pulls from the edge stall — the release script now recovers on its own

Docker Hub / GHCR blob delivery straight to `47.114.113.152` has repeatedly
stalled mid-layer (TLS handshake timeout on one blob) while the same image
pulls fine from a workstation. `deploy-feishu-relay-edge-release.sh` no longer
needs a manual rescue for this: it first tries the pull on the edge, and only
if that fails or times out does it pull the image locally and ship it over the
same SSH connection (`docker save | gzip -1 | ssh … docker load`) before
applying the release. Nothing to do differently — just run `--apply` and it
falls back automatically.

### Iterating on a small change without rebuilding an image

The fast path separates dependencies from application source. The edge keeps
the last verified adapter image (including Node and `node_modules`) and mounts
`/opt/feishu-relay-edge/hotfix` read-only at `/app/hotfix`. Each hotfix is
uploaded to `hotfix/releases/<release-id>/`; adapter syntax, JSON, Python
syntax/import and dependency manifest checks run there before
`hotfix/current` is switched atomically. The container is then recreated with
`--no-build --pull never`, so an adapter, frontend or LarkAgentX bridge fix
does not contact Docker Hub/GHCR and does not rebuild an image. The deployer
records the running image digest and refuses the activation if it changes
during the hotfix.

The overlay includes the adapter modules, the standalone Feishu dashboard
`frontend-dist`, the quant research console `quant-frontend-dist`,
`feishu-relay/config/source-registry.json`, and the bridge modules under `feishu-relay/bridge`.
The bridge's systemd unit is changed once to use a stable entrypoint; every
later update stages a new release, changes the same `current` pointer and
restarts the service.
Credentials, cookies and the LarkAgentX login state remain in the existing
受限 environment/state paths and never enter the release directory. The
deployer deliberately does not run npm or install Python packages. If
`feishu-relay/adapter/package.json`, the Node runtime, the supervisor venv or a
base-image requirement changes, the hotfix script stops before activation;
publish the corresponding immutable runtime release for that change. The
active overlay is health-checked together with the WebSocket bridge and the
webhook keyword coverage endpoint, and the last five overlays are retained for
rollback. A failed health check restores the previous pointer automatically.
The bridge health field may remain `connecting` immediately after a restart:
the upstream client has no on-open callback and changes it to `connected` after
the first decoded event; the deploy gate also requires the enabled systemd
process and a live health response.

```bash
feishu-relay/scripts/edge/hotfix-feishu-relay-edge.sh                         # tests + plan
feishu-relay/scripts/edge/hotfix-feishu-relay-edge.sh --apply                 # fast source update
feishu-relay/scripts/edge/hotfix-feishu-relay-edge.sh --list                  # retained versions
feishu-relay/scripts/edge/hotfix-feishu-relay-edge.sh --rollback <release-id> --apply
```

This remains an untracked runtime until the fix is committed and released.
`deploy-feishu-relay-edge-release.sh` now sets
`FEISHU_ADAPTER_HOTFIX_ENABLED=false` before starting the pinned image, which
prevents an old overlay from shadowing a normal release. The health endpoint
reports the base commit SHA plus a `hotfix-...` release label while the overlay
is active, making the running source distinguishable from an immutable image.

## Deterministic emergency failover to the workstation

From the repository root, run:

```bash
bash feishu-relay/scripts/edge/preflight-feishu-relay-handoff.sh
bash feishu-relay/scripts/edge/failover-feishu-relay-to-local.sh
```

The preflight is read-only: it proves the remote writer, local fenced standby,
n8n/adapter health and minimum edge free space before a handoff. The failover
script deliberately fails closed. It first disables both remote pollers,
copies the remote durable ledger (including the `source_message_id` primary-key
dedupe rows, source cursors, OAuth refresh state, media retry state and writer
generation) into the local database in a single transaction, promotes the
local writer generation, then starts the local pollers.
Consequently a source message already marked `sent` remotely cannot be claimed
or sent again locally. If the server cannot be reached to take that snapshot,
the script leaves local polling disabled instead of risking duplicate delivery.

## Deliberate failback to the edge

After the edge is healthy again, return ownership with:

```bash
bash feishu-relay/scripts/edge/failback-feishu-relay-to-remote.sh
```

It fences local polling first, copies the full ledger, outbox and retry-media
back to the edge, increments the edge writer generation, then enables only the
edge pollers. It is intentionally not automatic: a network partition must not
be allowed to create two group-history writers.

## Card messages (`msg_type: interactive`)

Feishu renders a card into a legacy 1.0 `{title, elements}` shape when the
message is read back through the API. A **card JSON 2.0** message (`schema:
"2.0"`, body of `markdown` / `div` / `img` elements) cannot be downgraded, so
the default read returns a fixed banner — `请升级至最新版本客户端，以查看内容` —
plus one shared image key whose resource is already deleted. Relayed as-is,
that banner reads like analyst content and carries none. The `#anqiang` bot
switched to 2.0 cards on 2026-09-08, the day the route moved to
安强训练营1（50）; every card that day arrived as the banner.

Both pollers therefore read messages with
`card_msg_content_type=user_card_content`, which returns the JSON the sender
actually posted (1.0 or 2.0; `schema` tells them apart). The relay then:

- walks `text` / `markdown` / `plain_text` / `lark_md` elements, `header.title`,
  `div.text` objects and `a` / `button` links into the portable text summary;
- collects `image_key` (1.0) and `img_key` (2.0) resources, uploads each to the
  target tenant through the existing image path, and relays a `post` carrying
  the card's text and images — a card image the API refuses is counted and
  skipped rather than failing the message;
- drops the upgrade banner before it can be mistaken for the card, and when
  nothing survives says `卡片内容无法通过接口获取…请在源群查看原卡片` instead.

The `portable_summary_version` string is unchanged on purpose: bumping it would
queue every already-delivered card for an in-place rewrite.

To check what the API returns for one card, run inside the adapter container
with the user OAuth client (the tenant token cannot read external groups):

```js
await oauth.userRequest(`/im/v1/messages/${messageId}`, { params: { card_msg_content_type: 'user_card_content' } });
```

## Outbound format: card JSON 2.0

Since 2026-09-08 every relayed message — text, rich text with images, direct
images, and cards — is sent to its target groups as a **card JSON 2.0**
(`msg_type: interactive`) built by `feishu-relay/adapter/card-content.mjs`:

- a `plain_text` div whose content begins with the route tag on its own line
  (`#anqiang\n…`), so text arrives exactly as typed and the summary-group
  ingestion, which routes on a leading `#tag`, reads a card the same way it
  read the bubble it replaces;
- one `img` element per image, keyed by the `img_key` the adapter uploaded.

Rich text carrying a video or file stays a `post` (a card cannot embed
either), and native file/media/archive deliveries are unchanged. An edited
source updates the delivered card in place through the card `patch` API
rather than sending a second bubble. Each ledger target entry records the
`msgType` it was delivered as; an edit rebuilds the payload in that same
shape, so a text/post bubble delivered before the upgrade (no recorded
`msgType`) is still edited through `update` as text/post — a card cannot
patch a text bubble (Feishu answers 400).

`FEISHU_GROUP_RELAY_OUTBOUND_CARD=false` restores the pre-09-08 text/post
shape for both the Feishu group relay and the WeChat relay; nothing already
delivered is re-sent either way, because the card path uses its own
deterministic message uuid.

The shared adapter endpoint serves the quant console at `/` and the standalone
Feishu Relay dashboard at `/monitor`. `/dashboard` is retained as a compatibility
alias for the group-listening page; `/workbench` and `/relay` remain Feishu routes.
