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

## Data-plane boundary

组件边界不只是 import。`scripts/verify_component_boundaries.py` 过去只检查
Python import 跨界和 `./`/`../` 路径引用，**看不见 SQL**，所以数据层的耦合长期
没有守卫。现在用 `config/components.json` 两组声明把它们钉住，新增一处就会红。

### 跨界数据引用（`foreign_data_contracts`）

| 对象 | 归属 | 消费方 | 唯一允许出现的位置 |
|---|---|---|---|
| `public.workflow_entity` | n8n（第三方） | quant-research | `quant-service/app/n8n_workflow_audit.py` |
| `public.workflow_published_version` | n8n（第三方） | quant-research | 同上 |
| `public.execution_entity` | n8n（第三方） | quant-research | 同上 |

这三张是 n8n 自己的内部表，连 `"workflowId"` 这种 camelCase 列名都是它 ORM 的
产物。读它们是一条**有意保留的兼容缝**，不属于 quant 的契约。原来同一段 35 行
SQL 被抄了两份，其中一份内联在 `app/routers/` 里 —— 而 AGENTS.md 的变更地图规定
routers 只做 HTTP 边界与入参校验。现在只允许在那一个适配器里出现；读不到时按
`workflow_audit.available=false` 上报，不再让"未知"塌成"工作流已停用"。

`feishu-relay` 与 `xhs-intel` 对 quant 的跨界数据引用为 **0**：它们消费 quant
数据走 `/api/v1` HTTP 契约（`baidu-pan-market-archive.mjs` 里的
`source: 'quant.market.events'` 只是出处标签，不是查表）。

**已拆除**：`quant.analyst_signals.ingestion_job_id` 曾经是
`REFERENCES public.ingestion_jobs(job_id) ON DELETE CASCADE` —— `ingestion_jobs`
是飞书中继的投递台账（`feishu-relay/adapter/ledger.mjs` 拥有），删一条投递记录
会级联删掉 quant 的研究信号。迁移 `20261008_sep0002` 去掉了这条约束，列本身
保留（`NOT NULL`，仍是研究出处）。它也是逼着 quant-only 库伪造外部表的原因：
`standalone/001-ingestion-contract.sql` 的桩表在该迁移到达所有环境后即可退役。

### 共库事实（`shared_runtime_databases`）

| compose | 库 | 共用的组件 |
|---|---|---|
| `deploy/compose.server.yaml` | `n8n` | feishu-relay + quant-research |
| `compose.yaml` | `n8n` | feishu-relay + quant-research |

47owner 主栈把 `feishu-adapter` 和 `quant-research*` 放在同一个库里。组件清单把
`feishu-relay` 的 runtime 记作 `47edge`，那只对 edge 部署成立 —— owner 上也跑着
一份。申报不是豁免，是让这件事看得见、并且新增一处会红。

**终态参照**：`deploy/shared-peer/compose.yaml` 已经把两者拆成
`PEER_QUANT_DATABASE` 与 `PEER_N8N_DATABASE` 两个库，所以它不在上表里。主栈要
达到同样的隔离，需要把 `public.ingestion_*` 迁到中继自己的库，并把
`analyst_signals` 的来源改成跨库的不可强约束引用 —— 跨界外键已经拆掉，这一步
不再被 schema 阻挡。

## Compatibility rules

- Patch changes keep the documented paths and fields backward compatible.
- A new required field, authentication rule, database column, or external source
  version increments the interface contract and requires a coordinated release.
- Component releases record their own source SHA, image/digest and migration head;
  a global repository SHA is not evidence that all runtime units are running the
  same code.
- Secrets, cookies, tokens, webhooks and local profile directories remain host
  configuration and never enter component archives.
- 跨组件的数据引用和共库事实必须出现在 `config/components.json`；拆掉之后要把
  申报一起删掉（守卫会报 stale），否则白名单只会越积越多。迁移目录不参与扫描：
  一条**拆除**跨界外键的迁移也必须在 `downgrade` 里写出那个对象名。
