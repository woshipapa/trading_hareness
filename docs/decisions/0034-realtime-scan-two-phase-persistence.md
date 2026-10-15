# 0034 实时扫描两阶段持久化

- 状态：生效
- 日期：2026-10-16
- 范围：owner quant 实时观察池扫描的 signal persistence

## 背景

实时扫描在拿到行情后需要运行纯规则。旧路径把输入读取、组合快照、纯规则计算、事件去重和信号写入放在同一个数据库事务里。规则计算期间连接一直被占用，慢的数据库隧道因此会把 CPU 计算时间放大为连接等待；同时，扫描每 30 秒或更快运行，长事务会挤压其他研究读写。

## 决定

生产扫描使用有界 prepare/evaluate/commit 三阶段协议，由同一个同步数据库执行器调用：

1. prepare 事务写入扫描 receipt、组合快照、provider health，并读取带 `observed_at` 截止的本地输入；事务提交前把 receipt 标记为 `partial`。
2. 事务关闭后，在进程内对已冻结对象执行纯规则和特征计算。该阶段不访问 provider、不读取数据库，也不占用数据库连接。
3. commit 事务重新读取 signal-key 事件状态，批量写入报价证据、规则快照、拒绝原因和信号事件，成功后把 receipt 更新为 `completed`。

第二阶段任意失败都会通过独立恢复事务把 receipt 标记为 `failed`，然后向原调用方重新抛出异常。这样 foreign key 在 commit 前已经可见，读取端能区分 partial/failed 和 completed；事件去重仍以 commit 时的最新状态为准。没有完整批量证据依赖图的 replay/test adapter 继续走原单事务兼容路径。

## 取舍

扫描 receipt、组合快照和 signal events 不再共享一个回滚边界；因此 partial/failed 是明确的可恢复状态，不能把任何一个 partial receipt 当成有效信号扫描。单 owner monitor 的单写调度保持不变，手工并发请求仍需通过已有运行边界控制。内存会短暂持有一批冻结输入，观察池上限仍是既有业务容量上限。

## 验证

新增回归验证纯评估发生在两个数据库事务之间、成功状态转移为 partial→completed、提交异常转移为 partial→failed。该变更不需要 schema migration；owner 发布后仍需按同一交易日复测 transaction audit、partial/failed 计数、锁等待和 `scan_performance`。
