# Component contracts

The three deployable projects communicate through versioned HTTP/event boundaries. A
component may be moved to its own repository when its local `component.json`,
standalone compose file and contract tests travel with it. No component may import
another component's Python, JavaScript or database modules.

## Feishu relay to quant research

`feishu-relay` uses `QUANT_SERVICE_URL` for read-through research data and for the
explicitly authenticated archive/raw-overflow write routes. The relay only sends
JSON over HTTP; it does not connect to the quant PostgreSQL database. Quant exposes
`/health` as the liveness contract and `/openapi.json` as the route contract. Any
route rename requires the mounted OpenAPI check and regenerated frontend types.

The relay must treat a non-2xx response, an empty response where coverage is
required, or a stale provider timestamp as an evidence failure. It must never turn
that response into a trading or order decision.

## LarkAgentX bridge to Feishu relay

The bridge posts normalized, message-level-idempotent events to the adapter's
`/internal/larkagentx/inbound`, `/internal/larkagentx/group-relay` or
`/internal/larkagentx/summary` endpoint, protected by `x-larkagentx-token`.
The adapter owns the durable relay ledger. The bridge owns the WebSocket cursor,
spool and per-group history export. `GET /api/group-relay/larkagentx/history/export`
is a JSONL evidence export and carries a sequence cursor for incremental reads; it
does not promise history that was never received by the WebSocket.

## XHS collector to Feishu relay

The collector exposes `/health` without credentials and token-protected `/v1/run`
and `/v1/status` endpoints using `X-XHS-Collector-Token`. The Feishu adapter or n8n
forwards an explicit `#xhs` command; the collector returns a bounded, durable job
result and never receives a model key. `Spider_XHS` is an external dependency and
must be identified by both Git commit and source-tree digest in the runtime
manifest.

## Compatibility rules

- Patch changes keep the documented paths and fields backward compatible.
- A new required field, authentication rule, database column, or external source
  version increments the interface contract and requires a coordinated release.
- Component releases record their own source SHA, image/digest and migration head;
  a global repository SHA is not evidence that all runtime units are running the
  same code.
- Secrets, cookies, tokens, webhooks and local profile directories remain host
  configuration and never enter component archives.
