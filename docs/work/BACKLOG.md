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

### B011 回看接口日历上界的修复待发布

- state: ready
- kind: follow-up
- why: 大面风险分和竞价处置表的回看接口把日历里未来的交易日算进“最近 N 天”；修复 7bb29e71 已在主线，没发布
- how: 随下一次 owner 纯代码发布上线；发布后 `track?days=60` 返回的天数应接近 60
- who: 用户执行发布；agent 核对
- needs: 下一次 owner 发布（预计随 W006）
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


### B030 LarkAgentX 还能自动化的事：目标群管理、撤回同步、加急告警等

- state: open
- kind: idea
- why: 用户 2026-10-13 在目标群机器人自动创建上线后问：「你在看看结合当前feishu的能力，我们larkagentx还可以在前端和后端做那些自动化的功能」。盘点结果见 how；除群机器人和两条只读命令外都没有试过
- how: 盘点和数据在 `docs/work/probes/2026-10-13-larkagentx-capability-survey.out.txt`。按价值排：(1) 一键建同步群（建群、只许群主发言、建机器人、建路由、出邀请链接和二维码）和目标群成员管理（成员、进退群记录、入群申请、移除），四个目标群的群主都是本账号；(2) 撤回同步和送达核对：8,225 条已转发里有 79 条源头后来撤回，webhook 目标群现在撤不了，群主可用 `im.v1.RecallGroupMessage`，前提是让 bridge 监听目标群、把机器人发出的消息和转发记录对上；(3) 加急告警：中继掉线、登录将过期、关键信号时用 `im.v1.CreateUrgent` 加急给用户自己的账号；(4) 看板粘贴邀请链接入群；(5) 把环境变量里的 4 个目标群读成看板可管理的（群名、机器人）。不建议做：往源群（老师的群）里写任何东西；语音转文字、合并转发展开、云文档提取（历史里没有语音和合并转发，文档链接 3 条）
- who: 做哪几项由用户定；agent 实现。每一项开工前先用只读或在自己的群里试一次，做法同 W009 的群机器人
- needs: 用户选定范围
- updated: 2026-10-13

### B029 22 个通达信探索分支的归档

- state: open
- kind: reconcile
- why: 41 个 `codex/tdx-*` 分支里 22 个没有合并；它们是 10 月 9–10 日的探索分支，模块和报告已收进主线，方案写明不整支合并；留着会让人误以为还有东西没合
- how: 逐个用 `finishworktree.sh <仓库> <名字> --archive --apply`（打归档标签并打包后删除工作区和分支）；`claude/tdx-p0-followup`（1 个提交，P0 评审修复）先看是否已被后来的提交覆盖
- who: 去留由用户定；agent 执行
- needs: 用户同意
- updated: 2026-10-13

### B031 owner 主机 2026-10-13 收盘后不响应

- state: doing
- kind: defect
- why: 2026-10-13 15:07:35 起 SSH 在握手阶段超时（TCP 端口能连上），owner 读路径 `127.0.0.1:15682/health` 超时，经 edge 中继读 owner 的路由返回 503；edge 自身正常。最后一次成功的 owner 查询是 15:02–15:03（主会话取 14 个关键分钟的全市场快照）。盘后流程 18:45 开始，包括日线补全的第一次运行（W003），主机不恢复它们都跑不了
- how: 先等它恢复并读负载、内存、最忙的进程、容器状态和是否有内存不足杀进程的记录（后台命令每 30 秒重试 SSH，一连上就读）；据此判断是主机过载、带宽被占满，还是服务卡死。查明前不对 owner 发任何批量请求
- who: agent 只读排查；重启服务或主机由用户决定并执行
- needs: 主机恢复响应，或用户从云控制台查看
- stream: W003
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
- how: 查调度起点；通达信的并行快照 09:15:20 就有第一份（F011），W008 的补缺做完后这一分钟可以由它补
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

## Closed

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
