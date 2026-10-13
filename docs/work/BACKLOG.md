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

- state: ready
- kind: data-backfill
- why: 2023 年只有 48.5% 的日线行有复权因子（F002）；结算窗口里任一天缺因子就退回未复权价格，2023 年下半年遇到除权的股票会算偏
- how: 用日线自带的昨收推算（当日昨收 ÷ 前一交易日收盘，逐日累乘），经 `"adj_factor"` 入口写入，来源标为推算；写前在已有因子的行上核对一致率，写后核对除权日前后复权序列连续
- who: agent 备脚本；写库由用户执行
- needs: 无。如果决定做 B001，就等 B001 写完再一起推算，免得算两遍
- updated: 2026-10-13

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
- updated: 2026-10-13

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
- updated: 2026-10-13


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
- updated: 2026-10-13

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

- state: open
- kind: defect
- why: 另一个会话的改动（s2-gateway-tunnel）引起，10 月 12 日起每次跑 `pytest scripts` 都有这 1 个失败；不是本线工作造成的，但会让人误判
- how: 属于那个会话的工作；这里只提醒：`pytest scripts` 的基线是 1 failed
- who: 改动的所有者
- needs: 无
- updated: 2026-10-13

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

- state: open
- kind: defect
- why: 提交前要过的脚本测试（`python3 -m pytest scripts -q`）因此有 1 项失败，后面真正的失败容易被当成“已知的那一项”放过。失败信息：`supervisor task s2-gateway-tunnel is not classified in hosts[workstation].supervisor_tasks; say whether it writes to the owner, reads it, talks to the edge or stays local`。来自提交 9c70389d（2026-10-12，另一条工作线给 supervisor 加了 S2 网关隧道），在已发布的 26170231 的干净检出里同样失败，与 W006、W008 的改动无关
- how: 在主机登记表里给 `s2-gateway-tunnel` 填上归属（写 owner、读 owner、连 edge 还是只在本机），由加这个任务的那条工作线来填：它连到哪里只有那边清楚。验收：`/Users/papa/.venvs/svc/bin/python -m pytest scripts/test_runtime_hosts.py -q` 全过
- who: 加这个任务的会话（主检出里 `scripts/svc_supervisor.py` 还有那边未提交的修改，本会话没有动）
- needs: none
- found: 2026-10-13，W008 派工 4 的实现者跑脚本测试时报出，主会话在主检出和发布检出 `/Users/papa/codebase/.codex-wt/n8n-release-26170231` 各复现一次（1 failed, 8 passed）
- updated: 2026-10-13

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

- state: waiting-user
- kind: follow-up
- why: 2026-10-13 对方把量化服务从 47.110.79.189（SSH 3535）迁到 118.178.133.37（SSH 22，用户 stockpeer，私钥不变；对方说明里给的主机指纹是 ED25519 `SHA256:JSgjaKCz873b9Na6b+DF6DBv1Ni+cToO0gY9O9bhZA8`）。旧主机上的量化容器 18:37:15 起停用，不要恢复、不要解除 Docker 的屏蔽。本机的 owner 读路径（`127.0.0.1:15682`）现在连的还是旧主机，所以不通：一切要读 owner 的事都停着（W003/W001 当晚盘后的核对、W007 的续取、W008 明早 09:16 的核对、任何发布）。以下都是对方说明里的话，agent 还没有连上新主机核实过：保留的版本 `owner-code-20261013-04`（26170231）；数据库仍在对方的工作站，经反向 SSH；新主机只有 1 GB 内存加 2 GB 交换区，不能在上面构建镜像，也不能再起一套生产写入服务；edge 的配置不随这次迁移改
- how: 按对方说明的顺序。(1) 第一次连接新主机并核对主机指纹；(2) 改 `config/secrets/.env.local`：`LONGHU_SSH_HOST`、`LONGHU_SSH_PORT`（22），以及 `OWNER_PEER_SSH_HOST`（新主机内网 172.19.35.9）、`OWNER_PEER_SSH_PORT`（22）、`OWNER_PEER_SSH_USER`、`OWNER_PEER_SSH_KEY_PATH`、`OWNER_PEER_KNOWN_HOSTS_PATH`、`OWNER_PEER_SSH_HOST_KEY_ALIAS`（空）；只改本地，**不要跑 `sync-secrets.sh owner`**，下次同步前先核对生成的 `.env.owner` 与新主机现有配置的差别；(3) 重启本机服务管理器让 `owner-tunnel` 换目标（它的参数在管理器启动时就定了，只杀隧道进程不够：`launchctl kickstart -k gui/$(id -u)/com.papa.svc-supervisor`，本机所有后台任务会跟着重启几秒）；(4) 只读验收：新主机上 15682、15683 的 `/health`，本机隧道的 `/health` 里版本应是 `owner-code-20261013-04`，`scripts/release-sync-status.sh`（要带新的 `OWNER_PEER_HOST`、`OWNER_PEER_PORT`），再读当晚盘后各阶段的回执（`docs/work/probes/2026-10-13-post-close-stages.py`）；(5) 把仓库里仍指向旧地址的默认值改掉并过脚本测试，只改量化 owner 的目标：`scripts/release-sync-status.sh:61-62`、`scripts/release_plan.py:27`、`scripts/shared-peer/deploy-code-only.sh:62-63`、`scripts/shared-peer/deploy-full-release.sh:69-70`、`scripts/sync-xiaojie-message-features.sh:17-18`、`scripts/shared-peer/configure-peer-self-tunnel.sh:10`、`config/secrets/env-split.py:68`、`config/secrets/sync-secrets.sh:20`，测试 `scripts/test_release_unit_isolation.py:33`（owner 标记里加新地址）和 `scripts/release-sync-status.test.mjs:42`（现在靠端口 3535 区分 owner 和 edge，端口都成 22 后要换区分办法）；`scripts/teacher_review_daily.py:43-44` 有别的会话未提交的修改，留给那边；`deploy/shared-peer/compose.yaml:14,56` 的默认端口不要顺手改（改 compose 会让下一次发布变成全量）；文档里的旧地址（`AGENTS.md`、上一级的 `AGENTS.md`、`OPERATIONS.md`、`docs/RELEASE_SYNC_47.md`、`docs/SHARED_PEER_RUNTIME.md` 等，`git grep -n 47.110.79.189` 能列全）改说明、不改历史记录行
- who: 第 (1)–(3) 步要用户做或明确放行：agent 2026-10-13 20:24 试着扫新主机的主机密钥并比对指纹，被自动模式的安全检查拦下（理由是“探查凭据”），随后读 `env-split.py` 的一条命令也被拦（理由是“改变流量去向”）；agent 没有绕过，`.env.local`、`known_hosts`、服务管理器都没有动。第 (4)(5) 步在连通之后由 agent 做
- needs: 用户的决定
- found: 2026-10-13，用户转来对方的迁移说明（原话：「当前对方owner服务器已更改为这个，然后你可以使用项目里有保存过的env就是他的ssh key。」）
- note1: 2026-10-13 20:34 用户说改成手动模式由他来同意。第 (1) 步做完：扫到的主机指纹与对方说明一致，已写进 `~/.ssh/known_hosts`；用保存的私钥登录成功，只读检查见 FACTS F022。第 (2) 步（改 `.env.local`）的命令仍被自动模式的安全检查拦下（理由同前，“改变流量去向”），说明会话当时还在自动模式；没有绕过，等用户切换模式后重试，或用户自己改
- note2: 2026-10-13 21:10 用户切到手动模式后的进度。第 (2) 步完成：`.env.local` 改了 4 个键、加了 4 个键，备份在同目录 `.env.local.bak-20261013`，没有同步到远端。第 (3) 步完成：20:38 重启本机服务管理器，`owner-tunnel` 连到新服务器。第 (4) 步：本机 `127.0.0.1:15682/health` 返回 200，版本 `owner-code-20261013-04`、提交 26170231，调度容器同版本；当晚的情况见 B042、B043 和 FACTS F023、F024。还差两件：(a) edge 主机上的 `quant-longhu-owner-tunnel` 服务仍指向旧服务器（3535），所以经 edge 读 owner 的路由返回 `fetch failed`、控制台看不到 owner 的数据；已验证 edge 用的那把密钥在新服务器上已授权（`docs/work/probes/2026-10-13-edge-owner-tunnel.out.txt`）；改这个服务的命令被会话的安全检查拒了两次（先是命令，后是装着命令的脚本），命令给了用户，由用户在 edge 上执行：备份服务文件到 `/root/quant-longhu-owner-tunnel.service.bak-20261013`；`ssh-keyscan -t ed25519 -p 22 118.178.133.37` 的结果追加到 `/etc/quant-intraday-edge/known_hosts` 并核对指纹；`sed -i 's/ -p 3535 / -p 22 /; s/stockpeer@47\.110\.79\.189/stockpeer@118.178.133.37/'` 改服务文件；`systemctl daemon-reload && systemctl restart quant-longhu-owner-tunnel`；`curl http://127.0.0.1:15682/health` 应为 200。(b) 第 (5) 步仓库里的旧默认地址还没改
- note3: 2026-10-13 21:14 第 (5) 步做了：发布工具、两个凭据脚本和消息特征同步脚本的默认地址改到新服务器（提交 4bb8c7ff；`pytest scripts -q` 361 项通过加已知的 B035 一项失败，两个 node 测试通过）；用新默认值跑 `scripts/release-sync-status.sh --owner-source-sha 26170231… --edge-source-sha 764ca2b2…`：ALL CHECKS PASSED（`docs/work/probes/2026-10-13-release-status-newhost.out.txt`；edge 中继当晚 21:10 被另一条工作线发布到了 `hotfix-20261013T131021Z-764ca2b22292-2811`）；`scripts/release plan` 能读到新主机。`AGENTS.md`（本仓库和上一级目录）与 `docs/work/README.md` 里的地址已改。还没做的：(a) edge 主机上的隧道服务，等用户执行（note2）；(b) `scripts/teacher_review_daily.py:43-44` 的默认地址还是旧的，这个文件有别的会话未提交的修改，本会话没动；本机每 20 分钟一次的 `teacher.cycle` 如果靠这个默认值连 owner，现在是连不上的；(c) `OPERATIONS.md`、`docs/RELEASE_SYNC_47.md`、`docs/SHARED_PEER_RUNTIME.md`、`docs/OWNER_READ_API_FOR_HARNESS.md`、`docs/SHARED_STOCK_DATA_API.md`、`docs/TEACHER_REVIEW_DAILY.md`、`docs/DATA_SOURCE_LAYER.md`、`docs/COMPONENT_CONTRACTS.md`、`feishu-relay/AGENTS.md` 里的旧地址（改说明、不改历史记录行）；(d) 下一次 `sync-secrets.sh owner` 之前，先把 `env-split.py` 生成的 `.env.owner` 和新主机上现有的配置按键名对一遍，保留对方的生产角色和写入开关；在那之前不要同步；(e) `deploy/shared-peer/compose.yaml` 里隧道端口的默认值仍是 3535，靠主机上的环境变量覆盖，没有改（改 compose 会让下一次发布变成全量）
- note4: 2026-10-13 21:16 用户说需要对方在他的服务器上做的事要写成提示词交给他转发。提示词写好了：`docs/work/probes/2026-10-13-prompt-to-owner.md`（回报五项；请对方确认或处理六件：我们后加的盘后阶段由谁跑、每日指标 5,138/5,560、新机内存规格与重任务放哪里、下次凭据同步前要保留的键名、迁移后是否暂停发布、启动时那阵连接失败供知晓）。对方回复后把答复记到 B042、B043、B044 和本项
- note5: 2026-10-13 21:24 用户转述：对方让我们自己在他的服务器上执行（「当前对方让我们都来执行，因为我们可以ssh」），会话改为手动逐条同意。随后做了：edge 主机的 `quant-longhu-owner-tunnel` 改到新服务器（备份在 edge 的 `/root/quant-longhu-owner-tunnel.service.bak-20261013`，核对过的主机密钥加进了它的 known_hosts，服务 active，edge 上 `127.0.0.1:15682/health` 返回 200，从本机经 edge 中继读 `limit-up-risk/daily` 返回 200）。到此连接都切完了。剩 note3 里的 (b)(c)(d)(e)；给对方的提示词因此多半不用发了，里面的问题改由我们自己只读查（每日指标缺口见 B043，凭据键名的核对见 note3 的 (d)）
- updated: 2026-10-13

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
- updated: 2026-10-13

### B044 新的 owner 主机内存只有 894 MB：在上面跑的东西都要先量内存

- state: open
- kind: follow-up
- why: 2026-10-13 20:33 量得新主机内存共 894 MB、可用 267 MB，交换区 2,047 MB 已用 410 MB（FACTS F022）；调度进程自己占 166 MB。旧主机是 3,495 MB。受影响的：在容器里跑的只读探针（读文档正文的那几个，例如通达信容差探针一次读上百兆）；会加载整个服务的命令行工具（`market_history_backfill`、历史回放）；W006 每晚的总结和历史回放；全量发布（不能在这台机器上构建镜像）；分钟面板导出
- how: 探针只查计数和时间，要正文的改成在数据库里聚合；W006 先在本机量 `summarise` 和回放的内存峰值再定存储设计（已派，见 W006）；需要大内存的一次性任务考虑在别处跑、只把结果写回；把结论写进 `docs/work/README.md` 的注意事项
- who: agent
- needs: none
- found: 2026-10-13
- updated: 2026-10-13

### B045 空的涨停榜被存成两行并计为“格式不对”

- state: open
- kind: defect
- why: 供应商对空榜返回的 `info` 是“一个空列表加一个字符串”，`market_history_backfill._info_rows` 把它当成行列表，于是存下 2 个不是行的元素，解码器再把它们计进 `coverage.malformed`。2026-10-13 四板、五板以上两张榜是空的，读数因此显示 `malformed: 4`，看上去像丢了 4 只股票（实际没有）；10 月 9 日、12 日各有一张空榜，各记 2。不影响读数里的股票，但覆盖统计会误导人
- how: `_info_rows` 认出“第一个元素是列表”的形状时只取第一个元素；加一条用这个真实形状的测试；已存的文档不用改（解码器照样跳过）
- who: agent
- needs: none
- found: 2026-10-13（`docs/work/probes/2026-10-13-high-board-rows.py` 与 `.out.txt`）
- updated: 2026-10-13

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

### B052 会员期限：指定到期日；读个人资料里的备注信息

- state: ready
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

## Closed

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
