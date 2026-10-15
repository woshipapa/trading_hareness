# 0032 实时扫描证据与事件状态批量化

- 状态：生效
- 日期：2026-10-16

## 背景

实时观察池扩大后，采集和规则计算已经在内存中完成，但扫描事务仍会按标的重复读取事件状态，并逐条写入报价证据、规则快照和拒绝原因。这些操作跨 owner 数据库隧道，往返次数会把 CPU、内存和网络优势消耗在等待上。

## 决定

一次扫描先冻结并计算整个观察池的纯规则输入，再用三次集合查询读取全部事件状态：最新事件、当日最近告警和标的级最近 watch 告警。随后仍按标的顺序写入事件，以保留同一扫描内重复 signal key 的状态机语义。

报价证据、规则输入快照和扫描拒绝原因使用批量 cursor 写入。旧的单行回调继续作为没有批量端口的回放和测试适配器回退。所有写入仍处在原有单个扫描事务内，receipt、幂等键、point-in-time 输入和研究只读边界不变。

## 后果

合成 200 标的探针中，事件状态读取从 600 次 SQL execute 降为 3 次，往返数量减少 200 倍；这只代表 repository statement count，不代表 owner 线上端到端耗时。重证据和拒绝投影也从逐标的 execute 收敛为每类一个批次。事件插入、episode 生命周期和 paper evidence 仍按顺序执行，后续若要继续批量化需单独验证状态机顺序。

## 怎样保证

- `load_intraday_signal_event_states` 保留每个标的独立状态对象，并在后续写入后更新 `latest_by_key`。
- 批量写失败会回滚原有扫描事务；非关键的 scan timing 仍在主 receipt 之后 best-effort 合并。
- `quant-service/tests/test_intraday_scan_repository.py`、`test_intraday_scan_signal_persistence.py`、`test_intraday_rule_snapshot_repository.py` 覆盖批量读取、回退和 cursor 批量写。
- `docs/work/probes/2026-10-16-intraday-event-state-batch.out.txt` 保存无 owner 数据库访问的 statement-count 探针；上线后必须按同一交易日复测真实 statement count、事务时长、锁等待和隧道速率。
