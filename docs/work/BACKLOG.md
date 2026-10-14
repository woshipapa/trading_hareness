# 待办清单

知道要做、现在没在做的事。格式和状态见 `dev-notes` 技能的 `registers.md`。改之前先从磁盘重读，只改自己的条目。
凡是往生产库写数据的，都要用户同意并由用户执行命令。

## Open

### B001 日线往前补一年（2022-08 至 2023-08-14）

- state: open
- kind: data-backfill
- why: 生产库日线从 2023-08-15 起（F001）。回放从 2023-09-01 开始时，60 日前高到 2023-11 才有，“250 日最大成交额”到 2024-08 才是完整窗口，120 日成本读数同理
- how: 需要未复权日线；授权接口的指定日期 K 线是前复权的，不能直接用。先在数据源层的能力目录里选定来源，抽 20 只与库里 2023-08 的重叠日逐项核对，再经 `persist_tushare_rows` 的 `"daily"` 入口写入
- who: agent 选源、备脚本、试跑核对；写库由用户执行
- needs: 选定数据源
- updated: 2026-10-13

### B002 2023 年的复权因子补齐

- state: done
- kind: data-backfill
- why: 2023 年只有 48.5% 的日线行有复权因子（F002）；结算窗口里任一天缺因子就退回未复权价格，2023 年下半年遇到除权的股票会算偏
- how: 用日线自带的昨收推算（当日昨收 ÷ 前一交易日收盘，逐日累乘），经 `"adj_factor"` 入口写入，来源标为推算；写前在已有因子的行上核对一致率，写后核对除权日前后复权序列连续
- who: agent 备脚本；写库由用户执行
- needs: 无
- result: 2026-10-14 随 B078 完成（同一次写入，253,316 行）。后续：试验台历史读数是按未复权价算的，补因子后要重放 2023–2024 才会刷新，见 B082
- updated: 2026-10-14

### B003 2023 年缺的涨跌停价补齐

- state: ready
- kind: data-backfill
- why: 2023 年 8.6% 的日线行没有涨跌停价，涨跌停价表只从 2026-09-01 起有（F003）；缺的行判断封板和一字要退回有缺陷的板块规则（B009）
- how: 沿用 2026-10-12 验证过的“沿用比例”规则（取该股此前五根日线最常见的涨跌幅比例；与已存值一致率 99.98% 以上），只在当日一致率不低于 99.5% 时写入，经 `"stk_limit"` 入口；脚本原型在本机 `state/research/owner_repairs_20261012/limits_fill.py`
- who: agent 备脚本；写库由用户执行
- needs: 无
- updated: 2026-10-13

### B004 个股分钟线回补（盘中卖点和买点规则要用）

- state: open
- kind: data-backfill
- why: 生产库没有多年的分钟线；“10 点前不板就走”“跌破分时均价走”“炸板走”“从高点回落止盈”和“止盈加止损”的先后判定都要分钟线
- how: 授权接口 `longhu_history` 的 `GetStockTrend`，按股票和日期取（F007）。范围按优先级：昨日涨停股的次日约 5 万个股票日；开盘买入者的再下一日约 4.3 万；涨停当日和炸板股当日约 5 万加炸板名单。先在非交易时段试取 200 个测速度和失败率，再分几晚跑；断点续取，连续 20 个无数据即停；落本机温层 parquet，不写生产库的原始观测表
- who: 对生产网关的大批量读取，要用户同意；agent 写脚本并在非交易时段运行
- needs: W006 的日线部分跑通；用户同意
- note: 2026-10-13 23:00 取数速度量过了（FACTS F027）：授权接口这条链路 8 个并发时约 7.4 次/秒，再加并发不会更快，16 个并发会被 owner 网关拒绝。按 6 到 8 个并发算，一个非交易时段的夜晚（约 15 小时）能取 35 万到 40 万次；全市场个股分钟线按这个数排期。想更快要 owner 一侧放宽它网关的并发，这要问对方
- updated: 2026-10-13

### B005 逐分钟大单净额回补

- state: open
- kind: data-backfill
- why: 盘中“抢筹 / 出货”类条件要用
- how: 同一接口的 `GetStockDaDanTrend`，只能回溯到 2025-01-02（F008），与 B004 同一批股票日
- who: 同 B004
- needs: B004 跑顺
- updated: 2026-10-13

### B006 分四档的资金流在 2026-08-20 之后的缺口

- state: ready
- kind: data-backfill
- why: `quant.stock_money_flow_daily` 的四档净额只到 2026-08-20，之后只有不分档的主力净额；用到分档的条件之后没有数据
- how: 先只读查清四档来源为什么停在 8 月 20 日、能否恢复和回补，再定做法
- who: agent（只读排查）
- needs: 无
- updated: 2026-10-13

### B007 历史竞价榜未解出的列

- state: open
- kind: follow-up
- why: 逐只竞价快照、竞价曲线、委托明细 2026-10-13 起才有，没有历史可补；历史竞价榜只含竞价金额靠前的约 200 只，第 6–10 列含义未解。老师的“竞价额约为前日 1.5 倍”“9:20 后封单猛加”要等样本
- how: 攒够十来个交易日的逐只快照后，与同日竞价榜逐列对照，看能否解出更多历史字段
- who: agent
- needs: 约 10 个交易日的快照（2026-10-27 前后）
- updated: 2026-10-13

### B008 2026-10-12 补日线留下的尾巴

- state: open
- kind: data-backfill
- why: 8 个股票日因之后有除权、前复权 K 线不能用而没补：300718、688392、920055、920165（9 月 21 日），300855、301068（9 月 23 日），300770（9 月 28 日），688110（10 月 9 日）；另有 4 个判为无效的；新上市的 920025、920201、920229、301139 缺因子和涨跌停价
- how: 这 8 个可用历史分钟价（F007）合成当日开高低收；其余逐个看
- who: agent 备脚本；写库由用户执行
- needs: 无
- updated: 2026-10-13

### B009 仓库涨跌停规则的三处缺陷和 9 月 30 日的错值

- state: ready
- kind: defect
- why: `market_rules.a_share_limit_ratio` 只能复现 94% 的已存涨跌停价：主板 ST 自 2026-07-06 起按 10% 算（实际 5%）、北交所取整方向不对、302 开头代码按 20%；2026-09-30 约 143 只 ST 股存的涨跌停价因此是错的
- how: 按 2026-10-12 的实测改规则并加测试；9 月 30 日的错值用“沿用比例”规则重算后覆盖（写库）
- who: agent 改代码；写库由用户执行
- needs: 无
- note1: 2026-10-14 只做了北交所取整这一处：新函数 `market_rules.a_share_limit_prices`（北交所向昨收方向取整，其余分位半入），`longhu_market_sync.build_control_rows`、`t1_settlement._limits`、`factor_lab` 三处改用它。提交 3d2e6a8b，合进 main c5da5fa0（2026-10-14 11:10），还没发布；发布路径测试 `Ran 3129 tests in 42.675s OK (skipped=2)`。**主板 ST 的比例和 302 前缀没有改**：动手后发现“主板 ST 自 2026-07-06 起 10%”在六个测试里被明确钉住（纸面执行、`live_policy`、板块规则），是有意写的规则，而本条依据的是 10 月 12 日一次只读测量（决定 0018 第 4 点），两者矛盾，owner 主机又连不上、没法再读一次；这条规则进 `live_policy` 的兜底判断，不凭一次没法复核的测量去翻。主机恢复后先跑只读探针 `docs/work/probes/2026-10-14-st-limit-band.py`（主板 ST 自 5 月起每月存下的涨跌停比例，按日线来源和涨跌停表的来源分组；302 开头代码的比例），再定改不改、从哪天起。9 月 30 日那 143 只的错值重算也等这个结论
- note2: 2026-10-14 11:31 correction: 本条的前提是反的。只读探针（FACTS F034）表明主板 ST 自 7 月 6 日起确实是 10%，仓库的规则是对的；错的是库里 9 月 16 日以来存的 5%（每天约 140 只，其中每天有 6 到 17 只的最高价高于存下的涨停价），而本条说“错了”的 9 月 30 日恰恰是这段时间里唯一存对的一天。决定 0018 第 4 点的那次测量是拿规则去对这些存错的值，所以读反了；0018 的“沿用股票自己最近的比例”也因此把 5% 一天天传了下去。北交所取整那一处已改（note1），不受影响。本条改成要做的事：(1) 查清是哪段程序从 9 月 16 日起写进 5%（`longhuvip_composite` 这一路；`tencent_free` 来源的是 10%）；(2) 重算并覆盖约 2,400 根日线和涨跌停表里的错值（写库，用户执行）；(3) 补全阶段的沿用规则加一道核对：沿用出来的比例与板块规则不符时不写、记为待定；(4) 给决定 0018 加更正。受影响的读数：凡是用存下的涨跌停价判断主板 ST 是否封板、能否买卖的（结算模块的 `closes_at_up_limit`、`sale_blocked` 等），9 月 16 日以来对这约 140 只都可能判错。302132 的比例仍未定
- updated: 2026-10-14

### B010 日线表里周末日期的行

- state: ready
- kind: defect
- why: `canonical_bars_daily` 有 2026-08-30 共 12 行、2026-09-12 共 1 行，日期不是交易日
- how: 查来源和写入路径，定是删除还是改日期；防止再次写入
- who: agent 排查；写库由用户执行
- needs: 无
- updated: 2026-10-13

### B012 盘中卖出规则

- state: open
- kind: follow-up
- why: 日线只能评估开盘、收盘、挂单止盈止损；真正的盘中卖点要分钟线，“止盈 5% 加止损 3%”同日先后也定不了
- how: 在试验台的卖出规则里加：10 点前不板就走、跌破分时均价、炸板走、从高点回落止盈
- who: agent
- needs: B004
- updated: 2026-10-13

### B013 盘中买点

- state: open
- kind: follow-up
- why: 老师的买点都在盘中（卡位发生当下、一字开板时的量和回封、开盘五分钟放量进攻、9:20 后封单变化），日线验不了；只统计封住的票会高估
- how: 用分钟线和炸板名单，把“触板时买入”算全（包括没封住的）
- who: agent
- needs: B004；炸板名单（库里每日已存）
- updated: 2026-10-13

### B014 全市场日线条件

- state: open
- kind: follow-up
- why: 趋势股回踩 5 / 10 日线、回撤不超过 30%、放量滞涨（摸前高收回且量比 4 倍以上，次日开盘比大盘低 1.58% / 1.26%）、非涨停放量突破，这些要全市场每只股票的窗口，试验台第一版只覆盖涨停股
- how: 同一套试验台，加一个“全市场收盘”样本
- who: agent
- needs: W006 发布
- updated: 2026-10-13

### B015 按天开关和“远端量”的多日口径

- state: open
- kind: follow-up
- why: 老师的两个开关（连板环境、两市成交额 2.1 万亿）按字面不支持；成交额应改成相对近 60 日的位置。“远端量换掉后好走”在次日口径上相反，他说的是之后一段时间
- how: 开关的记录方式做细；远端量按 3、5、10 日口径各算一遍
- who: agent
- needs: W006 发布
- updated: 2026-10-13

### B016 按老师说法做的打法的参数缺口

- state: open
- kind: follow-up
- why: 摘要 D 节：一字板接力 20 个数字参数没有一个是老师口述的；赛马没有比较对象；10 日线二波不读均线参数（0923 奥康国际设了 20 日线仍按 10 日线算）；否定理由没有结构化；20 条规则没有任何打法对应
- how: 逐条列给用户定优先级；本机 `state/research/shortterm_tactics_20261013/teacher_method_digest.md` 的 D 节
- who: agent 整理；用户定取舍
- needs: 无
- updated: 2026-10-13

### B017 竞价处置表自己的页签和中继路由

- state: open
- kind: follow-up
- why: W002 方案第 4 步没做；W006 的竞价看板可能已覆盖它的用途
- how: W006 的看板上线后决定：不做就关闭 W002，做就补页签和 `research-routes.mjs` 的两条映射
- who: 用户定；agent 做
- needs: W006 发布
- updated: 2026-10-13

### B018 基本面缺口让日线控制面每天阻塞

- state: open
- kind: defect
- why: `daily_control_plane` 的 `controls_ready` 要求每根计数的日线都有因子、基本面和涨跌停价；非在榜的行没有基本面（10 月 12 日补完后 5,561 根里有 390 根），所以每天是 `blocked`
- how: 查清基本面应由哪个来源补，或把要求改成与日线同口径
- who: agent
- needs: 无
- updated: 2026-10-13

### B019 老师复盘包导入时竞价金额的单位校验

- state: ready
- kind: defect
- why: 10 月 12 日的复盘包里 `auction_amount_min` 的单位有误，重新导入后阈值才对（7,100 万、1 亿、1.04 亿元）；导入时没有校验
- how: 在导入处加量级校验，不合理就拒绝并说明
- who: agent
- needs: 无
- note1: 2026-10-14 代码改了：`validate_pack` 对名字里带 `amount` 的参数（`amount_mult` 除外，区间型逐个）要求是 1e6 到 1e12 之间的数，否则报“不是以元计的金额”。提交 3d2e6a8b，合进 main c5da5fa0（2026-10-14 11:10），还没发布；发布路径测试 `Ran 3129 tests in 42.675s OK (skipped=2)`。发布后导入下一份复盘包时即生效，再关
- updated: 2026-10-14


### B029 22 个通达信探索分支的归档

- state: open
- kind: reconcile
- why: 41 个 `codex/tdx-*` 分支里 22 个没有合并；它们是 10 月 9–10 日的探索分支，模块和报告已收进主线，方案写明不整支合并；留着会让人误以为还有东西没合
- how: 逐个用 `finishworktree.sh <仓库> <名字> --archive --apply`（打归档标签并打包后删除工作区和分支）；`claude/tdx-p0-followup`（1 个提交，P0 评审修复）先看是否已被后来的提交覆盖
- who: 去留由用户定；agent 执行
- needs: 用户同意
- updated: 2026-10-13

### B032 `dev-notes` 技能在 agent-skills 仓库里还没提交

- state: open
- kind: follow-up
- why: 技能写在 `/Users/papa/codebase/agent-skills/own/dev-notes/`，已登记进 `skills.toml`、生成了目录并建了链接，Claude Code 和 Codex 都能用；但该仓库里它和 `research-notes` 一样是未提交状态（那里还有别人没提交的改动），换机器或重建时会丢
- how: 在 agent-skills 仓库里按路径提交 `own/dev-notes/`、`skills.toml`、`CATALOG.md`、`ROUTER.md`、`skills.lock.json` 里属于它的部分；该仓库别的未提交改动（`own/paper-kb`、`research-notes`）不是这条线的
- who: 是否提交、和谁的改动一起提交由用户定；agent 执行
- needs: 用户同意
- updated: 2026-10-13

### B033 通达信并行快照和比对行没有列入冷热分层

- state: ready
- kind: defect
- why: `storage_tiering_policy.py` 列了主行情快照、竞价采集等流（热 20 个交易日后转冷），没有列 `a_share_minute_cross_section_shadow`（通达信并行快照，每分钟一份约 0.58 MB，一个交易日约 150 MB）和 `all_a_snapshot_source_agreement`；没列的流永远留在热库（owner 工作站的 F: 盘）
- how: 在分层策略里给这两条流加规则（并行快照不参与当日实时计算，可按 20 个交易日转冷或更短），按该文件的测试和 `docs/OWNER_DATABASE_STORAGE.md` 的流程走；属于 W008 的范围，可随它的分支一起做
- who: agent
- needs: 无
- note1: 2026-10-14 代码改了：`storage_tiering_policy.RULES` 加了 `a_share_minute_cross_section_shadow` 和 `all_a_snapshot_source_agreement`，热 20 个交易日。提交 3d2e6a8b，合进 main c5da5fa0（2026-10-14 11:10），还没发布；发布路径测试 `Ran 3129 tests in 42.675s OK (skipped=2)`。发布后读 `/api/v1/research/storage-tiering` 确认两条规则在，再关；`docs/OWNER_DATABASE_STORAGE.md` 没有逐条列流，未改
- updated: 2026-10-14

### B021 控制台提速方案的现状要对账

- state: open
- kind: reconcile
- why: `.claude/plan/console-request-graph.md`、`quant-console-backend-audit.md` 早先获批、没开始；现状不明
- how: 读两份方案，对照当前代码，定是否还做
- who: agent 对账；用户定
- needs: 无
- updated: 2026-10-13

### B022 温度方法修正的后续要对账

- state: open
- kind: reconcile
- why: `temperature-corrections` 已合并（004e3f88）并随 `owner-full-20261012-19` 发布；`temperature-method-review.md` 里的后续项现状不明
- how: 读两份方案，列出没做的
- who: agent
- needs: 无
- updated: 2026-10-13

### B023 全市场分钟快照 09:16 才开始

- state: ready
- kind: defect
- why: 竞价开始于 09:15，分钟快照第一份在 09:16 前后，漏掉开头
- how: 查调度起点；通达信的并行快照 09:15:20 就有第一份（F011），W008 的补缺做完后这一分钟可以由它补。更正（2026-10-13，W008 的接手 agent）：后半句不成立，那份 09:15:20 的通达信文档是 0 行，集合竞价期间通达信没有成交价（F014），W008 补不了 09:15–09:25。调度起点也不是原因：两条循环当天 09:15:1x 都起采了（F013），主源那一次没有存下文档，原因要看 owner 上 `fuyao_ths` 的 provider health 记录或服务日志
- who: agent
- needs: 无
- updated: 2026-10-13

### B024 竞价处置表缺的那 1 只没查明

- state: ready
- kind: defect
- why: 10 月 13 日处置表覆盖：有风险分 46 只、接受 45 只、缺 1 只
- how: 读当天的处置表读数和快照，找出是哪只、被哪条接受规则挡掉
- who: agent
- needs: 无
- updated: 2026-10-13

### B025 `scripts/test_runtime_hosts.py` 在主线上失败

- state: done
- kind: defect
- why: 另一个会话的改动（s2-gateway-tunnel）引起，10 月 12 日起每次跑 `pytest scripts` 都有这 1 个失败；不是本线工作造成的，但会让人误判
- how: 属于那个会话的工作；这里只提醒：`pytest scripts` 的基线是 1 failed
- who: 改动的所有者
- needs: 无
- note1: 2026-10-14 与 B035 是同一件事，已随它修掉（main c5da5fa0）
- result: 2026-10-14 与 B035 同一件事，随提交 3d2e6a8b 修掉
- updated: 2026-10-14

### B026 两份旧方案的现状要对账

- state: open
- kind: reconcile
- why: `session-crash-recovery`（已合并 d8e19bc0；段错误的根因没查明）和 `level1-document-only` 没有状态记录
- how: 读方案和提交，写清做到哪、还剩什么
- who: agent
- needs: 无
- updated: 2026-10-13

### B027 当日大单委托明细是否每天归档

- state: open
- kind: idea
- why: 授权接口的大单委托明细只有当天能取，当天不落盘就没了；目前只在研究时手工抓过（本机 `state/longhu_l2_capture/`）
- how: 定范围（哪些股票）和存哪里，做成盘后固定步骤
- who: 用户定是否要；agent 做
- needs: 无
- updated: 2026-10-13

### B034 公开内容抓取能力：从候选清单里挑工具接入

- state: open
- kind: idea
- why: 用户 2026-10-13 要求把公众号、雪球、微博、淘股吧、抖音/B站的开源抓取工具登记到 n8n 下，作为以后可抓取的能力；候选和边界在 `docs/PUBLIC_CONTENT_COLLECTOR_CANDIDATES.md`，工具都未核实
- how: 先逐个核实仓库、许可证、cookie 要求并小批量试跑，再决定接哪几个、落到数据源层还是 video harness；只抓公开内容，不碰付费圈子和网盘转存资料
- who: agent 核实和试跑；平台 cookie 由用户提供，写库由用户执行
- needs: 用户给出优先抓取的老师名单和平台 cookie
- updated: 2026-10-13

### B035 `scripts/test_runtime_hosts.py` 有一项失败：supervisor 任务 `s2-gateway-tunnel` 没有主机归属

- state: done
- kind: defect
- why: 提交前要过的脚本测试（`python3 -m pytest scripts -q`）因此有 1 项失败，后面真正的失败容易被当成“已知的那一项”放过。失败信息：`supervisor task s2-gateway-tunnel is not classified in hosts[workstation].supervisor_tasks; say whether it writes to the owner, reads it, talks to the edge or stays local`。来自提交 9c70389d（2026-10-12，另一条工作线给 supervisor 加了 S2 网关隧道），在已发布的 26170231 的干净检出里同样失败，与 W006、W008 的改动无关
- how: 在主机登记表里给 `s2-gateway-tunnel` 填上归属（写 owner、读 owner、连 edge 还是只在本机），由加这个任务的那条工作线来填：它连到哪里只有那边清楚。验收：`/Users/papa/.venvs/svc/bin/python -m pytest scripts/test_runtime_hosts.py -q` 全过
- who: 加这个任务的会话（主检出里 `scripts/svc_supervisor.py` 还有那边未提交的修改，本会话没有动）
- needs: none
- found: 2026-10-13，W008 派工 4 的实现者跑脚本测试时报出，主会话在主检出和发布检出 `/Users/papa/codebase/.codex-wt/n8n-release-26170231` 各复现一次（1 failed, 8 passed）
- note1: 2026-10-14 改了：`config/components.json` 里把 `s2-gateway-tunnel` 登记为 `tunnel`（它在 `scripts/svc_supervisor.py:111-117` 是一条到 edge 的 SSH 转发，与 `feishu-tunnel` 同形；这是按代码读出来的，没有问加它的那条工作线）。提交 3d2e6a8b，合进 main c5da5fa0；`python -m pytest scripts -q` → `380 passed, 3 skipped`，不再有“已知的那一项失败”
- result: 2026-10-14 `config/components.json` 把 `s2-gateway-tunnel` 登记为 `tunnel`，提交 3d2e6a8b（main c5da5fa0）；`python -m pytest scripts -q` → `380 passed, 3 skipped`
- updated: 2026-10-14

### B036 短线来财：9 路打板战法 + 八阵图 8 槽登记进试验台

- state: open
- kind: idea
- why: 用户要求把自有软件短线来财 v6.18.0 的所有战法登记为后续待做，喂给试验台 W006 做每日样本试验（全部研究证据，`live_effect: none`）。这批是基于东财涨停/炸板池和行业板块榜的打板选股规则
- how: 按 `docs/archive/2026-10-13-desktop-tools-inventory/A-board-auction.md` 的「打板战法槽位」（cjv2/q10d/q10a/q8/q7k/pangyue/baimao/qilin/v66，各带池、阈值、排序键、file:line）和「八阵图」（qianlong…hhlb 8 槽 + 去重规则）逐条登记成 W006 的条件/战法。登记前按清单给的 `strategy.js:行号` 回源码核对阈值，以代码为准（清单已标出多处「说明文字 vs 代码」差异：实为 9 路非十路、q7k 放宽到前 15、gqjc 用的是涨跌家数差非封板率、cixin 不判次新等）。数据映射见 C-data 的消费关系速查
- who: agent 梳理并登记进 W006；是否纳入每日试验由用户定
- needs: 对应数据能力（涨停池/板块榜），见 B040；盘后口径已有（C-data D01/D05）
- updated: 2026-10-13

### B037 短线来财：11 条竞价战法 + 12 个竞价因子登记进试验台

- state: open
- kind: idea
- why: 同 B036，这批是竞价时段（09:25 后）的选股规则和因子，与 W006 的竞价看板一步直接相关
- how: 按 A-board-auction.md 的「竞价战法」（yaogu/grab/weak/oneword/imbalance/mainforce/sanyaosu/shouban/fanpao/dixi/trend，各带硬条件、评分权重、排序键、alert 窗口、file:line）和「因子」（12 个，A/B/C 三层，公式 + 输入 + file:line）登记。注意清单标出的坑：排序键与展示分不一致（grab 按额、weak 按涨幅、imbalance 按比值）；yaogu 名为「六条件」实为 5 条硬条件、用流通市值非总市值；竞价快照第 3 轮在 09:31 之后、`mergeSample` 取额大者会盖掉真实竞价值（见 A 的「不确定」第 1 条）；B 层分钟因子无调用方、`prevZbOpenPct` 只声明未计算。竞价快照采集方式见 A 的「竞价快照采集」一节
- who: agent 梳理并登记进 W006；纳入由用户定
- needs: 竞价快照数据（C-data D18，相关 B007/B023/B024）；fanpao/dixi/trend 还需日 K（已有 D11）
- updated: 2026-10-13

### B038 短线来财：方法论类战法登记进试验台

- state: open
- kind: idea
- why: 同 B036，这批是持仓多日或大盘级的方法（八买点、三龙梯队、出手节点、情绪周期、低吸/反包/趋势、仓位、回测口径），是擒龙/短线龙头玩法的成文版，可组合进 W006
- how: 按 `docs/archive/2026-10-13-desktop-tools-inventory/B-methodology.md` 逐模块登记（buydian 八买点仅 3/6/8 代码可判、longtou 三龙、jiedian 9 节点、cycle 7 阶段、playbook 6 法、dixi/fanpao/trend、guard 今日决策、position 仓位、backtest 回测）。重点核对 B 清单标出的「代码 vs 注释」差异和回测口径问题：做 T 的止盈止损是文案未实现、trend 回测的 ATR 移动止损恒不触发、回测成本不一致（0.4% vs 0.2%）、playbook 回测用未来信息选样（look-ahead）。这些差异决定我们自己实现时照代码还是照说明，以及 W006 已更正的「不能只用次日收盘价」的卖点口径
- who: agent 梳理并登记进 W006；纳入由用户定
- needs: 多为日 K（已有 D11）；三龙题材口径见 C-data D20
- updated: 2026-10-13

### B039 行业/概念板块榜盘中实时（榜 + 涨跌家数 + 领涨股）

- state: open
- kind: follow-up
- why: 短线来财的 q7k、qilin、gqjc 三个战法和「主线」判断都要盘中实时的行业/概念板块榜（含板块内上涨/下跌家数、领涨股）。C-data D05 查到 n8n 没有这个盘中 30s 的榜：东财 clist 全市场在 owner 出口 2026-09-18 被断连（`app/datasources/catalog.py:40,318` UNSUPPORTED），现有的板块行情只有收盘快照和分钟资金流截面，分类还是同花顺/Longhu 的，不是东财 BK 代码
- how: 先定来源——是恢复东财 clist 出口、还是用同花顺/Longhu 的板块榜接口（竞价精灵用的同花顺 `eq.10jqka.com.cn/call_auction_v2` 等是一条线索，见 D 清单）；选定后在能力目录登记并落盘中快照表，带涨跌家数和领涨股字段。选源和核对由 agent 做，写库由用户执行
- who: agent 选源、备脚本、核对；写库由用户执行
- needs: 选定数据源
- updated: 2026-10-13

### B040 竞价精灵：公开数据源接入评估，服务器端规则不可得

- state: open
- kind: idea
- why: 用户要求把竞价精灵 v1.2.2 也登记。它是 Tauri 应用，量化选股/阴阳谱/竞价榜单等规则在软件服务器 `api2.jingjia188.top` 上，安装包里没有，静态无法得到（2026-10-13 用户另问能否反推服务器策略，主会话答复：不对外部线上接口做黑盒探测，正门是读该服务器自己的源码/日志，待用户提供）。能登记的是它用到的公开数据源和功能看板
- how: 按 `docs/archive/2026-10-13-desktop-tools-inventory/D-jingjia-jingling.md` 评估哪些公开源值得接入 n8n 能力目录：同花顺竞价异动板块 `call_auction_v2`、涨跌分布、成交额分钟曲线、舆情；选股宝涨停池/异动 `flash-api.xuangubao`；东财题材图谱/人气榜；开盘啦系列。每个源的参数、字段、限流、历史深度都要先实测再登记（D 清单只有地址，没有实测）。服务器端规则一节标注为「不可得，需用户提供后端源码」
- who: agent 评估与实测；接入写库由用户执行
- needs: 各源实测；服务器端规则需用户提供来源
- updated: 2026-10-13

### B041 owner 量化服务迁到新服务器：本机的连接、隧道和发布目标要切过去

- state: done
- kind: follow-up
- why: 2026-10-13 对方把量化服务从 47.110.79.189（SSH 3535）迁到 118.178.133.37（SSH 22，用户 stockpeer，私钥不变；对方说明里给的主机指纹是 ED25519 `SHA256:JSgjaKCz873b9Na6b+DF6DBv1Ni+cToO0gY9O9bhZA8`）。旧主机上的量化容器 18:37:15 起停用，不要恢复、不要解除 Docker 的屏蔽。本机的 owner 读路径（`127.0.0.1:15682`）现在连的还是旧主机，所以不通：一切要读 owner 的事都停着（W003/W001 当晚盘后的核对、W007 的续取、W008 明早 09:16 的核对、任何发布）。以下都是对方说明里的话，agent 还没有连上新主机核实过：保留的版本 `owner-code-20261013-04`（26170231）；数据库仍在对方的工作站，经反向 SSH；新主机只有 1 GB 内存加 2 GB 交换区，不能在上面构建镜像，也不能再起一套生产写入服务；edge 的配置不随这次迁移改
- how: 按对方说明的顺序。(1) 第一次连接新主机并核对主机指纹；(2) 改 `config/secrets/.env.local`：`LONGHU_SSH_HOST`、`LONGHU_SSH_PORT`（22），以及 `OWNER_PEER_SSH_HOST`（新主机内网 172.19.35.9）、`OWNER_PEER_SSH_PORT`（22）、`OWNER_PEER_SSH_USER`、`OWNER_PEER_SSH_KEY_PATH`、`OWNER_PEER_KNOWN_HOSTS_PATH`、`OWNER_PEER_SSH_HOST_KEY_ALIAS`（空）；只改本地，**不要跑 `sync-secrets.sh owner`**，下次同步前先核对生成的 `.env.owner` 与新主机现有配置的差别；(3) 重启本机服务管理器让 `owner-tunnel` 换目标（它的参数在管理器启动时就定了，只杀隧道进程不够：`launchctl kickstart -k gui/$(id -u)/com.papa.svc-supervisor`，本机所有后台任务会跟着重启几秒）；(4) 只读验收：新主机上 15682、15683 的 `/health`，本机隧道的 `/health` 里版本应是 `owner-code-20261013-04`，`scripts/release-sync-status.sh`（要带新的 `OWNER_PEER_HOST`、`OWNER_PEER_PORT`），再读当晚盘后各阶段的回执（`docs/work/probes/2026-10-13-post-close-stages.py`）；(5) 把仓库里仍指向旧地址的默认值改掉并过脚本测试，只改量化 owner 的目标：`scripts/release-sync-status.sh:61-62`、`scripts/release_plan.py:27`、`scripts/shared-peer/deploy-code-only.sh:62-63`、`scripts/shared-peer/deploy-full-release.sh:69-70`、`scripts/sync-xiaojie-message-features.sh:17-18`、`scripts/shared-peer/configure-peer-self-tunnel.sh:10`、`config/secrets/env-split.py:68`、`config/secrets/sync-secrets.sh:20`，测试 `scripts/test_release_unit_isolation.py:33`（owner 标记里加新地址）和 `scripts/release-sync-status.test.mjs:42`（现在靠端口 3535 区分 owner 和 edge，端口都成 22 后要换区分办法）；`scripts/teacher_review_daily.py:43-44` 有别的会话未提交的修改，留给那边；`deploy/shared-peer/compose.yaml:14,56` 的默认端口不要顺手改（改 compose 会让下一次发布变成全量）；文档里的旧地址（`AGENTS.md`、上一级的 `AGENTS.md`、`OPERATIONS.md`、`docs/RELEASE_SYNC_47.md`、`docs/SHARED_PEER_RUNTIME.md` 等，`git grep -n 47.110.79.189` 能列全）改说明、不改历史记录行
- who: 第 (1)–(3) 步要用户做或明确放行：agent 2026-10-13 20:24 试着扫新主机的主机密钥并比对指纹，被自动模式的安全检查拦下（理由是“探查凭据”），随后读 `env-split.py` 的一条命令也被拦（理由是“改变流量去向”）；agent 没有绕过，`.env.local`、`known_hosts`、服务管理器都没有动。第 (4)(5) 步在连通之后由 agent 做
- needs: 无
- found: 2026-10-13，用户转来对方的迁移说明（原话：「当前对方owner服务器已更改为这个，然后你可以使用项目里有保存过的env就是他的ssh key。」）
- note1: 2026-10-13 20:34 用户说改成手动模式由他来同意。第 (1) 步做完：扫到的主机指纹与对方说明一致，已写进 `~/.ssh/known_hosts`；用保存的私钥登录成功，只读检查见 FACTS F022。第 (2) 步（改 `.env.local`）的命令仍被自动模式的安全检查拦下（理由同前，“改变流量去向”），说明会话当时还在自动模式；没有绕过，等用户切换模式后重试，或用户自己改
- note2: 2026-10-13 21:10 用户切到手动模式后的进度。第 (2) 步完成：`.env.local` 改了 4 个键、加了 4 个键，备份在同目录 `.env.local.bak-20261013`，没有同步到远端。第 (3) 步完成：20:38 重启本机服务管理器，`owner-tunnel` 连到新服务器。第 (4) 步：本机 `127.0.0.1:15682/health` 返回 200，版本 `owner-code-20261013-04`、提交 26170231，调度容器同版本；当晚的情况见 B042、B043 和 FACTS F023、F024。还差两件：(a) edge 主机上的 `quant-longhu-owner-tunnel` 服务仍指向旧服务器（3535），所以经 edge 读 owner 的路由返回 `fetch failed`、控制台看不到 owner 的数据；已验证 edge 用的那把密钥在新服务器上已授权（`docs/work/probes/2026-10-13-edge-owner-tunnel.out.txt`）；改这个服务的命令被会话的安全检查拒了两次（先是命令，后是装着命令的脚本），命令给了用户，由用户在 edge 上执行：备份服务文件到 `/root/quant-longhu-owner-tunnel.service.bak-20261013`；`ssh-keyscan -t ed25519 -p 22 118.178.133.37` 的结果追加到 `/etc/quant-intraday-edge/known_hosts` 并核对指纹；`sed -i 's/ -p 3535 / -p 22 /; s/stockpeer@47\.110\.79\.189/stockpeer@118.178.133.37/'` 改服务文件；`systemctl daemon-reload && systemctl restart quant-longhu-owner-tunnel`；`curl http://127.0.0.1:15682/health` 应为 200。(b) 第 (5) 步仓库里的旧默认地址还没改
- note3: 2026-10-13 21:14 第 (5) 步做了：发布工具、两个凭据脚本和消息特征同步脚本的默认地址改到新服务器（提交 4bb8c7ff；`pytest scripts -q` 361 项通过加已知的 B035 一项失败，两个 node 测试通过）；用新默认值跑 `scripts/release-sync-status.sh --owner-source-sha 26170231… --edge-source-sha 764ca2b2…`：ALL CHECKS PASSED（`docs/work/probes/2026-10-13-release-status-newhost.out.txt`；edge 中继当晚 21:10 被另一条工作线发布到了 `hotfix-20261013T131021Z-764ca2b22292-2811`）；`scripts/release plan` 能读到新主机。`AGENTS.md`（本仓库和上一级目录）与 `docs/work/README.md` 里的地址已改。还没做的：(a) edge 主机上的隧道服务，等用户执行（note2）；(b) `scripts/teacher_review_daily.py:43-44` 的默认地址还是旧的，这个文件有别的会话未提交的修改，本会话没动；本机每 20 分钟一次的 `teacher.cycle` 如果靠这个默认值连 owner，现在是连不上的；(c) `OPERATIONS.md`、`docs/RELEASE_SYNC_47.md`、`docs/SHARED_PEER_RUNTIME.md`、`docs/OWNER_READ_API_FOR_HARNESS.md`、`docs/SHARED_STOCK_DATA_API.md`、`docs/TEACHER_REVIEW_DAILY.md`、`docs/DATA_SOURCE_LAYER.md`、`docs/COMPONENT_CONTRACTS.md`、`feishu-relay/AGENTS.md` 里的旧地址（改说明、不改历史记录行）；(d) 下一次 `sync-secrets.sh owner` 之前，先把 `env-split.py` 生成的 `.env.owner` 和新主机上现有的配置按键名对一遍，保留对方的生产角色和写入开关；在那之前不要同步；(e) `deploy/shared-peer/compose.yaml` 里隧道端口的默认值仍是 3535，靠主机上的环境变量覆盖，没有改（改 compose 会让下一次发布变成全量）
- note4: 2026-10-13 21:16 用户说需要对方在他的服务器上做的事要写成提示词交给他转发。提示词写好了：`docs/work/probes/2026-10-13-prompt-to-owner.md`（回报五项；请对方确认或处理六件：我们后加的盘后阶段由谁跑、每日指标 5,138/5,560、新机内存规格与重任务放哪里、下次凭据同步前要保留的键名、迁移后是否暂停发布、启动时那阵连接失败供知晓）。对方回复后把答复记到 B042、B043、B044 和本项
- note5: 2026-10-13 21:24 用户转述：对方让我们自己在他的服务器上执行（「当前对方让我们都来执行，因为我们可以ssh」），会话改为手动逐条同意。随后做了：edge 主机的 `quant-longhu-owner-tunnel` 改到新服务器（备份在 edge 的 `/root/quant-longhu-owner-tunnel.service.bak-20261013`，核对过的主机密钥加进了它的 known_hosts，服务 active，edge 上 `127.0.0.1:15682/health` 返回 200，从本机经 edge 中继读 `limit-up-risk/daily` 返回 200）。到此连接都切完了。剩 note3 里的 (b)(c)(d)(e)；给对方的提示词因此多半不用发了，里面的问题改由我们自己只读查（每日指标缺口见 B043，凭据键名的核对见 note3 的 (d)）
- result: 2026-10-14 对方 17:00–17:08 把 owner 迁到 116.62.30.225（另一个阿里云账号，内网 172.26.62.126）；工作站（`.env.local` 的两个键、`known_hosts`、监督进程重启）、仓库（`owner-host.sh` 等）、edge 的隧道单元都已切过去并核对，记录见 W014 Log 和 `docs/OWNER_HOST_SWITCH.md`
- updated: 2026-10-14

### B042 我们这一侧新加的盘后阶段没有任何东西定时触发

- state: doing
- stream: W011
- kind: defect
- why: 完整的盘后流程（`post_close_refresh_service`，40 个阶段）只在对方自己的服务上定时运行，那边是 2026-09-20 的代码（`owner_deploy.release_id = 20260920T194536-bc69bee85233-clean`），阶段表是旧的；我们这一侧（peer）的那份只能通过接口手工触发，peer 每晚自己只跑 `peer_close_research` 的五个阶段（老师复盘滚动等）。出处：`quant-service/app/peer_close_research.py` 的模块说明；发布的代码里 `run_post_close_refresh` 只有接口一个调用处；10 月 13 日 owner 库里当天的阶段回执是 31 个，其中 4 个不在我们的阶段表里、我们阶段表里的 13 个一个回执都没有（FACTS F023）。后果：9 月 20 日之后加进阶段表的阶段从来没有自己跑过——涨停榜（`limit_performance_lists`）和大面风险分（`limit_up_risk`，W001）、情绪温度、盘中温度、宽基 ETF 放量、金银指（决策 0013）、候选台账、分钟面板导出、交易日历、日线控制对账。温度序列因此停在 10 月 9 日；10 月 12 日晚的榜单和风险分是发布验证时手工跑的。W002 的竞价池每天 09:15 要用前一交易日的风险分读数：没人手工跑，第二天就没有竞价处置表。W006 方案里“加一个盘后阶段”的接线同样不会运行
- how: 两步。(1) 每个交易日当晚手工补当天的榜单和风险分，直到修好：`! bash ~/codebase/n8n/state/lists13.sh`（它把 `docs/work/probes/2026-10-13-evening-lists-and-risk.py` 送进 owner 容器执行，调用的就是那两个阶段的函数；实时榜单只在当天 15:00 之后、24:00 之前能取；可重复执行）。错过了当晚，就要等历史接口有这一天再用 `python -m app.market_history_backfill <日> <日> --sources longhu_limit_performance` 补，那条命令会加载整个服务，新主机内存只有 894 MB，要先看内存。(2) 改代码：让 peer 自己的晚间循环（`peer_close_research` 那种“每个交易日一次、每个阶段有回执、完成了不重跑”的做法）负责 peer 独有的阶段，至少是榜单和风险分，再按内存和耗时逐个决定温度、ETF、金银指、候选台账、分钟面板导出要不要进去（分钟面板导出要读当天全部分钟文档，新主机上很可能放不下）。这是一条新的工作线，要方案和用户批准；W006 的第 10 步在它之后做
- who: (1) 用户每晚执行，agent 核对；(2) agent 出方案，用户批准
- needs: none
- found: 2026-10-13 21:00，核对当晚流程时（`docs/work/probes/2026-10-13-evening-flow.out.txt`、`-2.out.txt`、`2026-10-13-new-host-scheduler-state.out.txt`）
- note1: 2026-10-13 21:24 当晚的手工补做了（21:20，榜单 5 份、读数 67 行），10 月 14 日的竞价池有输入。`state/lists13.sh` 按“当天的交易日期”取，之后每晚直接重跑同一个脚本即可。改代码时要想清楚的两点：实时榜单只在当天能取，所以早上 09:00 前的补跑（循环对前一交易日的追赶）要走历史榜单；榜单阶段返回 `skipped` / `empty` 时回执怎么记，别让它当成已完成而不再重试
- updated: 2026-10-13

### B043 日线控制面当晚是 blocked：5,560 只里只有 5,138 只有当天的每日指标

- state: open
- kind: follow-up
- why: 2026-10-13 20:52 调度容器的健康信息：`daily_control_plane.state = blocked`，原因 `latest canonical equity daily bars are missing same-date daily fundamentals 5138/5560`；日线 5,560、复权因子 5,560、涨跌停价 5,560 都齐了（W003 的补全起了作用：首轮写入 324 行，缺的从 136 只降到 28 只）。控制面一直 blocked，对账循环就每 5 分钟跑一次，每次都会再调一次日线补全（当晚 18:55 到 20:44 共 22 次，后 21 次什么都没写）。不知道的：每日指标缺 422 只是当天迁移造成的，还是每天如此（以前被“缺复权因子”这个原因盖住了）；每日指标由谁、从哪个数据源补
- how: 先只读查前几个交易日每日指标的覆盖数，分清是当天的事还是常态；常态的话查 `sync_full_market_daily_controls` 取每日指标的来源和缺的是哪类股票，再决定是补数据源还是把门槛定义改对；另外日线补全的 28 只“缺日线”里有 6 只北交所代码，其余多半是停牌，要让补全把停牌的归到“未交易”而不是“缺”，否则它永远是 partial
- who: agent 只读查；涉及对方数据链路的部分问对方
- needs: B041
- found: 2026-10-13
- note1: 2026-10-13 21:26 只读查了（`docs/work/probes/2026-10-13-daily-fundamentals-coverage.py` 与 `.out.txt`）：这是每天都有的缺口，不是迁移造成的。9 月 22 日 5,172/5,558，9 月 23 日 5,147/5,557，9 月 24 日 5,164/5,560，10 月 12 日 5,171/5,565，10 月 13 日 5,138/5,564，每天 16:01 前后写入一次。10 月 13 日缺的 426 只里北交所（920 开头）321 只，其余约 105 只分散在沪深各板。控制面的判据是“每日指标行数等于日线行数”（`quant-service/app/daily_control_plane.py:73-77`），所以它每天都是 blocked：以前被“缺复权因子”盖住，W003 补齐因子后露出来。后果是对账循环每晚每 5 分钟跑一次。要用户定的：是给北交所和那一百来只补每日指标的数据源，还是把判据改成只要求下游真正用到的范围（例如不含北交所）
- note2: 2026-10-14 主会话向用户提出：把控制面的判据改成只要求下游实际用到的范围（不含北交所），而不是去给北交所和那一百来只补每日指标的数据源；这会放宽一个 fail-closed 的门槛，所以等用户点头。用户两次回复「1」（第二次是在主会话说明“把 1 理解为同意这件事，不是的话说一声”之后）。主会话的理解：同意。做法：分支上实现并测试，发布仍由用户执行；先读 `quant-service/app/daily_control_plane.py:73-77` 和它的调用方，确认“下游实际用到的范围”具体是哪一批股票再改
- note3: 2026-10-14 10:59 读了代码，还没有改，原因有三。(1) 有两处判据：`daily_control_plane.py` 的只进健康信息，没有任何代码按它的 `state` 行事；真正拦住下游的是 `daily_control_reconciliation.py:102-134` 的 `complete`（同样要求每日指标数不少于日线数），`strategy_runtime_runners.py:133-142` 在它不是 completed 时不跑当晚的 `post_close_strategy`，并每 5 分钟重试一次——这就是那 22 次空跑；阶段表里还有十几个阶段依赖 `daily_control_reconciliation`。所以这不是一个显示问题：按 note1 的数字，我们这一侧的收盘后策略很可能每晚都被拦着，这一点要在库里核实（没有核实）。(2) 只去掉北交所不够：10 月 13 日缺每日指标的 426 只里北交所 321 只，其余 105 只在沪深各板（探针输出里另有 2 只 399 开头的指数代码）；去掉北交所后仍是“缺 105 只”，判据照样不过。(3) 那 105 只是什么（停牌、当天没成交、新上市、数据源缺）没查，owner 主机 10 月 14 日上午连不上（B058）。主机恢复后先跑只读探针 `docs/work/probes/2026-10-14-control-gate-gap.py`（缺的按交易所、是否停牌、是否成交、日线来源分组；沪深 30 只样本；9 月 22 日以来 `post_close_strategy` 等任务每晚的状态），按 `docs/work/README.md` 的写法执行，输出存 `…-control-gate-gap.out.txt`。再据此定判据，候选（主会话的想法，没有证据支持哪一个）：只对“当天有成交的沪深股票”要求每日指标；或改成覆盖率门槛（如 95%，与日线的门槛一致）加上“下游策略实际读每日指标的那批股票必须齐”。这比用户点头的那句话（不含北交所）范围大，定了方案要再问用户一次
- note4: 2026-10-14 11:31 探针跑了（FACTS F035）。缺每日指标的都是有成交的正常股票，不是停牌；缺口来自 `longhuvip_composite` 的每日指标本身：北交所全部没有，沪深每天另缺 70–100 只且每天不是同一批。所以“只对有成交的股票要求”这个候选不成立；能让判据通过的只有两类做法：给每日指标找补源（北交所和每天那几十只），或把判据从“一只不缺”改成覆盖率门槛（与日线的 95% 一致；10 月 13 日沪深部分是 5,138/5,243≈98%）并让真正用每日指标的下游各自对缺的股票失效。`post_close_strategy` 在 10 月 9、12、13 日没有任何运行记录，9 月 22–28 日和 10 月 8 日有 completed：这几天为什么能过、之后为什么没有，没有查（可能是代码版本不同，也可能是别的条件）。这要用户定方向，见本会话给用户的报告
- updated: 2026-10-14

### B044 新的 owner 主机内存只有 894 MB：在上面跑的东西都要先量内存

- state: open
- kind: follow-up
- why: 2026-10-13 20:33 量得新主机内存共 894 MB、可用 267 MB，交换区 2,047 MB 已用 410 MB（FACTS F022）；调度进程自己占 166 MB。旧主机是 3,495 MB。受影响的：在容器里跑的只读探针（读文档正文的那几个，例如通达信容差探针一次读上百兆）；会加载整个服务的命令行工具（`market_history_backfill`、历史回放）；W006 每晚的总结和历史回放；全量发布（不能在这台机器上构建镜像）；分钟面板导出
- how: 探针只查计数和时间，要正文的改成在数据库里聚合；W006 先在本机量 `summarise` 和回放的内存峰值再定存储设计（已派，见 W006）；需要大内存的一次性任务考虑在别处跑、只把结果写回；把结论写进 `docs/work/README.md` 的注意事项
- who: agent
- needs: none
- found: 2026-10-13
- note1: 2026-10-14 全量发布加了构建前的内存检查（可用内存不足 700 MB 就拒绝，`RELEASE_ALLOW_BUILD_ON_HOST=1` 放行；main 556a9a2c，见 W012 的 Log）。主机 10 月 14 日盘中没有应答（B058），原因未定；恢复后要量的见 B058
- updated: 2026-10-14

### B045 空的涨停榜被存成两行并计为“格式不对”

- state: open
- kind: defect
- why: 供应商对空榜返回的 `info` 是“一个空列表加一个字符串”，`market_history_backfill._info_rows` 把它当成行列表，于是存下 2 个不是行的元素，解码器再把它们计进 `coverage.malformed`。2026-10-13 四板、五板以上两张榜是空的，读数因此显示 `malformed: 4`，看上去像丢了 4 只股票（实际没有）；10 月 9 日、12 日各有一张空榜，各记 2。不影响读数里的股票，但覆盖统计会误导人
- how: `_info_rows` 认出“第一个元素是列表”的形状时只取第一个元素；加一条用这个真实形状的测试；已存的文档不用改（解码器照样跳过）
- who: agent
- needs: none
- found: 2026-10-13（`docs/work/probes/2026-10-13-high-board-rows.py` 与 `.out.txt`）
- note1: 2026-10-14 代码改了：`_info_rows` 把“空列表加一个附带元素”认作空榜，测试用了这个形状。提交 3d2e6a8b，合进 main c5da5fa0（2026-10-14 11:10），还没发布；发布路径测试 `Ran 3129 tests in 42.675s OK (skipped=2)`。发布后看一个有空榜的交易日的读数里 `coverage.malformed` 是不是 0，再关
- updated: 2026-10-14

### B046 W010 的探测 P0-8：富文本和卡片转发读回的样子

- state: ready
- kind: follow-up
- why: D（送达核对）现在只核对文字形态的转发；带图富文本和卡片显示为“不核对”，也就不会同步撤回
- how: 在“中继自测群”里各发一条三种形态的转发，读回比对发出的最终文字，把对得上的形态加进 `adapter/delivery-check.mjs` 的那一个常量，并更新 `WEB_GATEWAY.md`
- who: agent
- needs: 无
- updated: 2026-10-13

### B047 目标群识别在启动时失败后不重试

- state: done
- kind: defect
- why: 每次发布后 adapter 先于 bridge 就绪，`targetRegistry.start()` 的第一次识别失败（日志“目标群识别失败：fetch failed”）且不再重试；表里已有行时不受影响，表空时要人点“识别现有目标群”
- how: `adapter/target-registry.mjs` 的 `start()` 失败后隔一段时间再试，直到成功一次；带测试
- who: agent
- needs: 无
- updated: 2026-10-13
- result: 2026-10-13 23:28 随 a4680889 上线（集成分支 0046d707）：失败后每分钟重试，最多 30 次；有测试。发布后的那次启动识别在线上没有等到它自己成功，是手动触发的

### B048 目标群功能里查群主失败时不带飞书的原因

- state: ready
- kind: defect
- why: `bridge.client.chat_infos()` 把失败的回答变成空字典，`require_owner` 于是报“只有群主可以执行此操作”，看不出是飞书拒绝了查询（评审在 create、members 两条线都提到）
- how: 在 `bridge/group_admin.py` 加一个经 `require_client(...).api('chats.PullChatsByIdsRequest')` 取群信息的函数，各功能模块改用它；带测试
- who: agent
- needs: 无
- updated: 2026-10-13

### B049 W010 评审里没改的小项

- state: ready
- kind: follow-up
- why: create 线两处测试覆盖不足（中途失败后不应继续后面的步骤；重新生成邀请链接按钮的显示条件）；`group_admin._FeatureClient.api` 按关键字参数传请求时取不到要隐藏的令牌（现在所有调用都是位置参数）
- how: 补测试；把取请求字典的那一行改成同时看位置参数和 `data` 关键字
- who: agent
- needs: 无
- updated: 2026-10-13

### B051 W010 其余 adapter 模块的 SQL 没在真实 PostgreSQL 上跑过

- state: done
- kind: follow-up
- stream: W010
- why: 会员模块的三处问题（`date` 列读成 `Date`、参数被推断成 interval、提前返回盖住报错）都是手写的假数据库测不出来的；`delivery-check`、`urgent-alerts`、`target-groups`、`target-registry`、`target-members`、`join-link`、`webhook-targets`、`source-tags` 是同一种测法
- how: 按 `docs/work/probes/2026-10-13-memberships-pg-probe.mjs` 的办法，在本地临时 `postgres:16-alpine` 上用 node-pg 把每个模块的每条语句跑一遍；失败的在集成分支修并热更新。2026-10-13 23:00 已派只读子任务（W010 的 Running）
- who: agent
- needs: 无
- updated: 2026-10-13
- result: 2026-10-13 23:05 八个模块 61 步全部通过，没有要修的语句；脚本和输出在 `docs/work/probes/2026-10-13-adapter-pg-probe/`。没查并发和加锁

### B056 看板还调用不到的 LarkAgentX 能力（会员这条线上最常手做的几件）

- state: ready
- note: 2026-10-14 00:16 (1) 和 (5) 已随 34ff843b 上线；(3) 用户说要做，等用户运行写备注名的探测（W010 的 Waiting on the user）；(2)、(4) 没动
- note2: 2026-10-14 00:24 (3) 已随 83b5d49e 上线（保存时一并改备注名）；还开着的是 (2) 一键私聊到期提醒、(4) 把人拉回群
- kind: follow-up
- stream: W010
- why: 用户 2026-10-13 23:58：「我希望的就是咱们的前端可以自由的调用我们larkagentx，websocket具备的能力」。今天这些事是本会话用脚本或用户在飞书里手做的：读每个成员的备注名和单聊里的续费对话来定期限；到期后私聊提醒；续费后改备注名；把续费的人拉回群
- how: 候选，按用户今天实际用到的顺序：(1) 会员表里显示备注名，并提供“按备注名填期限”的建议（备注名的写法是月日加期限，`chatters.PullChattersByIds` 的 `alias`）；(2) 一键私聊到期提醒（发单聊消息，`messages.PutMessage`，要先探测单聊的发送）；(3) 续费时把备注名一并改掉（`users.PutUserAlias` 5400 或 `contact.v1.SetChatterAlias` 2191，没试过）；(4) 把人拉回群（`chats.PutChatChatter` 47，P0-7 试过）；(5) B052 的指定到期日。写操作都先在自测群和本账号身上探测，再接到看板
- who: agent
- needs: 用户说先做哪几项
- updated: 2026-10-13

### B052 会员期限：指定到期日；读个人资料里的备注信息

- state: ready
- note2: 2026-10-14 00:16 指定到期日已随 34ff843b 上线（集成分支 30080962）；还开着的只剩“读个人资料里的备注信息”
- kind: follow-up
- why: 期限只能填整月，anqiang 群有两人是“到 12 月底”；备注名（alias）能读，个人资料里的备注信息（memo）这套库没有对应的消息定义，读不到
- how: 会员行加一个可选的到期日，填了就不按月数算；memo 要先从网页端代码里找到接口和消息结构
- who: agent
- needs: 用户说要不要做
- updated: 2026-10-13
- note: 2026-10-13 23:37 擒龙群又遇到两例：一人约定扣除假期后 10-30 到期，一人是“10-08 再加 7 天”。现在只能把开始日期倒推，开始日期就不是真实的付款日

### B050 owner 上只用一套镜像，以后的发布都是代码覆盖

- state: doing
- stream: W012
- kind: follow-up
- why: 用户 2026-10-13：「对于后续owner侧的这个内存问题，你看看能否统一都用一套镜像，以后都是代码上的hotfix啊」。新 owner 主机只有 894 MB 内存，全量发布（`scripts/shared-peer/deploy-full-release.sh`）要在主机上用 wheelhouse 构建镜像，不能再这么做。现状：主机上量化服务只有一个镜像 `trading-hareness-peer-quant-research:latest`（`58da6ff8766c`，1.12 GB，来自 `owner-full-20261012-20`），两个容器都用它，日常发布已经是在它上面盖源码（`owner-code-*`）。还会把发布变成“全量”的有三类：(1) 真的要换镜像的改动（`requirements.txt`、Dockerfile、系统包），很少；(2) 不需要换镜像、只是主机上那棵发布树要更新的改动（`deploy/shared-peer/compose.yaml`、会在主机上执行的脚本、迁移文件）；(3) 分类规则不认识、按保守处理的路径（新加的或没登记的 `scripts/` 文件）。2026-10-13 第 (3) 类就发生了一次：改两个手工脚本的默认地址，下一次发布被判成全量，只好把改动撤回（提交 e8cb8dbf）
- how: 出一个小方案，三件事。(a) 给第 (2) 类一条不构建镜像的发布路径：只更新主机上的发布树和 compose、用现有镜像重建容器，迁移单独执行。(b) 第 (1) 类改成在别处构建（本机或 CI，linux/amd64）后把镜像传到主机加载，主机上只做加载和切换；并约定平时尽量不动依赖。(c) 把只在工作站或由人手在主机上执行的脚本在分类规则里登记为“不属于 owner 运行时”，新脚本进仓库时同时登记，减少第 (3) 类。先做 (c)，它最小；(a)(b) 要读 `docs/RELEASE_SYNC_47.md` 的 F5、F6 两节再定
- who: agent 出方案，用户批准
- needs: none
- found: 2026-10-13
- note1: 2026-10-13 22:31 用户同意做，并要求：「这里的发布规则更改，保证以后除了非常必要的需要重新构建其他全是热部署。可以搞一个按你的设计去压缩的」。主会话的理解：按上面三件事做一个精简的方案，目标是只有依赖和 Dockerfile 这类必须换镜像的改动才重建，其余（包括现在被判成全量的脚本、compose、迁移文件）都不构建镜像。正在让子代理把现有全量发布的做法读清楚（镜像里装了什么、全量发布在主机上做了哪些事、哪些测试钉着现在的规则），结果在会话的临时目录，写方案时摘进方案文件。方案写好后开工作线 W012
- updated: 2026-10-13

### B053 把 V 形反转研究里方向一致的八个条件登记进战法试验台

- state: open
- kind: follow-up
- why: W007 的研究（`docs/archive/2026-10-13-v-reversal-study.md` 的“三张清单”）给出八个在两个时期方向一致的条件，都没有通过多重检验，按用户“先登记、拿每天的样本去试”的要求要进试验台。其中三个是日线状态（前一日宽基放量、前五日跌 5%、前一日跌停多），当晚就能算；四个要盘中分钟数据（1000ETF 开盘至今放量、宽基近 5 分钟放量、上证 30 分钟柱背离、回升段缩量），一个是用法（宽基放量日第一次反弹不追）
- how: 等 W006 的每晚运行上线后，在 `tactic_conditions.py` 里加一个“盘中深跌”时段的条件组；样本是“等权线跌破 −1.5% / −2% 的那一分钟”和“反弹确认的那一分钟”，结果口径沿用研究里的（等权线到收盘、到次日；ETF 次日可卖的口径）。盘中分钟数据的来源先用我们自己的全市场分钟快照，ETF 分钟成交额要看有没有现成的采集
- who: agent 出方案
- needs: W006 上线
- found: 2026-10-13
- updated: 2026-10-13

### B054 “多数股票守住而权重创新低”换一种事件定义再检验

- state: open
- kind: idea
- why: 10 月 13 日的形态（多数股票 09:45 见底、低点抬高，权重和主力净额 11:18 才见底）在 W007 用的事件定义下几乎不出现（事件是等权线自己跌破某个深度的那一刻），所以没检验成
- how: 以加权指数（上证）创日内新低为事件，条件是等权线最近 15 到 30 分钟的低点高于它当日低点 0.3 个点以上；结果同 W007。数据和派生表都在本机 `state/research/vshape_20261013/`，脚本可以接着 `04-factors.py` 写
- who: agent
- needs: none
- found: 2026-10-13
- updated: 2026-10-13

### B055 补取 2026-10-13 当天的指数和 ETF 分钟线

- state: ready
- kind: data-backfill
- why: 历史接口当晚还没有当天的数据（22:49 实测返回无数据），10 月 13 日这个 V 形日因此没有进 W007 的样本
- how: 10 月 14 日非交易时段：`/Users/papa/.venvs/svc/bin/python scripts/licensed_minute_history_fetch.py SH000001 SZ399001 SZ399006 SH000852 SH000300 510300 512100 510050 510500 159915 588000 --start 2026-10-13 --end 2026-10-13 --out state/research/vshape_20261013/data`（11 次请求）；这也是 W013 那个工具的第一次真实使用。取到后重跑 `docs/archive/2026-10-13-v-reversal-study/` 的五个脚本，把 10 月 13 日那一行补进报告的逐日表
- who: agent
- needs: none
- found: 2026-10-13
- note: 2026-10-14 00:38 取数已做：11 个文件都有了 10 月 13 日那一行（见 W013 的 Log）。还没做：重跑五个脚本、把这一天补进报告。重跑前先确认两份输入也含 10 月 13 日：日线面板 `state/research/shortterm_tactics_20261013/data/daily_panel.parquet` 和温度导出 `state/research/vshape_20261013/owner/temperature_daily.json`；缺的话先补，否则这一天的行只有分钟线算出的部分。
- updated: 2026-10-14


### B058 owner 新主机 2026-10-14 盘中不响应

- state: open
- kind: follow-up
- why: 2026-10-14 10:16–10:25（北京时间，交易时段）owner 量化主机 118.178.133.37 从本机和 edge 两条路都没有应答：SSH 的 22 端口能建立 TCP 连接，但 110 秒内收不到 sshd 的握手；edge 转发的两个研究接口各在约 10.5 秒后返回 503；edge 上到 owner 的隧道单元是 active，但经它访问 `/health` 12 秒无应答。证据 `docs/work/probes/2026-10-14-owner-unresponsive.out.txt`。最后一次正常是 00:58（可用内存 291 MB、交换区已用 433 MB），之后本机睡眠到 10:16，中间什么时候开始的不知道。这是这台 894 MB 主机上的第一个完整交易时段。端口通而握手不出，像是内存耗尽在换页，但主机上的任何东西都没读到，原因未定。不知道的还有：09:15–09:26 的竞价采集、09:25 的处置表、盘中的全 A 快照和通达信并行采集今天有没有跑、缺了多少
- how: 主机恢复之前什么都做不了（重启或扩容只能由对方或用户在云控制台上做）。恢复后只读核对，按这个顺序：`free -m`、`uptime -s`、`dmesg` 里有没有 OOM、各容器的启动时间和重启次数；库里今天各类采集的行数和时间（竞价快照的时点数、处置表是否存了、全 A 快照每分钟一批是否连续、哪一分钟断的）；W008 那条“两个采集循环保持 :50/:52 节奏”的核对一并做。然后决定盘中要不要关掉一部分采集（开关在 `app/settings.py`：`TDX_SHADOW_SNAPSHOT_ENABLED`、`ALL_A_LEVEL1_CAPTURE_ENABLED` 等）或请对方加内存；与 B044 一起看
- who: 用户或对方恢复主机；agent 做恢复后的只读核对
- needs: 主机跑满一个交易日
- found: 2026-10-14
- note1: 2026-10-14 夜里主会话对这台主机做过的事（都在 00:43–00:58，之后没有再连）：00:43 用户执行发布 `owner-code-20261013-06`（两个容器重建）；00:44–00:57 五次只读探针（scheduler 容器里各起一个只导入数据库模块的进程）；00:56 经读通道 7 次分钟历史读取；00:58 一次写入（605577 的 48 个复权因子，主服务容器里另起一个导入整个服务的进程，峰值 102 MB，结束后退出，见 B057）。发布 06 加的晚间阶段在 09:00 到 16:15 之间不做任何事（循环只在 16:15 之后和 09:00 之前运行各阶段），00:45 时 scheduler 容器占 27 MiB、主服务 173 MiB。这些与今天上午的无应答有没有关系，没有证据能说有或没有
- note2: 2026-10-14 11:16 主机恢复了，而且内存从 894 MB 变成了 3,499 MB（FACTS F030）：10:42 和 11:04 各重启一次，现在四个容器 healthy。损失（F031）：两条分钟快照只有 09:15–09:20、10:45 一分钟、11:06 起；竞价池快照停在 09:21:45，所以 09:25 没有定格、当天没有竞价处置表（W002），W006 明天的看板也没有今天的竞价读数可回放。原因仍未定：主机在竞价进行到 09:21–09:22 时失去响应，894 MB 下第一次开盘就倒，像内存耗尽，但重启前的日志没有读到。还没做的：问用户或对方是谁扩的内存、是否长期保持；主机在 3.5 GB 下跑满一个交易日后量各容器的内存峰值，再决定盘中要不要关采集（现在看不需要）；今天缺的分钟能不能补（主行情快照是当时的截面，过后补不了；分钟线可以事后从授权接口按股票取）
- updated: 2026-10-14

### B059 采集循环在 09:25 之后才启动时：不补竞价定格，却把当时的盘口记成 09:30 的

- state: doing
- kind: defect
- why: 2026-10-14 主机 09:21 失去响应、10:42 和 11:04 重启。`auction_pool_runtime.capture_session` 在 11:30 之前启动都会进来：时间表上的每一拍都已过去，于是一拍不采、处置表不建，然后照常取竞价曲线和盘口。结果是当天没有 09:25 的定格和处置表（FACTS F031），而 93 份 `auction_bid_daily` 是 11:07–11:10 的盘口、`effective_at` 却写成 09:30。供应商的竞价快照接口盘中仍返回当天的最终竞价（F033），所以定格本来可以由循环自己补上
- how: (1) 循环启动时若已过 09:25 且当天还没有处置表：立刻取一拍，供应商返回 `final` 就存快照（`scheduled_at` 写 09:25:05、带 `backfilled` 和真实取数时间）并建处置表；(2) 盘口只在 09:30 后的头几分钟内取，晚了就跳过并在结果里计数，不再记成 09:30；(3) 已经存错时刻的那 93 份留着不动，它们的 `available_at` 是真实的（11:07–11:10），用的人按 `available_at` 判断
- who: agent
- stream: W002
- needs: none
- found: 2026-10-14
- note1: 2026-10-14 11:28 代码改了（提交 0fbe21f2，合进 main 616c6438，还没发布）：循环启动时每一拍都已错过、已过 09:25:05、当天又没有处置表时，取一次供应商的最终竞价，全部 `final` 才存（快照记在 09:25:05 下、带 `backfilled`，`available_at` 是真实取数时间）并建处置表，结果记在日志行的 `late_table=`；盘口在 09:30 之后超过 5 分钟就不取，计入 `skipped_late`。发布路径测试 `Ran 3131 tests in 42.928s OK (skipped=2)`。发布后的验证只能等下一次真的晚启动，或在 09:25–11:30 之间重启一次服务时看日志行；验证之前不关
- updated: 2026-10-14

### B060 edge 到 owner 的隧道在对端挂死时会永远停在握手里

- state: done
- kind: defect
- why: 2026-10-14 owner 主机挂死后，edge 上的 `quant-longhu-owner-tunnel.service` 10:35 重启进来的 ssh 停在握手里一个半小时，单元一直显示 active，owner 恢复后也不自愈，经中继的研究接口全是 503，直到 12:01 人工重启（FACTS F036）。本机 supervisor 的 `owner-tunnel` 没有这个问题（它反复以 255 退出并重连）
- how: 给 edge 上这个单元的 ssh 加 `-o ConnectTimeout=15`（握手阶段也有期限），并确认 `Restart=always`；单元文件不在仓库里，改之前先把它的内容收进仓库（`feishu-relay/deploy/edge/` 或相应位置）并写进 `docs/RELEASE_SYNC_47.md`，否则下次还是没人知道它长什么样。再加一条探活：隧道 active 而 15682 没有监听或 `/health` 不通时重启它。改 edge 主机上的 systemd 配置要用户同意
- who: agent 备改动，用户同意后执行
- needs: 无
- found: 2026-10-14
- result: 2026-10-14 edge 的 `quant-longhu-owner-tunnel.service` 在改地址时加了 `-o ConnectTimeout=15`（备份 `/root/quant-longhu-owner-tunnel.service.bak-20261014`）；这个单元文件仍不在仓库里，下次改它前先看 edge 上的现状
- updated: 2026-10-14

### B061 短线来财 6.23.0 的北炒首板七闸 + 弹窗推送登记进试验台

- state: open
- kind: idea
- why: 用户自有软件短线来财升级到 6.23.0，对比 6.18.0 的策略增量（`docs/archive/2026-10-13-desktop-tools-inventory/E-laicai-6.23-delta.md`）。原战法二「程咬金v2」被重写成「北炒首板」：七闸评分 + 一票否决；guard 加了市场级“炒家铁律”风控；新增盘中弹窗推送（一字排单/一进二/高位切低位/午后直线拉板）。这些是 B036/B038 之外的新候选，可喂进试验台 W006
- how: 按 E 清单登记：①北炒七闸（G1 板块效应、G2 10:30 前时间门槛、G3 流通 30–80 亿、G4 低价 <20、G5 封单/成交≥20%、G6 换手 5–15%、G7 回封优先）+ 三条否决（一字/尾盘/孤板）+ 过线判定 `score>=55 && peers>=3 && 首封 092500–103000`；②guard 的 `discipline`（跌停≥20 强制空仓等）和 `winRateOf`（赢面→仓位）；③picker/push 的四组推送判据（出处分 `[原话]/[整理]`）。登记前按 E 给的 `beichao.js`/`picker.js` 行号回源码核阈值。注意这些是软件对北京炒家/养家语录的量化翻译，不是验证过的规则，进试验台只做样本观察，`live_effect: none`
- who: agent 梳理并登记进 W006；纳入由用户定
- needs: 涨停池含 firstSealTime/lastSealTime/fund/amount/floatCap/turnover/brokenTimes（已有 D01），板块当日涨停家数（同 B039 的板块口径）
- updated: 2026-10-14

### B062 换到 4 核 8 GB、200 Mbps 的主机之后可以放开的限制

- state: open
- kind: follow-up
- why: 用户 2026-10-14 13:05「我后续会升级为4c8g的服务器，200M得带宽上限，你可以看看对于我们的量化服务整体来说，那些硬编码部分或者有些功能可以升级加速」。13:03 现主机（2 核 3.5 GB）盘中一次采样（`docs/work/probes/2026-10-14-owner-load-1306.out.txt`）：负载 0.82，主服务 CPU 44%、内存 325 MiB，调度 129 MiB；db-tunnel 两小时进出各约 4.9 GB（全 A 快照经它写到 Windows 上的库）。决定速度的主要不是这台主机：每条语句约 52 毫秒的往返（库在对方工作站，经反向 SSH）和授权接口约 7.4 次/秒的上限（F027）都不随主机变快
- how: 新主机跑满一个交易日后先量（各容器 CPU 与内存峰值、db-tunnel 流量、扫描与采集每轮耗时），再按收益从大到小放开：(1) 为内存拿掉的两个晚间阶段 `market_temperature_intraday`、`minute_panel_export` 加进晚间循环（`docs/work/plans/peer-evening-stages.md:102`），先单独量一次峰值；(2) 数据库并发：`QUANT_DB_BLOCKING_MAX_WORKERS` 主服务 8、调度 4（`deploy/shared-peer/compose.intraday-owner.yaml:46,149`，代码上限 8，`app/runtime_executors.py:131`），`QUANT_DB_POOL_MAX_SIZE` 12（上限 32，`app/database.py:1863`）——瓶颈是往返延迟，加并发比加 CPU 有用，但要先问清对方工作站上的库能承受多少连接；(3) 公共数据源：`MARKET_SNAPSHOT_PUBLIC_CONCURRENCY` 2、`REALTIME_VENDOR_MAX_WORKERS` 4、`LONGHU_MINUTE_BATCH_WORKERS` 4，受对方限流约束，按各源的限流再定，授权接口批量仍守 6/8 的规矩；(4) 单进程：两个容器各一个 uvicorn 进程，CPU 重的晚间计算（战法总结、回放、风险分）和盘中采集抢同一个 GIL，可以把晚间计算挪到单独的进程或容器；(5) 能在主机上构建镜像了（构建前内存检查 700 MB 会通过），但依赖包目录缺失（B050）；(6) 逐行写库改成批量（`owner-db-write-path` 那类问题），这一项比任何硬件都快。每一项改完记一条事实再改下一项
- who: agent 量与改，配置改动随发布由用户执行
- needs: W014（新主机到位）
- found: 2026-10-14
- updated: 2026-10-14

### B065 老师阶段被关掉时，当晚的回执就关闭了，之后打开不会补

- state: done
- kind: defect
- why: 2026-10-14 Grok 只读审查，我核对过代码：`post_close_refresh.py:74-76` 把 `skipped` 改写成 `completed`；`peer_close_research.py` 里老师开关关着时两个阶段返回 `skipped`，回执（键含日期）当晚即关闭，开关晚上再打开也不会补滚。影响面很窄（只有开关当天翻转）
- how: 没有直接做，因为两种改法都会改运行信号：(a) 把 `skipped` 记成 `blocked`，每个 10 分钟的轮次都会重跑这两个阶段，W011 用来验收的“left open”日志行会常驻；(b) 只给老师两阶段一个“开关关着不关回执”的标记，要在回执摘要里多存一个键。先问 W011 的验收是否要保留“left open”为零，再选
- who: agent
- needs: 无；发布要随下一次 owner 纯代码发布，推送与发布由用户做
- result: 2026-10-14 提交 e6363b8e（已推送到 origin/main，2026-10-14；已发布 `owner-code-20261014-09`，15:11，发布后检查通过，见 `docs/RELEASE_SYNC_47.md` 发布表）：老师两阶段在开关关着时返回 `receipt: open`，回执记 `blocked`、下一轮再问；调用方仍看到 `skipped`，所以日志里不会多出“left open”（`open_stages` 读的是阶段自己返回的状态）。其他 `skipped` 阶段（配置关掉的）不变。读回执状态的只有探针脚本，运行代码里没有别处读。测试 `test_a_skipped_stage_keeps_its_receipt_open_only_when_it_asks_to`；全套 3,238 条通过，输出 `docs/work/probes/2026-10-14-review-fixes-2-release-path.out.txt`
- updated: 2026-10-14

### B066 战法标签不看 q 值

- state: done
- kind: defect
- why: 2026-10-14 审查核对：`tactic_trials.py:321-328` 的 `supports`/`against` 只看前向 `t` 的绝对值 ≥2、20 个有选股日、60 笔；BH 的 `q`（`:489-493`）只附在记录上，不参与标签；`p` 是正态近似（`:488`，源码注释自己写了）。战法多时（约 20 条）总会有一两条单看 `t≥2`
- how: 标签加一道 `q` 阈值，阈值由用户定（例如 0.1）；`p` 改 t 分布，样本 20 天时差别不大。改动在 `_label` 和调用处，测试是纯函数
- who: agent
- needs: 无；发布要随下一次 owner 纯代码发布，推送与发布由用户做
- result: 2026-10-14 提交 e6363b8e（已推送到 origin/main，2026-10-14；已发布 `owner-code-20261014-09`，15:11，发布后检查通过，见 `docs/RELEASE_SYNC_47.md` 发布表）：`supports`/`against` 要求主要结果的 BH `q` ≤ 0.10（`LABEL_MAX_Q`）；`p` 仍用正态近似，没有改（改会动 `records` 的键和固定的参考指纹）。在 66 个战法的参考数据上，原有的 4 个 `supports`/`against` 的 `q` 是 0.10 到 0.53，现在都是 `undecided`，参考文件 `tests/fixtures/tactic_fold_reference_b04be9b9.json` 只改了这 4 个标签。发布后的第一次每日总结，`label_changes` 里会出现这几条从 `supports`/`against` 到 `undecided` 的变化，是预期的。全套 3,238 条通过，输出 `docs/work/probes/2026-10-14-review-fixes-2-release-path.out.txt`
- updated: 2026-10-14

### B067 风险分的回执门禁和 `partial` 的含义

- state: done
- kind: defect
- why: 2026-10-14 审查核对了机制，没追影响：(1) 风险分阶段在日线 ≥4,000 根（`peer_close_research.py:62`）且榜单非空时就回执 `completed`，之后当晚不重算；读数里带 `coverage.missing_bar` 和 `ranks_reliable`，所以缺口可见，但回执关了。(2) `post_close_scheduler.py:55` 与 `post_close_strategy_service.py:233` 把策略的 `partial` 算作当日完成；`partial` 在什么情况下返回没有追到
- how: 先只读：查近 10 个交易日 `peer_limit_up_risk` 回执时的 `coverage.missing_bar`（`post_close_strategy_runs` 里 `partial` 的原因）；如果缺口里涨停股占比不可忽略，再把门禁改成补齐回执 `completed`，或覆盖率上升时允许同日重算
- who: agent 只读查询；写库的重算由用户执行
- needs: 无
- result: 2026-10-14 只读查询（`docs/work/probes/2026-10-14-risk-reading-coverage.py` → `.out.txt`，事实 F038）：最近 12 个有读数的交易日，风险分读数的 `missing_bar` 是 0 的 8 天、1 的 4 天（每天 33–100 行），日线每天 5,556–5,567 根，远高于 4,000 的门槛；没有看到缺口对结果有实质影响，所以没有改回执门禁。`partial`：策略自己只会返回 `completed` 或 `blocked`（`post_close_candidate_screen.py`），`partial` 来自上游补全阶段，算作当日完成不会让缺口漏过。若以后日线分批到达、4,000 根时缺口大，再看 B067 的做法
- updated: 2026-10-14

### B068 复盘检查点只有 2 分钟窗口，窗口外启动当天不补

- state: done
- kind: defect
- why: 2026-10-14 审查核对：`strategy_review_scheduler.py:19,56-57` 的 11:31 和 15:05 各只有 2 分钟；进程在窗口外启动或窗口内调用全失败，当天的日评、结算、周评都不补。15:05 的收盘复盘没有理由不能晚补
- how: 无回执时在 15:10–22:00 补跑一次收盘检查点（先 `completed_for_checkpoint` 查回执，保持幂等）；11:31 的盘中检查点是时点快照，不补
- who: agent
- needs: 无；发布要随下一次 owner 纯代码发布，推送与发布由用户做
- result: 2026-10-14 提交 e6363b8e（已推送到 origin/main，2026-10-14；已发布 `owner-code-20261014-09`，15:11，发布后检查通过，见 `docs/RELEASE_SYNC_47.md` 发布表）：收盘检查点 15:10–22:00 在没有回执时补跑，失败后至多每 10 分钟一次；只在带 `completed_for_checkpoint`（持久回执）时才补，避免重复结算；盘中检查点不补。4 条新用例，全套 3,238 条通过，输出 `docs/work/probes/2026-10-14-review-fixes-2-release-path.out.txt`
- updated: 2026-10-14

### B069 审查里推测的四处，要用运行证据确认

- state: done
- kind: follow-up
- why: 2026-10-14 审查给出、我没能用代码证明的：(1) `analyst_market_evaluation.py:447` 观测表用 `strategy_available_at >= %s::date`，没转 `Asia/Shanghai`，而同一函数里日内结果的查询转了；若数据库会话时区是 UTC，上海 16:00 之后的观测会划到次日；(2) `derived_daily_readings.py` 把所有读数的 `effective_at` 写成当日 15:00，次日补的读数对只看 `effective_at` 的读者像是昨日已知，要找有没有这样的读者；(3) `all_a_snapshot_sources.py:160-161` 缺状态（`None`）时视为可决策，源码说是有意的向后兼容，要查有没有调用方传 `None`；(4) `longhu_vendor_source.py:363,936` 分钟批量默认 12 并发，高于仓库的 8
- how: (1) 在 owner 库只读跑 `SHOW timezone` 与一条带跨 16:00 观测的对比；(2)(3) 全仓库查读者与调用方；(4) 看实际调用路径是不是大批量，是的话改默认或封顶 8
- who: agent
- needs: 无；发布要随下一次 owner 纯代码发布，推送与发布由用户做
- result: 2026-10-14 四处逐条结论：(1) 已改，提交 496d0339：观测表按 `AT TIME ZONE 'Asia/Shanghai'` 取日，与同函数的日内查询一致；没有探测数据库会话时区，所以不知道线上是否真的错过日，修改在会话时区是上海时等价。(2) 不改：`derived_daily_readings.newest` 只按日取读数，没有按时刻的读者，补录读数在“按日取”下就是当日读数，不构成前视；若以后有按时刻回放的读者，要同时过滤 `available_at`。(3) 不改：源码注释写明缺状态视为可决策是为兼容没有该标志的老调用方，`all_a_snapshot_rows` 总返回状态字典，`intraday_features` 另外要求 `status` 为 fresh/cached；没有找到会传 `None` 的调用方。(4) 已改，提交 496d0339：直连分钟批量的并发封顶 8（`LICENSED_READ_MAX_IN_FLIGHT`，依据 F027）；经 owner 网关的那一支原来就用 `minute_batch_workers()`（默认 4）。全套 3,239 条通过，输出 `docs/work/probes/2026-10-14-review-fixes-3-release-path.out.txt`
- updated: 2026-10-14

## Closed

### B057 龙版传媒（605577）缺的复权日线

- state: done
- kind: data-backfill
- why: 用户 2026-10-14：「同时把他的也补充上。龙版传媒（605577）缺失的复权日线」。只读查 owner 库（`docs/work/probes/2026-10-14-605577-daily-bars.*`、`-missing-dates.*`、`-factor-gap-2023.*`）：这只票的日线从 2023-08-15 起，2024、2025 年完整；缺两处。一是 2023-08-15 到 2023-11-09 有 48 根日线没有复权因子（全市场 2023 年的同一个缺口，B002），这些交易日做不出复权价。二是 2026-09-09、09-10、09-11 三天没有日线：9 月 14 日那根的昨收等于 9 月 8 日的收盘（18.67），授权接口这三天也没有分钟数据（返回 1017），是停牌，不是漏数据
- how: 只补第一处。这段区间里价格链没有断（每根的昨收等于前一根收盘，没有除权），已有因子的 13 根日线和因子表里 61 行（两个 tushare 来源）全是 1.018，所以 48 个交易日的因子都是 1.018。经服务自己的因子入口写入（`persist_tushare_rows` 的 `"adj_factor"`，来源 `longhu_qfq_derived`，每行带方法和依据）：`bash ~/codebase/n8n/state/fill605577.sh`（干跑）/ `--apply`（写 owner 库，可重复执行）。三天停牌不写任何东西
- who: agent（用户点名要补）
- needs: none
- found: 2026-10-14
- updated: 2026-10-14
- result: 2026-10-14 00:58 写入完成（主会话执行，`bash state/fill605577.sh --apply`）。输出 `docs/work/probes/2026-10-14-605577-factor-fill.apply.out.txt`：三项检查都成立（价格链无断点；日线上已有的因子只有 1.018；因子表 61 行只有 1.018），`written: 48 of 48; bars of the range still without a factor: 0`，进程内存峰值 102 MB，事务 18.1 秒。写入的是：因子表 48 行（来源 `longhu_qfq_derived`，方法 `constant_factor_over_unbroken_price_chain_v1`）、原始证据表 48 行、日线表 48 行的 `adj_factor`。写入后想再用只读探针独立读一遍，被会话的安全检查拦下（理由是“修改共享资源”），所以读回只有写入事务自己报的那一行；用户可以执行 `bash ~/codebase/n8n/state/fill605577.sh`（干跑，只读）复核，应输出 `nothing to write: every bar of the range has its factor`。没有做的：全市场 2023 年的同一个缺口（B002）；这只票 2026-08-27、2026-09-29 两根日线没有涨停价（B003 一类）；三天停牌不需要补

### B031 owner 主机 2026-10-13 收盘后不响应

- state: done
- kind: defect
- why: 2026-10-13 15:07:35 起 SSH 在握手阶段超时（TCP 端口能连上），owner 读路径 `127.0.0.1:15682/health` 超时，经 edge 中继读 owner 的路由返回 503；edge 自身正常。最后一次成功的 owner 查询是 15:02–15:03（主会话取 14 个关键分钟的全市场快照）。盘后流程 18:45 开始，包括日线补全的第一次运行（W003），主机不恢复它们都跑不了
- how: 先等它恢复并读负载、内存、最忙的进程、容器状态和是否有内存不足杀进程的记录（后台命令每 30 秒重试 SSH，一连上就读）；据此判断是主机过载、带宽被占满，还是服务卡死。查明前不对 owner 发任何批量请求。那个后台重试命令挂在旧会话上、最多再试 20 分钟；接手者可以做的只有存活检查：`nc -z -w 8 47.110.79.189 3535`（端口通不通）、`curl -s -m 20 -o /dev/null -w '%{http_code}' http://127.0.0.1:15682/health`（读路径）、`scripts/release status`（只读，但会连两台主机，主机恢复后再跑）。查库的探针和任何批量读取要等原因查明、并由接手 W003 的会话或用户放行。15:10–15:13 主会话对雷达接口重试过三次，之后已停
- who: agent 只读排查；重启服务或主机由用户决定并执行
- needs: 主机恢复响应，或用户从云控制台查看
- stream: W003
- note: 2026-10-13 15:55 仍不响应，已约 48 分钟。SSH 端口能连、握手超时；后台重试 40 次（每 30 秒）全部失败后已结束；本机到 owner 读路径的隧道（`127.0.0.1:15682`）已断开，连接被拒；edge 中继读 owner 的路由返回 `fetch failed`，edge 自身正常。从本机和 edge 两个方向都连不上，问题在 owner 主机一侧
- note2: 2026-10-13 15:56 用户：「当前那台机器在修复，你现把咱们这里的代码都完善好」。主机由用户在修；agent 这边只做本机的代码工作，不碰 owner。修好后先读原因，再做 W003、W001 的到点项和 W007 的取数
- note3: 2026-10-13 16:53 用户问主会话是否对 owner 做过高并发或打爆内存、硬盘的事。主会话当天对 owner 的全部操作（都是单连接、顺序执行、只读，没有往 owner 磁盘写任何文件）：13:01 和 13:27 各一次历史范围探针（对 410 万行日线表做一次按年汇总，其余是小查询）；13:02、13:27、13:35、14:55 共约 22 次授权接口调用；13:28 和 14:54 经 edge 读雷达 6 次；13:37 运行系统自带的当日汇总命令（它一次读全天所有通达信快照的正文，约 95 MB，10 秒超时被数据库取消）；13:38–13:40 两个通达信比对探针（只读小的比对行）；15:01 读一份快照的结构；15:02–15:03 读 14 个关键分钟的全市场快照正文（每份压缩后约 0.4 MB，容器里的 Python 进程瞬时占用估计几十到一百多 MB，输出 3.8 MB，正常结束）；主机不响应之后：15:10–15:13 经 edge 重试雷达 4 次、另两个接口各 1 次，15:14–15:38 约 43 次 SSH 连接尝试（每 30 秒一次，都在握手阶段超时），15:23 和 15:54 两次存活检查。分钟线批量取数一次都没调用。接手 agent 最后一次到达数据库是 14:17。另：本周上线的通达信并行采集每分钟往原始观测表写一份约 0.58 MB 的快照（一个交易日约 150 MB），是这次发布带来的持续新增写入。这些里最重的两次读是 13:37 和 15:02 的；后者结束到已知最早的失败（15:07:35）之间有四五分钟。是否诱因要看主机上 15:00–15:08 的内存不足记录、磁盘余量和数据库日志
- note4: 2026-10-13 16:57 correction: note3 末尾和主会话 16:55 对用户说的“主机磁盘可能被新增采集写满”说错了对象。数据库不在 47.110.79.189 上：PostgreSQL 在 owner 的 Windows 工作站（热数据在 F: 盘，冷表空间在 G: 盘），经常驻的反向 SSH 发布到这台中转服务器的 127.0.0.1:15432/15433，peer 容器再经 `db-tunnel` 边车连过去（`deploy/shared-peer/compose.yaml`，`docs/SHARED_PEER_RUNTIME.md` 的拓扑图；peer 的 compose 里没有数据库服务）。39 GB 的原始观测表和每天新增的快照都落在那台工作站的 F: 盘，热库预算是 500 GB 里的 300 GB（`docs/OWNER_DATABASE_STORAGE.md:70-80`）。47.110.79.189 上只有容器和隧道；主会话的重读消耗的是这台服务器上容器里的内存和隧道带宽，以及工作站上数据库的读。文档还写明：Windows 一侧的隧道断了，peer 服务就不可用
- note5: 2026-10-13 17:02 用户：在修的是 owner 的 Windows 工作站；代码这边先全部做完，等工作站修好就发布
- note6: 2026-10-13 17:33 reality: 17:29 检查，中转服务器已恢复（`uptime` 显示开机 29 分钟，也就是 17:00 前后重启过；内存 3,495 MB 里可用 2,226 MB；四个 peer 容器健康）；owner 读路径 `/health` 返回 200，直接读 owner 的雷达正常。用户 17:27 说“对方已经修好了”。原因没有告知，agent 也没有查到。还没恢复的一处：经 edge 中继读 owner 的路由仍返回 `fetch failed`（控制台因此看不到 owner 的数据），edge 到 owner 的那段连接要在 edge 上重连；agent 从本机没有登录 edge 的凭据（部署脚本有自己的），没有动
- note7: 2026-10-13 18:13 reality: 经 edge 中继读 owner 的路由恢复了：`curl http://127.0.0.1:18300/api/research/indicators/health` 返回当天的指标清单（第一次请求 20 秒超时，第二次在 20 秒内返回；这个接口本来就慢）。agent 没有动 edge；是它自己重连的还是有人处理的，不清楚。到此 owner 一侧的三条路径（中转服务器 SSH、owner 读路径、edge 中继）都通了；还不知道的只有下午那次不响应的原因
- note8: 2026-10-13 18:15 reality: owner 的盘后阶段回执（`docs/work/probes/2026-10-13-post-close-stages.py`，读 owner 的 `automation/runs`）显示 10 月 13 日有 26 个阶段在 16:02:07–16:03:52 运行并写了回执（多数 completed，`adjustment_factors` failed，`research_snapshot` blocked），另 5 个在 17:04:13–17:04:50。也就是说在本机连不上的那段时间里（15:07 起），中转服务器上的调度容器和它到数据库的连接在 16:02 是工作的：不通的是从外面进来的那段（SSH 和端口转发），不是整台主机停了。16:03:52 到 17:04:13 之间没有回执，与 17:00 前后的重启对得上
- note9: 2026-10-13 20:29 reality: 原因基本清楚了：对方当天把量化服务迁到了新服务器。旧主机 16:59:50 重启；18:37:15 上面的 Docker 被正常关停并屏蔽（`docker` 用户服务 inactive、masked），此后旧主机的 15682、15683 拒绝连接（`docs/work/probes/2026-10-13-old-owner-host-state.out.txt`）。用户 20:2x 转来对方的迁移说明：新地址 118.178.133.37、SSH 端口 22、用户不变、私钥不变，保留的版本是 `owner-code-20261013-04`。15:07 起那段不响应是否就是迁移操作，对方的说明里没有写。本项到此结束；本机怎么切到新服务器见 B041
- result: 2026-10-13 对方把量化服务迁到新服务器，旧主机上的量化容器已停用（note9）；后续在 B041
- updated: 2026-10-13


### B011 回看接口日历上界的修复待发布

- state: done
- kind: follow-up
- why: 大面风险分和竞价处置表的回看接口把日历里未来的交易日算进“最近 N 天”；修复 7bb29e71 已在主线，没发布。主线有本地提交没推送（数目用 `git log --oneline origin/main..HEAD | wc -l` 看；本机的 origin/main 引用停在 ac15bcde，没有 fetch 过就可能偏旧）：这个修复、发布记录、调研归档，其余是笔记；推送由用户做
- how: 随下一次 owner 纯代码发布上线；发布后 `track?days=60` 返回的天数应接近 60
- who: 用户执行发布；agent 核对
- needs: 下一次 owner 发布（预计随 W006）
- result: 2026-10-13 17:41 随 `owner-code-20261013-04` 发布；`track?days=60` 返回 59 天
- updated: 2026-10-13


### B030 LarkAgentX 还能自动化的事：目标群管理、撤回同步、加急告警等

- state: done
- kind: idea
- why: 用户 2026-10-13 在目标群机器人自动创建上线后问：「你在看看结合当前feishu的能力，我们larkagentx还可以在前端和后端做那些自动化的功能」。盘点结果见 how；除群机器人和两条只读命令外都没有试过
- how: 盘点和数据在 `docs/work/probes/2026-10-13-larkagentx-capability-survey.out.txt`。按价值排：(1) 一键建同步群（建群、只许群主发言、建机器人、建路由、出邀请链接和二维码）和目标群成员管理（成员、进退群记录、入群申请、移除），四个目标群的群主都是本账号；(2) 撤回同步和送达核对：8,225 条已转发里有 79 条源头后来撤回，webhook 目标群现在撤不了，群主可用 `im.v1.RecallGroupMessage`，前提是让 bridge 监听目标群、把机器人发出的消息和转发记录对上；(3) 加急告警：中继掉线、登录将过期、关键信号时用 `im.v1.CreateUrgent` 加急给用户自己的账号；(4) 看板粘贴邀请链接入群；(5) 把环境变量里的 4 个目标群读成看板可管理的（群名、机器人）。不建议做：往源群（老师的群）里写任何东西；语音转文字、合并转发展开、云文档提取（历史里没有语音和合并转发，文档链接 3 条）
- who: 做哪几项由用户定；agent 实现。每一项开工前先用只读或在自己的群里试一次，做法同 W009 的群机器人
- needs: 无
- result: 2026-10-13 用户决定全部推进（「可以这些全部推进，请你写好完整计划给codex-flow去执行推进。不同worktree最后merge」），转为工作线 W010
- updated: 2026-10-13

### B020 通达信这条线的现状要对账

- state: done
- kind: reconcile
- why: 11 份方案没有状态，几十个 `codex/tdx-*` 分支去留不明
- how: 读方案、目录、代码、发布记录和分支
- who: agent
- needs: 无
- result: 2026-10-13 对账完成，报告 `docs/archive/2026-10-13-tdx-reconcile.md`；要接着做的事立为 W008，分支的去留是 B029
- updated: 2026-10-13

### B028 连板竞价复盘的方案草稿

- state: dropped
- kind: follow-up
- why: `.claude/plan/board-auction-review.md` 是 2026-10-13 上午的草稿，没有审查也没有批准
- how: 无
- who: 无
- needs: 无
- reason: 被 W006 取代：个案记录、每日复盘和模式总结都并进了试验台的每日总结
- updated: 2026-10-13

### B063 竞价循环在窗口内重试没读成的 09:25 表和晚建的池

- state: done
- kind: defect
- why: 2026-10-14 Grok 只读审查，我对着代码核对属实：`run_auction_pool_capture_loop` 在 `capture_session` 返回后一律睡到次日 09:14。晚补读到 `failed`/`not_final`，或池在 09:14 之后才建成（`no_pool`），当天不再试，09:25 处置表缺失。B059 只管“09:25 之后才启动”，不管这一支
- how: 已改并提交（c94eb531）：`AuctionPoolCaptureRuntime.retry_late_table`；循环里晚补为 `failed`/`not_final`/`database_busy` 时每分钟再读一次，最多 30 次、不过 11:30；`no_pool` 在 09:30 之前每 30 秒重试。新增三条用例在 `quant-service/tests/test_auction_pool_runtime.py`
- who: agent 改并提交；推送、发布由用户做
- needs: 无；发布要随下一次 owner 纯代码发布（`scripts/release plan` 先确认，推送与发布由用户做），W002 的持有会话看到本条后并进 W002
- result: 2026-10-14 `make -C quant-service test-release-path` 3,232 条通过、2 条跳过，输出 `docs/work/probes/2026-10-14-review-fixes-release-path.out.txt`；提交 c94eb531（已推送到 origin/main，2026-10-14；已发布 `owner-code-20261014-09`，15:11，发布后检查通过，见 `docs/RELEASE_SYNC_47.md` 发布表）、未在线上验证
- updated: 2026-10-14

### B064 周复盘的回归永远凑不够样本

- state: done
- kind: defect
- why: 2026-10-14 审查核对属实：`analyst_market_review.py` 的周窗口是 7 个自然日（最多 5 个交易日），回归门槛 `MIN_REGRESSION_DAYS=8`，周回归恒为 `insufficient_history`，周评状态也恒为 `insufficient_history`
- how: 已改并提交（c94eb531）：报告仍展示自己的 7 天，回归读以报告日结尾的 28 个自然日（`REGRESSION_LOOKBACK_DAYS=27`），`summary.regression_window` 记下窗口，方法版本升到 `analyst-market-review-v2`（读取侧只回显版本，不按版本过滤）。这是我选的口径；要换成别的窗口（例如只用交易日、或 60 日）改一个常量
- who: agent 改并提交；推送、发布由用户做
- needs: 无；28 天窗口由用户确认，要换改 `REGRESSION_LOOKBACK_DAYS`；发布同 B063
- result: 2026-10-14 同 B063 的全套测试通过，输出 `docs/work/probes/2026-10-14-review-fixes-release-path.out.txt`；提交 c94eb531（已推送到 origin/main，2026-10-14；已发布 `owner-code-20261014-09`，15:11，发布后检查通过，见 `docs/RELEASE_SYNC_47.md` 发布表）。回归仍是描述性的，`live_effect: none`
- updated: 2026-10-14

### B070 竞价分档：把“真一字”和“开盘涨停后开板”分开记

- state: done
- kind: follow-up
- why: 用户 2026-10-14 在读完 Grok 的社区调研后说「竞价分档进行分离」。依据：连板网（`docs/archive/2026-10-14-grok-a-share-community-research/`，未审计，页面我打开核对过）开盘即涨停的次日，真一字全天买不到、开盘涨停但盘中开板能按涨停价成交，两者合并记账会偏乐观
- how: 09:25 的表只知道开盘，所以表里的 `bucket` 仍是 `opens_at_limit`，没有改（竞价池、战法条件都读它）。已实现的跟踪汇总 `/api/v1/market/auction-desk/track` 在日线出来之后按当日最低价拆成 `opens_at_limit:one_word`（最低价在涨停价上：全天买不到，记现金 0 且不从样本里删）和 `opens_at_limit:reopened`（最低价低于涨停价：开板后可按涨停价买，保守规则仍不假设成交），每档带 `fill_rule`；没有最低价的日线留在合并档里。`gap_bucket` 的历史统计 `BUCKET_STATISTICS['opens_at_limit']`（n=754）是合并口径，没有改
- who: agent
- needs: 无；发布要随下一次 owner 纯代码发布，推送与发布由用户做
- result: 2026-10-14 提交 e3291702（已推送到 origin/main，2026-10-14；已发布 `owner-code-20261014-09`，15:11，发布后检查通过，见 `docs/RELEASE_SYNC_47.md` 发布表）；`test_track_separates_a_board_that_stayed_one_word_from_one_that_reopened`；全套 3,240 条通过，输出 `docs/work/probes/2026-10-14-auction-bucket-split-release-path.out.txt`。还没有用真实读数看过两档各有多少只：发布后读 `/api/v1/market/auction-desk/track`
- updated: 2026-10-14

### B071 涨停次日大面风险分按“次日开盘到收盘”判定，并给两组成本

- state: done
- kind: follow-up
- why: 用户 2026-10-14 说「这两条可以做」，指 Grok 调研（`docs/archive/2026-10-14-grok-a-share-community-research/`，未审计）的建议 2。`/api/v1/market/limit-up-risk/track` 只有收盘到收盘的对比，里面含买入者拿不到的隔夜跳空
- how: `limit_up_risk_runtime.track` 多取次日开盘价，每个交易日另给 `open_to_close`（同一个高低五分位对比），顶层新增 `recent_open_to_close`（同一个 `working` 规则：高低差 ≥3 个点且 t ≥2）、`mean_open_to_close` 和 `net_of_round_trip_costs`（`ROUND_TRIP_COSTS`：万 5 佣金加千 1 滑点合计 15 个基点，千 2 滑点合计 25 个基点，是来源文章自述的口径，不是交易所费率），`judged_on` 说明按哪个判。原有的 `recent`（收盘到收盘）一字未改。控制台是否改成显示 `recent_open_to_close` 没有做，那是前端的事
- who: agent
- needs: 无；发布要随下一次 owner 纯代码发布，推送与发布由用户做
- result: 2026-10-14 提交 4fa711f3（已推送到 origin/main，2026-10-14；已发布 `owner-code-20261014-09`，15:11，发布后检查通过，见 `docs/RELEASE_SYNC_47.md` 发布表）；全套 3,248 条通过，输出 `docs/work/probes/2026-10-14-risk-oc-and-temperature-freeze-release-path.out.txt`。还没有用真实读数看过：发布后读该接口的 `recent_open_to_close`，高分档的 `big_loss_rate` 是否仍比低分档高 3 个点以上，要看实际数
- updated: 2026-10-14

### B072 情绪温度的档位规则冻结，并按事先写下的假设做前向检验

- state: done
- kind: follow-up
- why: 用户 2026-10-14 说「这两条可以做」，指 Grok 调研建议 6。我之前把它对到 B033 是错的：B033 是存储冷热分层，这里说的是情绪温度的档位（决策 0013）
- how: 见决策 0013 末尾新增的“2026-10-14 规则冻结与前向检验”。要点：分项、方向、窗口、门槛、档位边界的指纹 `RULES_SHA256` 由测试钉住；存下的读数带 `rules_sha256`；`forward_test` 只数 2026-10-15 起、盖了当前指纹的读数，判一条事先写下的假设（冰点日之后下一交易日上证开盘到收盘，高于其他日），冰点日和其他日各满 20 天才判，约一年多。读数新增 `index_open`
- who: agent
- needs: 无；发布要随下一次 owner 纯代码发布，推送与发布由用户做
- result: 2026-10-14 提交 bd029769（已推送到 origin/main，2026-10-14；已发布 `owner-code-20261014-09`，15:11，发布后检查通过，见 `docs/RELEASE_SYNC_47.md` 发布表）；全套 3,248 条通过，输出同 B071。注意：发布后的第一个晚上，最近 30 个读数会因为多了 `rules_sha256` 和 `index_open` 被重新存一次（内容变了），是预期的；`index_open` 在冻结日之前存下的读数里没有，不进前向样本
- updated: 2026-10-14

### B073 2026-10-14 上午主源缺的分钟快照，用个股分钟线事后重建并写进主序列

- state: done
- kind: data-backfill
- why: owner 主机 09:21–11:06 失去响应（F031、B058），主源全 A 分钟快照缺 09:30–10:44 和 10:46–11:05 共 95 分钟。用户 2026-10-14 在盘中说「当前盘中因为上午有很多点缺失了……缺失数据点全部取得存到数据库」，随后「我允许了这次」（同意在交易时段做一次约 5,300 次的读取，超过 5,000 次的门槛）、「需要全市场快照的补充，都去补充，然后事后统一写到owner里」「主分钟序列肯定要认longhu」
- how: 14:49–15:05 用 `state/fetch_today_minutes.py`（本机，不进仓库）经 owner 读路径取今天每只股票的分钟线（`GetStockTrendIncremental`），6 并发：沪深 5,141 只 + 补取 425 只（北交所 349 只和面板里没有的 76 只），共约 5,570 次，0 次失败，速度 5–8 次/秒，owner 没有拒绝；`state/build_minute_docs.py` 把每个缺的分钟重建成一份文档（价格、累计成交量、累计成交额、开高低收昨收；没有资金流，这与主源这一路“价量快照、无主力资金流”的口径一致）；与已存的真文档对比后（`docs/work/probes/2026-10-14-minute-reconstruction-validation.out.txt`，11:10、13:30、14:30 三分钟）：累计成交量中位误差 0.04%–0.07%，96% 的股票在 1% 以内；价格 50%–57% 在半个最小变动单位内（已存的是该分钟第 39–50 秒，重建的是分钟末）；最高价偏窄、最低价偏宽（分钟收盘价的滚动极值）。95 份写进 `a_share_minute_cross_section`，来源 `longhuvip`，`effective_at` 记在 HH:MM:59，`available_at` 是写入时间；元数据 `reconstructed: true`、`decision_eligible: false`、`fallback_for: fuyao_ths`
- who: agent；写库由会话在用户同意后执行
- needs: 无
- result: 2026-10-14 15:13 写入 95 份（`docs/work/probes/2026-10-14-minute-reconstruction-write.out.txt`，6,405 万字节）；读取方认 `longhuvip` 的代码 `2e0affeb` 随 `owner-code-20261014-10` 发布（15:15）；读取方探针：当天 246 份分钟文档，09:30–11:29 没有缺分钟（`docs/work/probes/2026-10-14-minute-series-after-backfill.out.txt`）。指标健康的 `fallback_minutes` 当天会显示 95、WARN，符合它的定义（主源失败、别的来源补）。没做：09:21–09:29（集合竞价阶段，分钟线里没有）；主分钟序列之外的流见 B074；W008 升级通达信要数满的 5 个通过日，今天不算
- updated: 2026-10-14

### B074 2026-10-14 上午主机故障期间，主分钟序列之外的流没有补

- state: open
- kind: data-backfill
- why: B073 只补了主源的全 A 分钟价量快照。审计（`docs/work/probes/2026-10-14-morning-gap-audit.out.txt`，09:21–11:05 共 105 分钟）里，下面这些流在该窗口仍只有 0–2 个分钟：通达信并行快照（1）、`market_radar`（1）、`all_a_snapshot_source_agreement`（1）、`market_sentiment_snapshot`（1）、`board_change_snapshot`（2）、热榜和飙升榜（1）、东财热度榜（1–2）、自选盯盘 `watch_scan_tape`（2）、东财涨停池七个流（0）、`realtime_quote`（0）；另有潜龙出海的扫描和研究提醒（F037：10 月 14 日只有 11:06–11:29 跑了 112 次）
- how: 分三类，先分清哪些能补：(1) 实时列表类（热榜、飙升榜、热度榜、东财涨停池、板块异动）：没有查到历史接口，从这边取不回来，只能在记录里标明那一段缺；涨停池当天的最终结果收盘后由 Longhu 榜单另存，所以日结论不受影响。(2) 派生类（`market_radar`、温度分时、情绪快照、源一致性）：输入是快照加涨停池，快照现在有了重建版，涨停池没有，要先定输入口径再决定能不能离线重算，且重算出来也要标事后重建。(3) 通达信并行快照是对主源的一致性检查，重建它没有意义，不补。潜龙出海和盯盘的扫描可以用重建的分钟离线回放，但回放不是当时的信号，只能进研究，不能进提醒
- who: agent；回放不写库的部分可以直接做，写 owner 库要用户执行
- needs: 用户决定哪几类值得做（(2) 和潜龙回放有成本，(1) 没有可做的）
- updated: 2026-10-14

### B075 10 月 13 日晚复盘包里有两条计划的金额参数是以“亿”写的，今天盘中被评估错

- state: done
- kind: defect
- why: 2026-10-14 盘后的当日模拟（`docs/archive/2026-10-14-today-simulation/README.md`）发现：`000823 超声电子`（relay_acceleration：`fail_amount=20`、`seal_amount_max=14`、`entry_amount_range=[2,12]`）和 `603096 新经典`（relay_break_reseal：`fail_amount=13`）的库内参数是 20、14、[2,12]、13，评估函数按元比较，所以失效线是 20 元和 13 元；清单文字里是“成交 2e-08亿~1.2e-07亿”。按库里的参数回放：超声电子全天 `watch`、没有入场信号（当天开盘涨停、低 24.11、收盘封板 +10.0%）；新经典 09:30、09:33 `invalid`、最终 `watch`（当天 +10.0% 收盘封板）。把参数乘 1e8 后：超声电子 09:30 `entry`（`sealed`），新经典 09:33 `entry`
- how: 根因是导入没有量级校验，B019 的校验（提交 `3d2e6a8b`，随 `owner-code-20261014-07` 在 11:42 生效）此后新导入的包会被拒绝；这两条是 10 月 13 日晚在校验之前导入的，今天（session_date 10-14）过期。没有再改代码。要注意：库里老的计划（`status=expired`）里如果还有 `amount` 参数以亿写的，只影响复盘统计，不影响今后
- who: agent
- needs: 无；明天起观察：今晚新导入的 10 月 14 日复盘包是否被校验放行，金额是否以元写
- note: 2026-10-14 参数来源核对（`docs/work/probes/` 本条的只读查询）：不止 10 月 13 日——9 月 28 日的 `000678`、`600802`（relay_fast_seal：`fail_amount` 3.69、6.0，`seal_amount_max` 1.8、4.0）和 9 月 29 日的 `603949`（relay_acceleration：`seal_amount_max` 4.0、`entry_amount_range` [3,4]）也是以亿写的。这些计划已过期，只影响当时的盘中提醒和事后复盘统计。老师计划参数的来源（T 口述/D 数据核对/I 系统默认）：龙头类 `sympathy_follow` 的 18 个数字参数里 T=0、D=2、I=16，`trend_continuation` T=0（20 个 D、29 个 I），`relay_race` 的 3 个参数里 T=2
- result: 2026-10-14 当日模拟确认，没有写库。今天上午这两条计划的盘中提醒（若有）不可信
- updated: 2026-10-14

### B076 试验台的收益拆成“隔夜”和“盘中”，并给买入类加上“一字记 0 不删”的账

- state: ready
- kind: follow-up
- why: 2026-10-14 龙头战法分析（`docs/archive/2026-10-14-leader-tactic-analysis/README.md`）：龙头持有选股的次日隔夜 +6.83% 对其余 +2.35%，盘中 −1.06% 对 −0.36%；次日开盘就在涨停价的占 48%（一字 27.0%、开板 20.9%），账本里 `buy_open` 只统计能买的那一半，结论比全部选股乐观
- how: 在每个战法的日摘要里多存隔夜和盘中两段（`held` 的 S1 开盘/今收、S1 收盘/S1 开盘），`buy_open` 类另给“一字记 0 不删”的均值；给总结接口和看板各加一列。会改日摘要的键，参考指纹文件要同步更新，历史摘要要用 `rebuild_digests` 重建
- who: agent
- needs: 无
- updated: 2026-10-14

### B077 每晚把当天龙头选股和候选的分钟线落本机

- state: ready
- kind: data-backfill
- why: 老师用开盘后几分钟的卡位确认龙头，我们的龙头是收盘后按首封时间和板数定的，两者是不是同一只只有分钟线才能比；接口只给最近一个交易日的分钟线
- how: 脚本已写好：`state/capture_leader_minutes.py [日期] [--dry-run]`（本机，不进仓库）。每晚 16:50 之后（收盘读数存下之后）读看板接口里的 hold/focus/avoid 名单，约 40 只，6 并发，写 `state/research/leader_minutes/<日期>.jsonl`。要定时：加进监督进程的任务表（`scripts/svc_supervisor.py` 的 TASKS，目前该文件有别的会话未提交的改动，没有碰）。要扩到全部涨停股，改取读数而不是看板
- who: agent 备好；定时任务由用户或确认没有冲突后加
- needs: 用户确认定时和范围
- updated: 2026-10-14

### B078 2023 年复权因子补写（B002 的写库一步）

- state: done
- kind: data-backfill
- why: 只读干跑 2026-10-14（`state/factors_plan_2023.py`，副本 `docs/archive/2026-10-14-leader-tactic-analysis/`）：2023-08 到 2023-11 缺因子的 254k 行里 253,316 行可以从昨收链推出，7 个无锚点的全是指数；已存的 238,831 个因子里 238,307 个与推算一致（99.78%，差的是供应商的小数位取整）；2023-11/12 里已存的 177,825 个因子被昨收链 100% 复现（63 个除权日全部一致）
- how: 写入脚本 `state/factors_fill_2023.py`（默认干跑，`--apply` 才写；一个交易日一个事务，走服务自己的 `adj_factor` 入口，来源 `longhu_qfq_derived`，方法 `pre_close_chain_v1`，不动已存的行）。在 owner 容器里跑：`python - --apply < factors_fill_2023.py`。约 25 万行，要看每个事务的耗时（owner 写库的已知教训：逐行语句每次约 52ms 往返），先拿一个月试
- who: 写库由用户执行或明确说“做”
- needs: 用户同意写 25 万行；写前先做 B001 的话一起推
- result: 2026-10-14 16:3x 用户执行 `bash state/fill2023.sh --apply`（第一版用服务自己的 `adj_factor` 入口，一天 5,000 行写了 20 多分钟，已停掉并确认没有半截数据；改成整批写入）：写入 253,316 行，2023-08-15 至 2023-11-09 共 51 个交易日，每个交易日写完核对因子行数和日线缺因子数，全部一致（`docs/work/probes/2026-10-14-factors-fill-2023.apply.out.txt`）。读回（`...-factors-readback-2023.out.txt`）：2023-08 到 2024-01 每月沪深日线因子覆盖都是 99.86%（与原来就有因子的 2023-12 和 2024-01 一致，余下的是停牌和新股）；新写的行：因子表 253,316、原始证据 253,316、运行记录 51 条；2023-08 至 2023-12 相邻交易日因子比与昨收链隐含比 466,087 对里 466,086 对一致（99.9998%），281 个除权日全部一致；例：000002.SZ 2023-08-25 因子 172.824 → 181.7038
- updated: 2026-10-14

### B079 结算里的涨跌停回退把主板 ST 当 10% 板（2026-07-06 之前应是 5%）

- state: open
- kind: defect
- why: `t1_settlement._limits` 在日线没有涨跌停价时调 `a_share_limit_ratio(symbol, False, 日期)`，`is_st` 固定为 False，2026-07-06 之前的主板 ST 股按 10% 板判断；2023 年约 8.6% 的日线没有涨跌停价（F003），这部分里的 ST 股封板、一字会被误判
- how: 回退里取 ST 状态（名称含 ST 或库里的 ST 标记），按当日规则算；先查回退一共影响多少行再定，涉及 B009、F034 的矛盾
- who: agent
- needs: 无
- updated: 2026-10-14

### B080 龙头类里两个条件的取舍

- state: dropped
- kind: follow-up
- why: 750 天重放里 `全市场最高板`（日均差 +0.22%，t 0.57）和 `龙头切换`（+0.11%，t 0.36）看不出优势；`梯队环境接力` 按日 t=3.20，按题材日分簇后 t=1.10。样本内
- how: 前向样本没有满 20 个选股日之前不动；满了以后按标签（现在 `q` 门已经加了）定。想提前处理就要用户定
- who: 用户定
- needs: 无
- reason: 2026-10-14 用户：「不需要下掉80」。`全市场最高板`、`龙头切换` 继续留在登记表里，不提前下掉；前向样本满 20 个选股日后由标签（`q ≤ 0.10` 门）自己说话。`梯队环境接力` 按题材日分簇后 t=1.10 的发现保留在分析报告里
- updated: 2026-10-14

### B081 跟随票的计划里报告“龙头一字买不到”（只报告，不改动作）

- state: done
- kind: follow-up
- why: 2026-10-14 当日模拟里 6 条计划依附 `000530`，它全天一字（开高低收都是 6.3）买不到；`sympathy_follow` 的“龙头强”把封板当成前提，看不出它是替代买点
- how: `teacher_review_rules.py`：龙头的 `open`、`low`、`limit_up_price` 写进 tape 的最新摘要；`sympathy_follow` 的清单多一行“龙头一字（至今全天买不到，跟随票是替代买点）”，不参与门控，动作不变。理由：老师的跟随打法本来就是龙头买不到时买跟风，是否跟随是老师的判断，不是这条规则的；结果统计时可以按这一行分账
- who: agent
- needs: 无
- result: 2026-10-14 提交 420273d0；新用例 `test_follower_report_names_a_leader_that_cannot_be_bought_without_gating_on_it`（一字与非一字两种龙头，动作都是 entry）；全套 3,249 条通过，输出 `docs/work/probes/2026-10-14-follower-leader-line-release-path.out.txt`；已推送，2026-10-14 16:29 随 `owner-code-20261014-11` 发布（发布表），发布后状态检查通过
- updated: 2026-10-14

### B082 补完 2023 年复权因子之后，重放试验台 2023–2024 的收盘和竞价读数

- state: done
- kind: follow-up
- why: B078 写入后，2023-08 到 2023-11 的日线有了复权因子，但试验台里 2023–2024 年已存的读数是用未复权价算的（`history_raw_basis_share` 2023 年 0.99、2024 年 0.94，W006 Log 11:56）；读数不会自己刷新
- how: 回放命令同上次（`state/replay14.sh --apply`，先收盘后竞价，没变的文档不重写；同一个写库路径，上次全量约 35 分钟、峰值 344 MB）；只重放 2023-09-01 到 2024-12-31 即可。重放后跑一遍 `rebuild_digests`（日摘要）
- who: 用户执行写库
- needs: 无
- result: 2026-10-14 用户 16:30–16:42 执行 `bash state/replay2023.sh --apply`（`docs/work/probes/2026-10-14-tactic-replay-2023-2024.apply.out.txt`）：收盘读数 322 天、19,826 行，竞价读数 321 天、19,760 行（2023 年跳过 1 天，第一个交易日没有前一日收盘读数，与上次相同）；封板判定与涨停榜的不一致率两年都是 0；**`history_raw_basis_share` 2023 年 0.99→0.0、2024 年 0.94→0.0，`raw_basis_share` 都是 0.0**；峰值内存 333 MB 和 218 MB。随后把龙头战法的提取与分析重跑（`docs/archive/2026-10-14-leader-tactic-analysis/analysis_after_factors.out.txt`）：各项与重放前相差都在 0.02 个点以内，结论不变
- updated: 2026-10-14

### B083 新主机的 7.0：先量一个完整交易日

- state: ready
- kind: follow-up
- why: `docs/OWNER_HOST_SWITCH.md` 第 7 步的前提：新主机（4 核 7.3 GB、200 Mbps）跑满一个交易日后再一项一项放开（B062）。现主机清点：内存用了 1.0 GB、盘用 6.8 GB（69 GB）
- how: 2026-10-15 盘中每 10 分钟一次 `docker stats --no-stream`（主服务、调度、两个隧道），存进 `docs/work/probes/2026-10-15-owner-load.out.txt`；db-tunnel 与网卡流量；两条分钟采集每轮耗时；晚间各阶段回执时间。对照 `docs/work/probes/2026-10-14-owner-load-1306.out.txt`（旧主机，2 核 3.5 GB：负载 0.82、主服务 CPU 44%、内存 325 MiB），写进 FACTS
- who: agent（只读）
- needs: 10 月 15 日交易日
- updated: 2026-10-14

### B084 edge47 的 feishu-relay 和 itougu 迁到新 owner 主机

- state: waiting-user
- kind: follow-up
- why: 用户 2026-10-14 17:5x 要求；清点和阻塞（要 root、Hub 不可达、rootless 网络、同机共用的规矩）记在 W015
- how: 见 `docs/work/streams/W015-edge47-relay-to-owner-host.md`
- who: agent，切换步骤由用户确认
- needs: 用户的三项决定（W015 Waiting on the user）
- updated: 2026-10-14

### B085 两个发布单元同机之后：host 注册表、发布工具的默认主机和隔离测试

- state: open
- kind: follow-up
- why: edge47 的 feishu-relay、xhs、itougu 搬到 owner 主机后，`config/components.json` 的 `47edge` 与 `47owner` 如何表示、`OWNER_MARKERS`（只覆盖主 relay 热发布脚本和 owner code-only 脚本）要扩到 workflow、xhs、itougu、failover、status 脚本；`release-sync-status.sh:57`、`release_plan.py:39`、`hotfix-feishu-relay-edge.sh:41`、`hotfix-xhs-intel-edge.sh:40` 等默认还是 `root@47.114.113.152`，改之前发布会作用在旧主机上（来源：Codex 审计第 16、17 条，W015）
- how: 先写决策记录，再改默认和测试；`failover-feishu-relay-to-local.sh` 与 `failback-...` 的表清单补上 `baidu_pan_archive_jobs`、`paper_kb_deliveries` 等
- who: agent
- needs: W015 切换完成并验证
- updated: 2026-10-14

