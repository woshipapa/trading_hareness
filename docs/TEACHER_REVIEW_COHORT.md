# 老师复盘策略包（teacher review）

把老师收盘复盘视频（或文字复盘）里点到的个股和打法，量化成逐只可判定的条件，
作为一个策略接入现有流程：观察池 → 盘中扫描规则族 → 信号事件 → 飞书 → 盘后结算。
策略只声明需要哪些数据能力，数值一律取自数据面已有接口/已沉淀证据，不直连任何供应商。
研究用途，`live_effect="none"`，不产生订单，不改任何实盘阈值。

## 流程

| 时间 | 环节 | 代码 |
|---|---|---|
| 复盘视频发布后（约 19:30–22:00） | 导入策略包：校验 → 选定首个可用交易日 → 用已存日线冻结价位 → 写入观察池 → 存档 → 飞书“入池”通知 | `POST /api/v1/teacher-review/packs`、`teacher_review_service.import_pack` |
| 次日盘中（9:30 起，前 30 分钟每 10 秒一轮） | 扫描规则族读取 `metadata.teacher_review`，逐只判定；满足即发 `entry`，失效发失效提醒，缺数据发“数据缺失” | `teacher_review_rules.teacher_review_signals`（挂在 `intraday_signal_generation`） |
| 信号生成后 | 首次满足即确认 → `intraday_alert_deliveries` 先落库 → 并发推送飞书 | `intraday_alerts._teacher_review_alert_text` |
| 盘后刷新（约 16:00） | `teacher_review_roll`：结算当日触发、老师预判与多策略共振，为仍有效的趋势票重算次日价位，下线过期计划，飞书结算摘要 | `teacher_review_service.roll` |

## 数据：只用数据面，Longhu 优先

策略的数据需求登记在 `platform/strategy_data_needs.py`（`teacher_review_playbooks`）。

| 用途 | 能力 | 实际来源（优先级） |
|---|---|---|
| 盘前价位（均线/平台/前高/60日线） | `bars.daily` | `canonical_bars_daily` + 时点正确的 `daily_adjustment_factors`（`longhu_qfq_derived` 优先），按最后一个交易日锚定成前复权 |
| 盘中价格/量比/换手/成交额 | `quote.watch_snapshot` | Longhu 盘口 → 腾讯批量 → 全 A 快照 |
| 昨收/开盘/最高/最低 | `quote.watch_snapshot` | Longhu 原始行 → 腾讯原始行 → 全 A 快照 → 分钟线 → 由涨跌幅推算 |
| 封板盘口 | `quote.order_book` | Longhu 十档 → 腾讯五档 |
| 分时均价 | `quote.watch_snapshot` | 列表报价的累计成交额 ÷ 累计成交量（与分时均价同义）；其他策略恰好拉到的分钟线优先 |
| 分钟放量/5 分钟走势 | `quote.watch_snapshot` | 快照带（每轮扫描的列表报价按股保存 31 分钟）重建；其他策略拉到的分钟线优先；带子不足 5 分钟时回退到量比/上一轮扫描价 |
| 30/60 分钟两段底背离 | `bars.minute` | Longhu 周期K（`GetKLineDay_W14` Type=30/60，120 根≈15/30 个交易日）；盘前只用前一交易日收盘及以前的K，盘中每分钟刷新一次（含形成中的K）；Longhu 取不到时用已存 `intraday_minute_sessions` 聚合；不足 35 根记“数据不足”，不当作“没有背离” |
| 竞价额/竞价封单/竞价涨幅 | `auction.open_snapshot` | 09:25 后首轮扫描取 Fuyao `a_share_auction_snapshot`（一次 ≤100 只）；快照不带日期，只收 `data_status=final` 且昨收等于计划冻结收盘价的行，定稿后全天复用；取不到时才用 09:31 前累计成交额近似 |
| 板块涨停家数 | `limits.limit_up_pool` | 已存 Fuyao 涨停池最新快照（每分钟）中当前涨停、`limit_up_reason` 命中板块关键词的家数；快照超过 10 分钟视为未知；另记今日峰值，“显著回落” = 峰值达到形成线后跌破峰值一半 |
| 龙头状态（跟随套利） | `quote.watch_snapshot` | 龙头本身也是老师计划票：取它在同一扫描链路里的最新状态（≤120 秒） |
| 盘后首封时间 | `limits.limit_up_pool` | 盘中采集的 `market_events`（分钟级） |
| 交易日 | `reference.trade_calendar` | `market_trade_calendar` |

不逐只拉分钟线：老师条件里分钟类的三个数都从扫描本来就批量拉取的列表报价里得到
（Longhu 一次最多 300 只的观察池报价、腾讯批量报价、全 A 快照 5500+ 只两页），零额外请求：

- 分时均价 = 累计成交额 ÷ 累计成交量；
- 5 分钟涨幅 = 当前价 ÷ 快照带里 5 分钟前那一轮的价格；
- 分钟放量倍数 = 最近 60 秒成交量（折算到每分钟）÷ 此前各分钟成交量中位数（至少 5 分钟），
  和分钟线版本的“当前分钟量 ÷ 前序分钟中位数”同义。成交量只在同一来源的样本之间做差（各源单位不同）。

快照带是进程内存，跨交易日清空；重启后前 5 分钟回退到量比和上一轮扫描价，信号 `features.tape` 里
记录样本数和跨度。需要时可设 `TEACHER_REVIEW_MINUTE_EXTRA=N` 给老师票额外追加 N 只逐股分钟线（默认 0）。

其他策略的逐股分钟线仍按原预算拉取。Longhu 分钟线接口一次只接受一个 `StockID`（300 只一批只适用于
报价这类列表参数接口），所以一轮扫描把整篮分钟线作为**一个**批次请求：只占 peer 共享阻塞线程池的一个槽位，
5.5 秒预算到点返回已完成的部分；没来得及拉的票不进缓存，下一轮优先补上。腾讯分钟线是异步 HTTP，同时并行作为兜底。

批次优先走 owner 的 `GET /licensed/longhu/minutes?symbols=`（见 `SHARED_STOCK_DATA_API.md`）：owner 在自己的
供应商线程池里并发，整批只占 owner 一个槽位。owner 未升级时 peer 自动回退为逐只调用，并发由
`LONGHU_MINUTE_BATCH_WORKERS`（默认 4）限制——这是实测值：逐只调用时每只占 owner 阻塞线程池一个槽位
（默认 4 线程 + 8 排队），并发 4/8/12 时 100 只都约 12–14 秒、吞吐持平，16 路以上返回 503，会连带挤掉报价和竞价读取。

每个打法都声明了必需输入（`teacher_review_rules.REQUIRED_INPUTS`）。某一轮拿不到时：该轮不判定入场，
失效判断只在有证据时成立（缺均价不会被当成“跌破均价”），并在连续两轮缺失后推一条“老师复盘·数据缺失”，
列出缺的字段。每个数值的来源记录在信号的 `features.sources` 里。

## 触发即推送、不被阻塞

- 入场和失效信号带 `independent_confirmation`，首次满足的那一轮就确认，不等第二轮；
  同一条件持续成立时由状态机去重（5 分钟窗口），条件中断后再次满足会再推。
- 门禁：只有“没有实时价格”会阻断；行情源/时间戳不新鲜、跨源价差、涨停可能买不到、市场上下文、
  日线因子质量、纸面组合限额都只作为“门禁提示”写在飞书里。
- 其他规则对同一只票的提醒不会让老师复盘提醒进入 10 分钟冷却。
- 老师复盘提醒和小杰龙头各自有自己的预算，互不占用。
- 每条提醒先写 outbox 再发送，一轮扫描内的多条提醒并发投递（最多 4 路），发送失败由下一轮扫描重试。

## 多策略共振

老师复盘规则族与小杰龙头（含潜龙出海）在同一个扫描周期里各自运行：

- 老师的票当天也被小杰/潜龙出海选中时，老师复盘提醒里写“多策略共振：小杰龙头 …”；
- 小杰/潜龙出海的提醒如果命中老师当日计划，写“多策略共振：老师复盘 … 看好/观察”；
- 盘后结算按 `xiaojie_leader_flow_observations` 标出共振票，并对比共振票与其他老师票的当日涨幅。

共振只是标注（`strategy_confluence.py`），不创建、不升级也不压制任何信号；进程重启后从已存观察恢复。

## 精确化（rules v3）

- **当日均线**：计划冻结前 n−1 日收盘和，盘中 当日 MA_n =（前缀 + 现价）/ n，不再用昨日 MA。
- **“收盘跌破”类失效**（趋势类）：14:50 后才按现价确认并推送失效；之前只在信号里记“盘中跌破（收盘确认）”。
  连板/接力类的“跌破昨收/均价”仍盘中即时失效。
- **哪根均线是支撑**按本股数据定：当前上涨段（上一次收盘 < MA20 之后）收盘跌破 MA10 ≤ max(1, 段长 5%) 次则用 MA10，否则 MA20；
  老师明确指定的（如慢票放宽到 MA20）优先。
- **放量外推**用本股近 30+ 个交易日 30 分钟K的累计成交占比中位数（如胜宏 10:00 已成交全天 34%），不再按时间线性外推。
- 前高等价位一律在前复权尺度（与次日原始报价同尺度）。
- 每个参数的 `_src` 写明数据出处与算法；包内 `calibration` 记录数据源与规则。

改写同一期复盘用新包，`supersedes` 指向旧包：导入时旧包未再覆盖的计划下线，盘后滚动与结算只处理新包。

## 策略包

格式 `teacher-review-pack/v1`（示例：`quant-service/tests/fixtures/teacher_review_pack_20260921.json`）。
每只股票给出老师立场、从固定目录中选一个打法（`app/teacher_review_playbooks.py`，18 种）、
打法参数、有效交易日数和带视频时间码的原话。每个数值参数都要写出处：

- `T`：老师原话给出的数；
- `D`：用行情数据或老师屏幕 OCR 核对过的数；
- `I`：为了让定性描述可测试而设的默认值。

策略包不能携带可执行逻辑；新的老师打法 = 目录里加一个打法 + `teacher_review_rules` 里加评估器。

## 时点（point-in-time）

- `available_at = max(策略包 strategy_available_at, 导入时间)`。
- 生效交易日是复盘日之后、09:15 晚于 `available_at` 的第一个交易日。晚于次日开盘才导入的包，
  直接从下一个交易日起算，连板类（有效 1 个交易日）因此自动作废。
- 冻结价位只用生效日前一交易日收盘及以前的数据；盘中规则不读日线。

## 观察池桥接

- 只把 relay/trend 类写进观察池；`rejected`/`relay_no_chase` 只在盘后结算。
- 已有行：只替换 `metadata.teacher_review`，不改标签、开关、价格和其他 metadata 键。
- 新行：`metadata.source="teacher_review"`，标签 `名称·复盘MMDD`。
- 容量：表锁下读取启用数，保留 5 个余量，超出的写入 `overflow` 并在飞书里列出，
  绝不让启用数超过 `INTRADAY_WATCHLIST_MAX_SYMBOLS`（超过会让整轮扫描 blocked）。
- 过期：自有行停用（不删除，保留历史）；他人行只移除 `teacher_review` 键。

## 存储

不新增表（peer 不对 owner 库做 DDL）：

- 策略包：`quant.raw_market_observations`，`provider_key='teacher_review'`、`capability='teacher_review_pack'`、`symbol='analyst:<id>'`；
- 结算：同表 `capability='teacher_review_settlement'`、`symbol='teacher_review:session'`；
- 当日计划：`quant.intraday_watchlists.metadata.teacher_review`，并随扫描冻结进 `intraday_rule_input_snapshots`；
- 信号：`quant.intraday_signal_events`（`conditions ? 'teacher_review'`）。

## 操作

```bash
# 导入（在 owner 主机上，经容器内 CLI，使用容器自己的写入密钥）
docker exec -i trading-hareness-peer-quant-research-1 \
  python -m app.teacher_review_cli import - < pack.json
# 先试算（不写库）
docker exec -i trading-hareness-peer-quant-research-1 \
  python -m app.teacher_review_cli import - --dry-run < pack.json
# 查看当前队列与结算
curl -H "X-Quant-Read-Key: $KEY" http://127.0.0.1:15682/api/v1/teacher-review/cohort
curl -H "X-Quant-Read-Key: $KEY" http://127.0.0.1:15682/api/v1/teacher-review/settlements
```

`TEACHER_REVIEW_ENABLED=false` 可整体关闭（导入返回 503，盘后阶段跳过；已写入观察池的计划只在其生效日判定）。

## 扫描时的近似（均为 I）

分时放量 = 分钟量能倍数 ≥ 2，或量比 ≥ 1.5 且 5 分钟收益为正（分钟线或快照带；两者都没有时：量比 ≥ 1.5 且在均价上方）；
“不能往下跌” = 5 分钟收益 ≥ -0.3%（分钟线或快照带；两者都没有时：不低于上一轮扫描价 0.3%）；
一字板接力的“竞价五个亿，打满” = 09:25 竞价价在涨停价、且封单（未匹配买量 × 竞价价）≥ 阈值
（Fuyao 竞价快照；取不到时用 09:25–09:31 Longhu 盘口涨停价买一封单量；用户确认 2026-09-22，
当日华瓷股份竞价成交仅 0.19 亿、封单 5.15 亿）；竞价成交额只作参考；封板 = 价格到涨停价且盘口只有买盘；
30/60 分钟底背离 = 最近两个摆动低点（左右各 3 根K确认）后低 ≤ 前低×1.005、MACD DIF 后高于前、两低之间反弹 ≥3%，
至少 35 根K；盘前结果冻结进计划，盘中用每分钟刷新的结果（摆动低点需右侧 3 根K确认，30 分钟约 1.5 小时、60 分钟约 3 小时），
B 路径 = 进入回踩区 + 背离成立 + 均价上方；赌消息的板块家数盘中只提示，盘后核对。
