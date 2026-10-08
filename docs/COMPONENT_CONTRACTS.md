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

## Release-unit isolation

两条热部署路径互不影响，这一点由 `scripts/test_release_unit_isolation.py` 守住：

| 保证 | 机制 |
|---|---|
| 改 feishu-relay（含 xhs、分析师）热部署 47edge 不动 47owner | edge 脚本正文里不出现 `47.110.79.189` / `stockpeer` / `OWNER_PEER_*`，只操作 `EDGE_HOST` |
| 47owner 的策略更新与迭代不动 47edge | owner 脚本正文里不出现 `47.114.113.152` / `EDGE_HOST`；且 `feishu-relay/*`、`frontend/*` 被归入 `skipped_files`（"不属于 owner 运行时"），未知路径一律 `full release required` fail closed，迁移变更强制走完整流程 |

### 两个前端已拆成独立发布单元

edge 上同源服务着两个 SPA（nginx 全量代理到适配器 18300，适配器按路由分发：
`/monitor`、`/dashboard`、`/workbench`、`/relay` 给飞书面板，`/`、`/research`、
`/personal` 给 quant 控制台）。**同源是故意的** —— 两个 app 共用同一套 API、SSE
和认证边界，所以不拆同源。拆的是**发布单元**：

| | 飞书面板 | quant 控制台 |
|---|---|---|
| 源码 | `feishu-relay/dashboard/` | `frontend/`（归 quant-research） |
| 发布单元 | `edge-relay` | `edge-quant-console` |
| 发布脚本 | `feishu-relay/scripts/edge/hotfix-feishu-relay-edge.sh` | `scripts/edge/deploy-quant-console-edge.sh` |
| 落盘位置 | `hotfix/releases/<id>/frontend-dist` | `hotfix/quant-console/releases/<id>` |
| 适配器读取 | `FRONTEND_DIST=/app/hotfix/current/frontend-dist` | `QUANT_FRONTEND_DIST=/app/hotfix/quant-console/current` |
| 脏检查 pathspec | `feishu-relay` | `frontend` |

拆之前：`frontend/dist` 被打进中继的原子发布目录，所以一次只为 xhs 或分析师的
中继热部署会顺带重新构建并发布 quant 控制台；反过来 quant 改完前端必须等一次
中继发布才生效。现在两者互不覆盖，各自有 `releases/` 与 `current/`、各自可回滚。

`./hotfix` 本来就整体挂进适配器容器，所以**不需要新增 compose 卷**，只改了
`QUANT_FRONTEND_DIST` 的路径。该目录不存在时适配器会退回镜像内副本
（`index.mjs` 的 `existsSync` 兜底），是降级不是 404 —— 但首次切换仍要
**先发控制台、再发中继**，并且发完中继要重启一次适配器（`quantFrontendDist`
在启动时求值一次；之后换 symlink 无需重启）。

### 其余跨单元事实

`frontend/dist` 仍然**运行在 47edge**（同源要求），但已是独立发布单元
`edge-quant-console`，声明见 `config/components.json` 的 `cross_unit_artifacts`。

两个脚本的脏检查都**只盯自己的源码目录**，而且都不能盯 `*/dist` —— 那些目录在
`.gitignore` 里，`git diff` 对它们永远返回 0，拿来当 pathspec 等于没有检查
（带着未提交改动热部署，改动会被构建发出去而 `release_id` 上不带 `-dirty`，
出处在说谎）。`scripts/test_release_unit_isolation.py` 逐个 pathspec 过
`git check-ignore`，并端到端验证：改一个被跟踪的 quant 前端文件，控制台单元必须
识别为脏，而中继单元的判断**不受影响**。

## Hot update per release unit

47 的发布模型是：日常源码改动走带版本的 source overlay、**复用现有镜像**，只有
运行时依赖或基础设施契约变了才发不可变镜像。每个单元的热更新入口声明在
`config/components.json` 的 `hot_update_paths`，由
`scripts/test_hot_update_paths.py` 守住。

| 单元 | 热更新脚本 | 什么情况下必须改走镜像发布 |
|---|---|---|
| `owner-quant` | `scripts/shared-peer/deploy-code-only.sh` | requirements/锁文件/Dockerfile/compose/系统包/supervisor 契约；Alembic 迁移（注释与 docstring 改动除外） |
| `edge-relay` | `feishu-relay/scripts/edge/hotfix-feishu-relay-edge.sh` | `adapter/package.json` 的**依赖字段**变化（`scripts` 等无关字段不算） |
| `edge-quant-console` | `scripts/edge/deploy-quant-console-edge.sh` | 无 —— 纯静态资源 |
| `edge-xhs` | `xhs-intel/scripts/hotfix-xhs-intel-edge.sh` | `requirements.txt` 与运行镜像不一致；`Dockerfile` 变化；外部 Spider_XHS 变化 |

`owner-schema`（数据库迁移）和 `edge-workflows`（n8n 工作流导入）不在表里：前者
本质上不能热更新，必须先应用、验证，再切依赖它的代码；后者不是容器源码。

### xhs-intel 原来没有热更新路径

`deploy-xhs-intel-edge.sh` 只要 `XHS_INTEL_TREE_SHA256` 变了就
`docker compose build xhs-collector` —— **改一行 Python 就重建一次镜像**，而那个
镜像要装 nodejs/npm、pip 依赖，还要对外部 Spider_XHS 跑 `npm ci`。一次源码改动
因此变成一次完整的供应链动作。

现在 `xhs-collector` 和适配器、quant-service 用同一套 overlay：挂
`${XHS_HOTFIX_DIR:-./hotfix-xhs}:/app/hotfix:ro`，`command` 在
`XHS_HOTFIX_ENABLED=true` 且 `/app/hotfix/current/edge_api.py` 存在时把
`PYTHONPATH` 指向 overlay 再跑它，否则跑镜像里烤进去的那份。那个镜像发布脚本
保持原样（它仍是依赖/基础镜像变化时的正道），只是不再是唯一的路。

### 守卫验的是什么

- 每个发布单元都有声明的热更新脚本，脚本存在且可执行；
- 热更新脚本里**不得出现** `docker build` / `docker pull`；
- 只要会重建容器，就必须带 `--no-build` 与 `--pull never`；
- 每条路径都必须写明 fail-closed 条件，并且脚本里真的有那道闸门；
- 热更新脚本必须放在它所属组件的目录下。

（`feishu-relay/scripts/edge/deploy-xhs-intel-edge.sh` 这个**镜像发布**脚本目前
还放在 `feishu-relay/` 下，按组件归属应该搬到 `xhs-intel/`。它正在被另一处改动，
所以这次没动它。）

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
