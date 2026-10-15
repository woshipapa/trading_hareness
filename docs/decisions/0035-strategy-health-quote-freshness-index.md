# 0035 策略健康读路径增加行情新鲜度时间索引

- 状态：生效
- 日期：2026-10-16

## 背景

策略健康接口已经把信号、结果和行情新鲜度收敛到一条只读快照 SQL，但其中的
`latest_quote_at` 和 90 秒内 `fresh_quote_rows` 仍对全部
`quant.intraday_quote_observations` 做全局时间聚合。现有 source-leading 状态索引
适合按来源检查，不能让这两个全局时间条件直接从索引头部完成。

## 决定

新增迁移 `20261016_ow0126`，建立
`quant.intraday_quote_observed_at_idx(observed_at DESC)`。它只改变查询访问路径，
不改变策略健康字段、市场时段判断、研究-only/live-effect 边界或任何 provider 调用。
迁移前置于 `20261016_ow0125`，owner 必须按 schema gate 应用到位后再发布依赖它的代码。

## 证据和边界

在本地 compose PostgreSQL 的同一份数据集上，策略健康快照保持一个 SQL statement；
未使用该时间索引时热身后的中位耗时约 37 ms，建立同名索引后 5 次采样中位约
3.7 ms。首个样本包含连接和缓存热身，探针只测本地数据库，不代表 owner 隧道或交易日
高峰延迟。owner 上线后应复测执行计划、索引命中、锁等待和写入吞吐，若写入成本超过
读侧收益则回滚该迁移。

## 怎样保证

- `test_quote_freshness_index_migration.py` 验证迁移链、索引名和可逆 downgrade。
- `docs/work/probes/2026-10-16-strategy-health-read.py` 只读测量 statement count 和延迟，
  输出不替代 owner 运行证据。
- 索引只服务控制面健康读；它不会把 provider 数据直接提升为阈值、策略或订单路径。
