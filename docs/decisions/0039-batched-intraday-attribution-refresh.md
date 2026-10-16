# 0039 盘后 intraday attribution 批量回写

- 状态：本地完成，待发布
- 日期：2026-10-16
- 范围：owner quant 的 intraday outcome recompute

## 背景

盘后结算会先为可结算的 signal event 重建派生 attribution。旧实现一次读取全部可结算事件，然后每个 attribution 变化都执行一次 `UPDATE quant.intraday_signal_events`。在 owner 反向隧道上，这会把纯 Python 分类和每行网络往返叠加到同一个长事务里。

## 决定

把 attribution 刷新移到独立的 repository adapter：

1. 保留原有 `state`、`signal_type` 和 `observed_at<=cutoff` 过滤条件。
2. 使用 psycopg `fetchmany` 按 1,000 行读取；每批只保留发生变化的派生证据。
3. 使用 `executemany` 批量更新，旧的轻量连接替身仍逐行回退。
4. attribution 计算、JSON 安全转换、冲突和事务边界保持不变；调用方仍在 outcome settlement 的同一写事务里完成刷新。

## 结果与边界

长事务的更新语句数从“每个变化事件一条”收敛为 `ceil(changed/1000)` 个批次，读取和内存占用也有界。该优化只改变数据库传输方式，不改变任何策略阈值、`live_effect`、点时截止或研究证据字段语义。owner 发布后仍需按同一交易日核对 statement count、事务时长、锁等待、RSS 和隧道速率。

## 验证

`tests.test_intraday_attribution_repository` 覆盖跨批次读取、只更新变化行和批量写入；与平台边界、outcome runtime、settlement clock 合计 84 项 overlay 测试通过。尚未发布 owner。
