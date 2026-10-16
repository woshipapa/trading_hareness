# 0035 盘中内存缓存与 SSD 回放格式

- 状态：提案实现中
- 日期：2026-10-16
- 范围：owner/peer quant 的盘中 raw evidence staging、research scheduler 回放

## 背景

盘中采集、策略计算和 Windows PostgreSQL 写入目前共享跨主机隧道。采集返回后，策略可以直接使用内存里的行情，但高频证据仍会在盘中产生大量数据库往返。把所有状态都延后会破坏信号去重、receipt 和实时健康，因此需要分层写入协议。

## 分层

### L0 内存 `CaptureBatch`

每次 provider 返回先构造一个不可变批次，字段固定为：

```text
batch_id           UUID
schema_version    capture-batch-v1
trade_date        Asia/Shanghai date
provider_key      实际返回数据的 provider
capability        采集能力
observed_at       UTC timestamp
available_at      UTC timestamp
sequence          进程内单调序号
rows              tuple[Mapping]，冻结后只读
row_count         int
payload_sha256    canonical rows 的 SHA-256
```

内存层只保存当前计算批次和有限的最近批次索引：`(capability, symbol)` 的最新值 TTL 120 秒，最多 256 MB；它用于策略、特征和健康读的低延迟读取。内存确认策略已消费后即可淘汰，不能把内存当成 durable evidence。

### L1 SSD `evidence segment`

SSD 是盘中 raw evidence 的 durable boundary。每个 segment 只属于一个 `target_stream`，目标表不由文件内容拼接 SQL，而由代码中的固定 registry 选择。

目录：

```text
<root>/pending/<trade_date>/<target_stream>/<segment_id>.jsonl
<root>/done/<trade_date>/<target_stream>/<segment_id>.jsonl
<root>/quarantine/<trade_date>/<target_stream>/<segment_id>.error.json
```

文件为 canonical UTF-8 JSON Lines：首行 header，随后是 records，末行 footer。单个 segment 达到 1 MiB、500 records 或 200 ms 即封存；临时文件写完后 `fsync(file)`、`rename`、`fsync(directory)`。每条 record 都保留原始 `effective_at` 和 `available_at`。

```json
{"type":"header","schema":"evidence-segment-v1","segment_id":"...","target_stream":"raw_market_observations","provider_key":"...","capability":"...","trade_date":"2026-10-16","created_at":"..."}
{"type":"record","record_id":"...","sequence":123,"effective_at":"...","available_at":"...","payload_sha256":"...","normalized":{},"payload":{}}
{"type":"footer","row_count":500,"first_sequence":123,"last_sequence":622,"content_sha256":"...","closed_at":"..."}
```

segment 只在 footer hash、row count 和 header/footer 一致时进入 `pending`；未封存的临时文件在启动恢复时只允许校验后续写或移入 quarantine，不能直接回放。

### L2 PostgreSQL

回放 worker 只通过固定 stream adapter 写入：

| stream | 首批范围 | 幂等键 | 盘中是否保留实时写入 |
|---|---|---|---|
| `raw_market_observations` | watch tape、all-A、公开证据、竞价原始证据 | provider/capability/market/symbol/effective_at/payload_sha256 | 否 |
| `intraday_quote_observations` | 盘口和报价归因 | symbol/source_name/observed_at | 仅实时策略所需最小投影 |
| `intraday_board_flow_snapshots` | 板块流曲线 | snapshot_minute | 否 |
| `intraday_minute_sessions` | 分钟 profile | symbol/trading_date/minute_bucket/source_name | 否 |

`intraday_scan_runs`、`intraday_signal_events`、事件去重、lease、health 和必须驱动盘中读的最小投影不进入延迟 stream。规则输入快照只有在确认 scan receipt、foreign key 和重放顺序协议后才加入延迟 stream。

## 回放节奏

research scheduler 使用 5433 batch lane，单 worker、单事务、默认每批最多 100 records，每 30 秒一批；大 segment 超出本批上限时，提交后把剩余 records 封存为新 segment，再归档原 segment。事务超过预算、池等待或数据库拒绝时退避 120 秒。数据库成功提交后才把 segment 移到 `done`。回放失败保留 `pending`，文件移动失败可安全重试，因为 adapter 使用相同幂等键。

SSD `done` 默认保留 7 天，quarantine 不自动删除。磁盘低于 3 GiB 时停止新 SSD segment 并回退到已有数据库路径；所有降级都记录计数和 warning，不能静默丢 raw evidence。

## 资源预算

- 内存：当前批次、最新值 cache 和最多 256 MB 的 recent envelope index；不缓存无限历史。超过字节或条数上限按 LRU 淘汰。
- SSD：按 segment 追加，避免每条 record 一个 inode；pending/done/quarantine 分离，便于水位统计和恢复。
- CPU：canonical JSON、hash 和可选压缩只在 SSD writer 中执行，策略计算消费同一批次，不复制大对象。
- 网络/数据库：盘中只保留小状态写；回放使用 5433、固定小批次和自适应退避，不能因 200 Mbps 链路而增加 PostgreSQL 并发。

## 迁移顺序

1. 先让 `watch_scan_tape` 使用统一 segment writer，shadow 对账行数、hash 和 pending 水位。
2. 再接入 all-A、分钟、盘口、板块流等 raw stream；每个 stream 单独回归幂等和点时字段。
3. 最后评估 quote/rule snapshot 等需要 foreign key 或实时状态的表，未完成协议前保持数据库路径。

本决策不改变任何策略阈值、promotion、`live_effect` 或订单路径。
