# owner 读路径接入说明（给 video_understanding_harness）

生成时间：2026-09-23 00:55（北京时间）。接口清单导出自线上 `http://127.0.0.1:15682/openapi.json`：共 190 个路径，其中 GET 105 个，写方法 86 个；peer 当时运行的版本为 `running_git_sha=e1f23ad554`。接口会随部署增减，以线上 `/openapi.json` 和 `/docs` 为准。

全部访问都是**只读研究**：不下单，不写自选，不入库，不对 owner 做任何 DDL。

---

## 1. 一张表看端口

| 地址 | 是什么 | harness 能否使用 |
|---|---|---|
| **`http://127.0.0.1:15682`** | **owner 读路径**。本机 ssh 隧道转发到 owner 主机上 peer 容器的 API（`intraday_edge`，走 owner PostgreSQL） | **用这个**。owner 证据只能来自这里 |
| `http://127.0.0.1:5681` | 本机 Docker 里的 `n8n-quant-research`，只有本地数据 | 只能做本地测试；**不能当作 owner 数据** |
| owner `15681` | owner 自己的主服务 | 没有转发，不归我们管 |
| owner `15683` | peer 调度器（研究任务） | 没有转发，不对外 |

## 2. ssh 隧道

| 项 | 值 |
|---|---|
| 主机 / ssh 端口 / 用户 | `47.110.79.189` / `3535` / `stockpeer` |
| 转发 | 本机 `127.0.0.1:15682` → owner `127.0.0.1:15682`（`-L 127.0.0.1:15682:127.0.0.1:15682`） |
| 启动脚本（唯一允许的方式） | `/Users/papa/codebase/n8n/scripts/shared-peer/start-local-longhu-tunnel.sh` |
| 脚本需要的环境变量 | `LONGHU_SSH_HOST=47.110.79.189`、`LONGHU_SSH_PORT=3535`、`LONGHU_SSH_USER=stockpeer`、`LONGHU_SSH_KEY_PATH=<运维提供>`，以及可选的 `LONGHU_LOCAL_PORT=15682`、`LONGHU_REMOTE_PORT=15682` |
| 密钥 | ED25519。按 AGENTS.md 规定，本文**不写私钥路径和内容**，由运维通过 `LONGHU_SSH_KEY_PATH` 提供。核对用的公钥指纹：`SHA256:BSQJGt3oMiaGB9BEYgant+gEM7tTttIbJfFDdS68RmE` |
| 主机校验 | `StrictHostKeyChecking=yes`，本机 `known_hosts` 已有 `[47.110.79.189]:3535` 的记录 |
| 当前状态（生成本文时） | 隧道在线：本机有 ssh 进程监听 `127.0.0.1:15682`，`/health` 返回 ok |

**同一台机器上的 harness 不需要接触密钥**，直接请求 `http://127.0.0.1:15682`。每次开跑先做两项确认：

```bash
lsof -nP -iTCP:15682 -sTCP:LISTEN          # 应该看到 ssh 进程在监听
curl -fsS -m 5 http://127.0.0.1:15682/health | python3 -c "import json,sys; d=json.load(sys.stdin); print(d['status'], d['build'])"
```

隧道断了就用上面的脚本重建。重建不了时，**owner 证据按"未核实"处理并停止**，不要改用 5681 冒充 owner 数据。

## 3. 认证矩阵

| 请求类型 | 需要什么 | harness |
|---|---|---|
| 普通 `GET`（105 个，下面标"需读 key"的除外） | 不需要 key | ✅ 直接用 |
| Longhu 授权通道：`POST /licensed/stock-api/call`，以及 `GET /licensed/*`、`/api/v1/research/longhu/catalog`、`/api/v1/research/longhu/schema-profile` | 请求头 `X-Quant-Read-Key` | ✅ 需要 key |
| 其他所有 `POST/PUT/DELETE`（第 7 节） | 请求头 `X-Quant-Write-Key` | ❌ **禁止**。写权限 key 不交给 harness |

**读 key 怎么取**（harness 的 `owner_universe.read_key()` 已经实现）：
1. 优先读环境变量 `VIDEO_RESEARCH_READ_KEY`；
2. 没有的话，在内存里从 `/Users/papa/codebase/n8n/.env` 解析 `QUANT_SHARED_READ_API_KEY`，再退到 `/Users/papa/codebase/n8n/deploy/shared-peer/.env`。2026-09-22 时 `deploy/shared-peer/.env` 那份被网关拒绝（401），所以 `n8n/.env` 优先。

读 key **不能打印、写日志、写进结果文件、放进 URL，也不能提交**。

## 4. 调用规范

- **超时**：普通 GET 10–30 秒；`/api/v1/market/level1/latest?limit=6000` 这类大响应 60 秒；授权通道 30 秒。
- **Longhu 网关容量**：owner 的 Windows 网关大约每秒 8 次调用（4 个 worker + 8 个排队）。在途超过约 12 个就返回 503。harness 要做到：
  - 并发不超过 4；
  - 每秒不超过 6 次；
  - 遇到 503 或 502 时指数退避，不要立即重试。
- **批量上限**：授权通道单次物理批量为 300 行（`physical_batch_limit=300`）。`/licensed/longhu/quotes`、`/licensed/longhu/minutes` 一次最多 300 只。
- **空结果不等于事实**：返回 0 行时，记为"未入库 / 缺口"（例如 9/22 的龙虎榜、概念板块都是 0 行），不要写成"没有"。
- **owner 证据的记录要求**（AGENTS.md 强制）：每条 owner 数据结论都要分别记下以下各项：
  - 目标地址 / 路径；
  - ssh 隧道状态；
  - 交易所 / 交易日；
  - 观测时间；
  - 数据源；
  - 行数与覆盖率；
  - 新鲜度；
  - `decision_eligible`。

  同时要查**聚合市场快照**（`/api/v1/market/snapshots`）和 **provider 健康**（`/api/v1/providers/health`）。可参照的记录格式：任务目录里的 `owner_market_evidence_<T>.json`（`schema_version=owner-market-evidence/v1`）。

## 5. 复盘最常用的接口（按用途）

| 用途 | 接口 | 要点 |
|---|---|---|
| 开跑前检查 | `GET /health` | 返回 `status`、数据库池、`build.running_git_sha`（热部署的实际运行版本）、各运行任务状态 |
| 全 A 名录（股票实体解析） | `GET /api/v1/universes/all_a` | 代码与名称；harness 会缓存 72 小时 |
| 当日收盘快照（必查） | `GET /api/v1/market/snapshots?limit=5` | 包含：<br>`session`（close / intraday）、`exchange_date`、`observed_at`<br>`universe_count`、`quote_count`、`coverage`、`status`、`decision_eligible`<br>涨跌家数、成交额、涨跌幅中位数 |
| provider 健康（必查） | `GET /api/v1/providers/health`、`GET /api/v1/providers/realtime-health` | 各数据源 healthy / degraded 状态和时间 |
| 全 A 最新横截面 | `GET /api/v1/market/level1/latest?limit=6000` | 最新一笔 Level-1：价格、涨跌幅、成交额 |
| 涨停池、炸板池等市场事件 | `GET /api/v1/events/market?event_type=limit_up_pool&trade_date=YYYY-MM-DD&limit=500` | Fuyao 涨停池从 09:26 起每分钟一个快照。统计板块涨停数要用**最新快照**，不能对全天取并集 |
| 龙虎榜 / 公告 | `GET /api/v1/events/lhb?trade_date=`、`GET /api/v1/events/announcements?symbol=` | 龙虎榜当日可能还没入库 |
| 板块资金流 / 成分 / 盘中曲线 | `GET /api/v1/market/sectors/flows?taxonomy_key=ths_industry&trade_date=`<br>`GET /api/v1/market/sectors/{sector_key}/members`<br>`GET /api/v1/market/sectors/intraday/curves?trade_date=&taxonomy=industry` | Longhu 行业 104 个板块；盘中曲线每分钟一个点 |
| 概念板块 | `GET /api/v1/market/sectors/concepts?trade_date=` | 当日可能为 0 行，属于缺口 |
| 收盘板块复盘 | `GET /api/v1/market/sectors/review/report/latest` | 板块 Top10 的复盘证据 |
| 老师计划与结算 | `GET /api/v1/teacher-review/cohort`<br>`GET /api/v1/teacher-review/settlements?limit=5` | 结算包括：预判命中、盘中触发、晋级延续 / 观察 / 退出（`roll.lifecycle`） |
| 观察池（含老师计划） | `GET /api/v1/intraday/watchlists` | `metadata.teacher_review` 里有计划的状态 `status` / `lifecycle` |
| 自选股收盘复盘 | `GET /api/v1/watch-reviews?trade_date=`、`GET /api/v1/watch-reviews/patterns?start=&end=&min_count=` | 每只股票的时间线、标签，以及标签对应的次日表现 |
| 小杰群聊语义特征 | `GET /api/v1/research/strategies/xiaojie-leader-flow/message-features?symbol=&as_of=&since=&instructor_only=true` | 按 `as_of` 时点只返回当时已经可用的消息（以账本首次接收时间为准） |
| 策略当日产出 | `GET /api/v1/strategy/daily-summary/latest`、`/strategy/post-close/latest`、`/strategy/decisions/latest` | 研究候选，不是交易指令 |
| 个股分钟 K + 分析师动作 | `GET /api/v1/analyst-research/stock-timeline?symbol=000001.SZ&start_date=&end_date=` | 分钟 K 线上叠加分析师动作时点 |
| **日 K（复权）/ 盘口 / 分钟** | 授权通道，见第 6 节 | 通用 GET 接口里**没有日 K**，要走 Longhu `GetKLineDay_W14` |

## 6. Longhu / 第三方授权通道（需要 `X-Quant-Read-Key`）

**通用调用**：`POST /licensed/stock-api/call`，请求体如下。

```json
{"target": "<目标>", "path": "/w1/api/index.php", "params": {"a": "<action>", "c": "<controller>", "...": "..."},
 "batch": {"param": "StockID", "values": ["000001", "600000"], "separator": ","}}
```

- 返回格式为 `{"target", "path", "calls", "batched", "physical_batch_limit", ..., "pages": [{"payload": {...}}, ...]}`，数据在 `pages[].payload` 下，直接从根取会拿到空。不同 action 的 payload 形状不一样，以实测为准。
- `batch` 可选：由网关按每批 300 拆分请求。
- 只允许登记过的目标：
  - `longhu_history`：历史接口；
  - `longhu_quote`：报价、分钟、竞价、个股 L2；
  - `longhu_market`：监控、排行、全球指数；
  - `longhu_market_wide`：宽度、板块、研究；
  - `longhu_lhb`：股东、机构、题材；
  - `longhu_article`：快讯；
  - `xuangubao`：`/api/pool/detail`、`/api/market_indicator/line`、`/api/surge_stock/stocks`、`/api/surge_stock/plates`；
  - `fupanwang`：`/kpl/zhibo`。
- 凭据由 owner 网关注入，harness 不需要也拿不到。

**常用示例**

```json
// 日 K（前复权）：Type=d 日线，Type=30/60 取 30/60 分钟 K；Is_FS=1 为复权；st=每页根数，Index 分页
{"target": "longhu_history", "params": {"a": "GetKLineDay_W14", "c": "StockLineData", "st": "111", "PhoneOSNew": "1",
  "VerSion": "5.19.0.0", "Index": "0", "apiv": "w40", "Type": "d", "StockID": "302132", "Is_FS": "1"}}
// 返回的 payload 是列式数组，每个下标对应一根 K 线（2026-09-23 实测）：
//   x=["20260922", …] 日期；y=[[开, 收, 高, 低], …]（注意顺序：开、收、高、低）
//   vol=成交量（手）；bal=成交额（元）；turnover=换手率%；stateZT=是否涨停；errcode="0" 表示成功
// 可以直接复用解析函数 quant-service/app/teacher_review_plan.py:parse_longhu_kline(pages)
// 个股盘口
{"target": "longhu_quote", "params": {"a": "GetStockPanKou", "c": "StockL2Data", "PhoneOSNew": "1", "VerSion": "5.20.0.2",
  "apiv": "w41", "StockID": "000001"}}
// 涨停池 / 炸板池 / 跌停池（选股宝，date 为空表示当天）
{"target": "xuangubao", "path": "/api/pool/detail", "params": {"pool_name": "limit_up", "date": "2026-09-22"}}
```

**已登记文档的 Longhu 操作**（`GET /licensed/stock-api/catalog` 返回完整列表和 89 个请求示例；网关不限制操作名，只限制目标）：

| 目标 | action（controller） |
|---|---|
| longhu_history | GetKLineDay_W14（StockLineData）、RiseFallAnalysis（HisHomeDingPan）、ChangeStatistics、MorningBiddingList、ZhiBoContent（HisConceptionPoint）、RealRankingInfo、DailyLimitPerformance / DailyLimitPerformance2、GetStockChouMa_New（StockL2History）、GetPMSL_KQXY（FuPanLa）、GGList_JGCC / GGList_JGCC_Plate_Stocks / GGList_BXZJ / GGList_BXZJ_Stocks（ZhuLiChiCang） |
| longhu_quote | GetStockTrendIncremental（分钟）、GetStockPanKou（盘口）、MorningBiddingList（竞价榜）、GetStockBid、DailyLimitPerformance / DailyLimitPerformance2、StockChouMaByTimeNew_W5、GetStockDaDanTrendIncremental（大单）、GetZhangTingGene、GetBKJJ_W36、GetBKJJBL |
| longhu_market | GetMainMonitor_w30、GetWeiTuo_W14、ChangeStatistics、RealRankingInfo、Radar、GetHotPHB、GlobalCommon（全球指数） |
| longhu_market_wide | GetPlateInfo_w38、RiseFallAnalysis、MoodNumCount（情绪）、GetPlate_Info_QJ、ZhiShuStockList_W8、GroupCount_w28（新高）、GetInterviewsByDateZS / GetInterviewsByDateStock、GetPianLiZhi_Index、GetStockIDPlate |
| longhu_lhb | GuDongRenShu、JGStockListox、GetJGNameID、InfoList / InfoZS / InfoGet（Topic） |
| longhu_article | GetTopList、GetList（快讯） |

**便捷读取接口**：
- `GET /licensed/longhu/quotes?symbols=000001.SZ,600000.SH`：报价，最多 300 只，返回 `{rows, source_status, physical_request_limit}`；
- `GET /licensed/longhu/minutes?symbols=…&deadline_seconds=`：当日分钟路径，最多 300 只；
- `GET /licensed/longhu/minutes/{symbol}`：单只股票的分钟；
- `GET /api/v1/research/longhu/catalog`：Longhu 能力目录；
- `GET /api/v1/research/longhu/schema-profile`：字段结构。

## 7. 写接口（86 个，harness 禁止调用）

下面列出来只是为了完整，说明这些能力存在。它们都需要 `X-Quant-Write-Key`，只由 peer 服务本身或运维使用：

- **行情同步与导入** `/api/v1/market/*`（23 个）：
  - 日线、分钟导入；
  - 收盘刷新（`post-close/refresh[/start]`）；
  - 板块、概念、成分同步与回填；
  - Tushare、Baostock、全市场日线同步；
  - 快照运行、全 A 名录同步。
- **盘中** `/api/v1/intraday/*`（7 个）：扫描、分钟捕获、板块报告、结果重算、观察池增改删（PUT / DELETE `watchlists/{symbol}`）、历史同步。
- **策略运行** `/api/v1/strategy/*`、`/api/v1/strategies/*`：决策、形态挖掘、收盘策略、复盘、主升浪、回测、盘中回放。
- **研究运行** `/api/v1/research/*`：L2 评估、Longhu 探测、小杰评估（`xiaojie-leader-flow/evaluate`，纯计算，但按规则仍属写方法）、十日龙头轮动。
- **老师复盘** `/api/v1/teacher-review/packs`（导入策略包）、`/roll`（滚动）。每日流程统一走 `scripts/teacher_review_daily.py`，见 TEACHER_REVIEW_DAILY.md。
- **分析师** `/api/v1/analyst-*`、`/api/v1/analysts/*`、`/api/v1/claim-review/*`、`/api/v1/remote-archive/*`（导入、重处理、同步游标）。
- **其他**：
  - 数据源探测与抓取：`/api/v1/providers/*` 下的 akshare / fuyao / realtime / tushare；
  - 特征、因子、快照构建；
  - 结果重算；
  - `/api/v1/pipeline/daily`、`/api/v1/bootstrap`；
  - 纸面账户 `/api/v1/paper/*`、个人 `/api/v1/personal/*`；
  - `/api/v1/stocks/{symbol}/study`、`/api/v1/universes/members`、`/api/v1/watch-reviews/run`；
  - 内部接口 `/api/v1/internal/raw-overflow/*`。

## 8. 全部 GET 接口（105 个）

`*` 表示必填参数。标"需读 key"的要带 `X-Quant-Read-Key`；标"勿用"的是内部或个人数据，harness 不要调用。

**基础与运行**
| 接口 | 参数 | 能力 |
|---|---|---|
| `/health` | | 服务健康、构建与热部署版本、运行任务、数据库池 |
| `/api/v1/metrics` | | 主要表的行数 |
| `/api/v1/agent/context` | | 给研究智能体的综合上下文 |
| `/api/v1/automation/runs` | task_key, limit | 自动化任务和收盘各阶段的回执 |
| `/api/v1/research/overview` | | 研究面总览 |
| `/api/v1/research-frameworks` | | 研究框架目录 |
| `/api/v1/training/roadmap` | | 训练路线图 |
| `/api/v1/research/storage-tiers` | | 存储分层现状 |
| `/api/v1/research/storage-tiering` | | 热冷分层策略与迁移任务状态 |

**名录、行情与数据就绪**
| 接口 | 参数 | 能力 |
|---|---|---|
| `/api/v1/universes/{universe_key}` | universe_key* | 股票池成员（`all_a`：全 A 代码与名称） |
| `/api/v1/market/snapshots` | limit | 全市场快照运行记录（覆盖率、`decision_eligible`、涨跌家数、成交额） |
| `/api/v1/market/level1/latest` | limit | 最新全 A Level-1 横截面 |
| `/api/v1/market/flow/features` | trade_date, limit | 市场资金流特征 |
| `/api/v1/market/minute/imports` | limit | 离线分钟导入记录 |
| `/api/v1/features/latest` | universe_key, limit | 最新特征 |
| `/api/v1/factors` | | 因子目录 |
| `/api/v1/factors/evaluations` | universe_key, limit | 因子评估历史（IC 等） |
| `/api/v1/data-quality/issues` | limit | 数据质量问题 |
| `/api/v1/data-readiness/features` | | 特征就绪度 |
| `/api/v1/data-readiness/history-estimate` | years, include_minute, universe_symbols | 历史数据量估算 |
| `/api/v1/data-readiness/replay` | | 回放就绪度 |

**板块**
| 接口 | 参数 | 能力 |
|---|---|---|
| `/api/v1/market/sectors` | taxonomy_key, limit, offset | 板块目录 |
| `/api/v1/market/sectors/{sector_key}/members` | sector_key*, taxonomy_key, limit, offset | 成分股 |
| `/api/v1/market/sectors/flows` | taxonomy_key, trade_date, limit | 板块资金流（默认 `ths_industry`） |
| `/api/v1/market/sectors/concepts` | trade_date, limit | 概念板块 |
| `/api/v1/market/sectors/concepts/candidates` | trade_date, limit | 概念候选 |
| `/api/v1/market/sectors/concepts/members/backfill/status` | trade_date | 概念成分回填状态 |
| `/api/v1/market/sectors/intraday/curves` | trade_date, taxonomy, since | 盘中板块资金流曲线（每分钟一个点） |
| `/api/v1/market/sectors/review/report/latest` | | 收盘板块复盘报告 |

**事件**
| 接口 | 参数 | 能力 |
|---|---|---|
| `/api/v1/events/market` | symbol, event_type, trade_date, limit, offset | 市场事件（涨停池、炸板池等每分钟快照） |
| `/api/v1/events/lhb` | trade_date, limit | 龙虎榜 |
| `/api/v1/events/announcements` | symbol, limit, offset | 公告 |

**盘中**
| 接口 | 参数 | 能力 |
|---|---|---|
| `/api/v1/intraday/scans/latest` | limit | 最近的盘中扫描 |
| `/api/v1/intraday/watchlists` | | 观察池（含老师计划的元数据） |
| `/api/v1/intraday/decision-cards/{symbol}` | symbol* | 个股最新决策卡 |
| `/api/v1/intraday/board-rotations/latest` | limit | 板块轮动事件 |
| `/api/v1/intraday/board-stock-mining/latest` | limit | 板块驱动个股挖掘 |
| `/api/v1/intraday/limit-linkage/latest` | limit | 涨停联动 |
| `/api/v1/intraday/outcomes/latest` | limit | 盘中信号的结果 |
| `/api/v1/intraday/services/status` | | 盘中各服务状态 |

**策略与复盘**
| 接口 | 参数 | 能力 |
|---|---|---|
| `/api/v1/teacher-review/cohort` | | 老师复盘在池计划 |
| `/api/v1/teacher-review/settlements` | limit | 老师计划每日结算（预判、触发、晋级 / 观察 / 退出） |
| `/api/v1/watch-reviews` | trade_date | 自选股收盘复盘 |
| `/api/v1/watch-reviews/patterns` | start, end, min_count | 复盘标签对应的次日表现 |
| `/api/v1/research/strategies/xiaojie-leader-flow/message-features` | symbol, as_of, since, instructor_only, limit | 小杰群聊语义特征（按可用时点返回） |
| `/api/v1/research/ten-day-leader-rotation/latest` | limit | 十日龙头轮动 |
| `/api/v1/research/l2/evaluations/latest` | | L2 评估 |
| `/api/v1/research/runs` | experiment_type, status, limit | 研究运行历史 |
| `/api/v1/research/runs/{research_run_id}` | research_run_id* | 研究运行详情 |
| `/api/v1/research/models` | | 研究模型 |
| `/api/v1/strategies` | | 策略目录 |
| `/api/v1/strategies/experiments` | universe_key, limit | 策略实验 |
| `/api/v1/strategy/contracts` | | 策略契约（版本、输入、数据需求） |
| `/api/v1/strategy/promotion` | | 各策略的实盘晋级状态（默认全部关闭、权重为 0） |
| `/api/v1/strategy/governance` | | 策略治理 |
| `/api/v1/strategy/health` | | 策略健康 |
| `/api/v1/strategy/funnel` | limit | 候选漏斗 |
| `/api/v1/strategy/daily-summary/latest` | exchange_date | 每日策略摘要 |
| `/api/v1/strategy/post-close/latest` | | 收盘策略候选 |
| `/api/v1/strategy/decisions/latest` | | 最新决策研究 |
| `/api/v1/strategy/reviews/latest` | session | 策略复盘 |
| `/api/v1/strategy/pattern-mining/latest` | | 形态挖掘 |
| `/api/v1/strategy/ablation/latest` | limit | 消融结果 |
| `/api/v1/strategy/watchlist-proposals` | | 跨策略观察池候选（只供人工复核） |
| `/api/v1/recommendations/latest` | | 研究推荐 |

**分析师**
| 接口 | 参数 | 能力 |
|---|---|---|
| `/api/v1/analyst-research/stock-timeline` | symbol*, start_date, end_date, analyst_id, limit | 分钟 K 叠加分析师动作 |
| `/api/v1/analyst-research/market-evaluation` | start_date, end_date, analyst_id | 分析师事件对照当日市场与板块资金 |
| `/api/v1/analyst-research/observations` | analyst_id, limit | 分析师观察 |
| `/api/v1/analyst-research/profiles` | | 分析师画像 |
| `/api/v1/analyst-research/reviews`、`/reviews/latest` | cadence, limit | 分析师复盘 |
| `/api/v1/analyst-research/status` | as_of_date | 分析师研究状态 |
| `/api/v1/analyst-research/sync-health` | | 同步健康 |
| `/api/v1/analyst-claims` | limit, offset | 分析师观点 |
| `/api/v1/claim-review` | status, limit | 观点复核队列 |
| `/api/v1/analyst-scorecards` | limit | 分析师评分卡 |
| `/api/v1/analyst-skills` | analyst_id, limit | 分析师技能 |
| `/api/v1/analyst-factors` | as_of_date, lookback_days | 分析师因子 |
| `/api/v1/analyst-prompt-lab/status` | limit | 提示词实验状态 |
| `/api/v1/analysts/anqiang/trade-actions`、`/trade-action-outcomes` | as_of_date, limit | 安强的交易动作与结果 |
| `/api/v1/remote-archive/messages` | analyst_id, limit, offset | 远端归档的分析师消息 |
| `/api/v1/remote-archive/reports` | limit, offset | 远端归档研报 |
| `/api/v1/remote-archive/state` | | 归档状态 |
| `/api/v1/remote-archive/sync-cursors/{stream_key}/{analyst_id}`、`/sync-cursors-global/{stream_key}` | | 同步游标（勿用） |

**数据源与 Longhu**
| 接口 | 参数 | 能力 |
|---|---|---|
| `/api/v1/providers/health` | | provider 健康（必查） |
| `/api/v1/providers/realtime-health` | | 实时源健康（来自已存证据） |
| `/api/v1/providers/capabilities` | | 数据能力目录 |
| `/api/v1/providers/tushare/catalog` | | Tushare 接口目录 |
| `/api/v1/providers/tushare/raw` | api_name*, provider, limit, offset | 已存的 Tushare 原始记录（例如 `limit_list_d`、`moneyflow_dc`） |
| `/api/v1/providers/fuyao/catalog` | | Fuyao 接口目录 |
| `/api/v1/research/longhu/catalog` | | Longhu 能力目录（需读 key） |
| `/api/v1/research/longhu/schema-profile` | | Longhu 字段结构（需读 key） |
| `/api/v1/research/longhu/replay-readiness` | | Longhu 回放就绪度 |
| `/licensed/stock-api/catalog` | | 授权通道目标、操作与示例（需读 key） |
| `/licensed/longhu/quotes` | symbols* | 报价，最多 300 只（需读 key） |
| `/licensed/longhu/minutes` | symbols*, deadline_seconds | 当日分钟，最多 300 只（需读 key） |
| `/licensed/longhu/minutes/{symbol}` | symbol* | 单只股票的分钟（需读 key） |

**内部 / 个人（勿用）**
| 接口 | 说明 |
|---|---|
| `/api/v1/internal/raw-overflow/next`、`/status` | 内部归档交接 |
| `/api/v1/personal/decision-briefs/latest`、`/decision-research/latest`、`/portfolio-snapshots/latest` | 个人账户数据 |
| `/api/v1/paper/status` | 纸面账户 |

## 9. 最小接入示例（Python，httpx）

```python
import httpx, os

BASE = os.environ.get("VIDEO_RESEARCH_API_BASE_URL", "http://127.0.0.1:15682")   # 不要改成 5681
with httpx.Client(base_url=BASE, timeout=30) as client:
    health = client.get("/health", timeout=5).json()            # 失败就停止，owner 证据记为未核实
    snapshot = client.get("/api/v1/market/snapshots", params={"limit": 1}).json()
    providers = client.get("/api/v1/providers/health").json()
    key = os.environ.get("VIDEO_RESEARCH_READ_KEY", "")          # 只放在内存里
    kline = client.post("/licensed/stock-api/call", headers={"X-Quant-Read-Key": key}, json={
        "target": "longhu_history", "params": {"a": "GetKLineDay_W14", "c": "StockLineData", "st": "111",
        "PhoneOSNew": "1", "VerSion": "5.19.0.0", "Index": "0", "apiv": "w40", "Type": "d",
        "StockID": "002957", "Is_FS": "1"}}).json()
    bars = []
    for page in kline.get("pages", []):
        payload = page.get("payload") or {}
        for day, (open_, close, high, low) in zip(payload.get("x", []), (item[:4] for item in payload.get("y", []))):
            bars.append({"date": day, "open": open_, "close": close, "high": high, "low": low})   # y 的顺序是 开、收、高、低
```

## 10. 相关文档

- 项目规则：`/Users/papa/codebase/AGENTS.md`，其中"Owner/Peer Market-Data Access"一节；
- 老师复盘每日流程：`docs/TEACHER_REVIEW_DAILY.md`；
- 策略包生成细则：`docs/teacher_review/PACK_BUILD_BRIEF.md`；
- peer 写入声明：`docs/PEER_WRITE_DECLARATION.md`。
