# 0012 分离估值归档与投影重试

- 状态：实现待合并发布；自动投影默认关闭。
- 日期：2026-10-09

`fuyao_valuation_index` 只抓取并归档估值。成功后标记该任务完成，投影 pending
不会使归档任务继续处于 pending。

新增 `daily_valuation_projection` 任务，在 18:30–23:30 窗口内读取已落库证据进行投影。
它不调用 Fuyao provider。pending 只重试投影任务，保留原有十分钟重试间隔。
缺少投影 callback 时跳过此任务，沿用 `DAILY_VALUATION_PROJECTION_ENABLED` 默认关闭。

相关测试覆盖归档一次 / 投影两次、投影独立运行时 provider 不可调用、关闭时跳过、
完成状态及 pending 状态。进程重启后的归档任务沿用既有 ArchiveState 生命周期；
本改动不承诺整个归档系统跨重启零请求。

本次 10-09 的 `project-valuations --apply` 由维护方在发布后通知并执行，
Windows 不执行同一天的 apply。长期启用自动投影仍须确认 0010 的单写入方合同及调度所有权。
