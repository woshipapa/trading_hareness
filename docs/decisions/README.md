# 架构决定记录（ADR）

每份记录一个已经做出的架构决定：当时的背景、决定本身、带来的后果，以及由什么
机制保证它不被悄悄违反。决定改变时不改旧记录，而是新写一份并在旧记录的状态里
写"被 NNNN 取代"。

| 编号 | 决定 | 状态 |
| --- | --- | --- |
| [0001](0001-data-ownership.md) | 每张表只有一个所有者组件，跨组件读写必须登记 | 生效 |
| [0002](0002-forward-only-owner-migrations.md) | 迁移只向前，由 owner 在 Windows 上执行，发布前核对库版本 | 生效 |
| [0003](0003-owner-full-release.md) | owner 全量发布：一条命令、守护锁、按序启动、失败即回滚 | 生效 |
| [0004](0004-scoped-write-keys.md) | 写密钥按调用方分开并限定路径 | 生效（调用方迁移中） |
| [0005](0005-retire-tushare.md) | 停用 Tushare，`datasources/` 是唯一的数据源层 | 生效（代码删除中） |
| [0006](0006-research-fail-closed.md) | 研究与老师策略默认不产生实盘效果 | 生效 |
| [0007](0007-host-registry.md) | 每个后台任务都登记在某台主机上，退役任务默认不启动 | 生效 |
| [0008](0008-main-composition-root.md) | 拆分 main.py：运行时上下文与按领域的装配模块 | 生效（进行中） |
| [0009](0009-minute-cross-section-storage.md) | 全 A 分钟截面一分钟存一行，最新截面走内存 | 生效（默认双写，待切换） |
| [0010](0010-post-close-data-readiness-contract.md) | 盘后数据分别就绪、真实可用时间与公共阶段接管 | 草案，待双方确认 |
| [0013](0013-sentiment-temperature-and-flows.md) | 短线情绪温度（冰点 / 沸点）与资金流向配合 | 日频已实现；分时、金/银指、看板待实现 |
| [0014](0014-feishu-relay-release-independent-of-owner-windows.md) | feishu-relay 的发布不受 owner 发布时间窗口限制 | 生效 |
| [0015](0015-tdx-may-be-decision-evidence.md) | 通达信数据可以作为决策证据，并接入生产采集 | 生效 |
| [0016](0016-limit-up-risk-score.md) | 涨停股次日大面风险分：只作研究展示 | 生效 |
| [0017](0017-auction-open-desk.md) | 竞价采集与 09:25 处置表：只作研究展示 | 生效 |
| [0018](0018-daily-close-completion.md) | 盘后日线补齐与逐股完成判定 | 生效 |
| [0019](0019-feishu-relay-dashboard-registrations.md) | 飞书转发：看板登记的来源标签和目标群 webhook 存在中继自己的库里 | 生效（第 6 条被 0020 取代） |
| [0020](0020-feishu-relay-creates-target-bots.md) | 飞书转发：用账号自己的会话在目标群里创建自定义机器人 | 生效 |
| [0021](0021-peer-evening-stages.md) | 我们这一侧每晚要的产出，由我们自己的晚间循环启动（回执只在产出存在时关闭） | 生效 |
| [0022](0022-feishu-relay-uses-existing-group-bots.md) | 飞书转发：可以借用群里已有的自定义机器人发消息，但不改它、不删它 | 生效 |
| [0023](0023-tactic-trial-ledger.md) | 战法试验台：战法和条件是证据观察，不是策略；每晚的总结按天折叠，每个时点各有自己的读数 | 生效 |
| [0024](0024-owner-paths-are-classified-not-built.md) | owner 的改动按路径分类；不认识的路径是缺一条规则，不是去构建镜像；迁移随覆盖层发布并每次过库版本闸门 | 生效 |

格式：

```markdown
# NNNN 标题

- 状态：生效 | 被 NNNN 取代 | 已撤销
- 日期：YYYY-MM-DD

## 背景
## 决定
## 后果
## 怎样保证
```
