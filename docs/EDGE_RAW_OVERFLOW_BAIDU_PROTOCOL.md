# Edge raw 溢出归档协议

状态：协议、边缘 keyset 批次 API、ACK/offset ledger 和适配器单并发上传 lane
已实现；默认关闭，待完成端到端演练后启用。现有 latest-snapshot lane 不受影响。

## 目标

当 `intraday_edge` 的 PostgreSQL 热库接近磁盘临界水位时，实时行情、策略
扫描和告警不能因为 raw 证据写入而停止。完整 raw 证据转入百度网盘异步归档，
本地只保留策略实时所需的精简证据和有限热窗口。

百度网盘永远是 L3 冷存储，不进入实时策略读取路径，也不改变任何策略阈值或
订单路径。

## 三档状态

| 状态 | 触发 | raw 行为 | 实时策略行为 |
|---|---|---|---|
| `normal` | 资源正常 | 完整 raw 写入 edge 热库 | 正常 |
| `cloud_overflow` | 达到告警水位且百度队列可用 | 完整 raw 进入有界云归档队列；本地保留精简证据 | 继续运行 |
| `critical` | 达到停止水位，或云队列不可用且 spool 已满 | 停止非必要 raw；保留行情、特征、信号和失败计数 | 继续运行，策略缺 raw 时 fail-closed |

状态转换必须记录到健康接口和自动化运行记录，不允许静默丢弃。

## 异步链路

```text
provider -> bounded raw batch -> archive ledger -> Baidu uploader
                                      |
                                      +-> manifest/row-count/SHA256 ACK
                                      |
                                      +-> advance durable offset
```

采集协程只负责把一个有界 batch 写入归档 ledger，然后立即返回；上传、重试、
百度目录操作和远端校验均由独立 worker 执行。网络慢或百度不可用不能占用行情
采集 executor。

## Offset

raw 表没有依赖 UUID 顺序。归档游标使用稳定的复合键：

```text
(capability, effective_at, observation_id)
```

每个批次记录：

```text
archive_batch_id
first_offset
last_offset
row_count
compressed_bytes
sha256
remote_path
remote_fs_id
state
```

只有以下条件全部成功才推进 `last_offset`：

1. 本地批次生成完成；
2. 百度上传成功；
3. 远端文件大小与 manifest 一致；
4. gzip/JSONL 完整性校验通过；
5. manifest 已登记。

进程重启、断网或上传超时时，从上一个已确认 offset 重放。归档键使用
`dataset + first_offset + last_offset + sha256`，重复上传必须幂等；适配器据此
生成稳定的 batch UUID 和远端文件名，ACK 丢失重试不会产生第二个对象。

## 硬上限

- 单批压缩文件不超过 256 MiB；
- 归档 worker 最多 1 个并发上传；
- edge 本地 spool 不超过 256 MiB；
- ledger 未确认队列不超过 1,000 批；
- 单批内存只允许保存当前批次，不允许加载整日或整表；
- 达到 spool/队列上限后，只停非必要 raw，不停行情、特征、信号和告警；
- 所有失败写入 `raw_overflow_dropped` 计数器和健康告警。

## 热库保留

- 当日数据和最近实时策略窗口留在 PostgreSQL；
- 已闭市批次可上传百度，但必须先校验再删除；
- 删除后只执行有界维护，不在盘中执行 `VACUUM FULL`；
- 研究恢复路径为：百度分区 -> L2 parquet/DuckDB staging -> schema/hash/PIT 校验；
- 实时策略不直接读取百度对象。

## 与现有百度归档的关系

现有 `baidu-pan-market-archive` 继续归档最新研究快照；本协议新增的是 raw
批次流，不替换快照队列。两者共享百度 OAuth 和上传 worker，但使用不同 bucket、
幂等键和 offset，避免相互阻塞。

当前实现的内部接口为：

- `GET /api/v1/internal/raw-overflow/status`
- `GET /api/v1/internal/raw-overflow/next?stream_key=raw_market_observations:<capability>`
- `POST /api/v1/internal/raw-overflow/ack`
- `POST /api/v1/internal/raw-overflow/failure`

这些接口只接受 `X-Quant-Write-Key`，适配器每次只轮转一个 allowlist stream、
最多 500 行；压缩后再交给已有百度分片上传器。ACK 前不推进游标，ACK 后只删除已
校验且早于热窗口的精确前缀。非 allowlist 的日线/研究原始数据仍保持本地，避免
误把历史大表一次性送入冷存储。

## 启用前验收

1. 模拟磁盘告警，确认行情扫描仍按周期完成；
2. 模拟百度超时，确认队列重试且内存/spool 有界；
3. 模拟进程重启，确认 offset 不跳过、不重复产生研究记录；
4. 随机下载一个分片，验证行数、字节数、SHA256 和 PIT 字段；
5. 恢复本地热库后，确认策略仍只读 PostgreSQL；
6. 通过运行状态接口展示 `normal/cloud_overflow/critical`、队列深度、offset、
   最近成功和最近失败。

在上述验收完成前，`cloud_overflow` 只允许以观测/演练模式运行，不得改变现有
采集写入和策略可用性语义。

## 实施进度（2026-09-02）

- P1（已完成）：`raw_overflow_policy.py`、`raw_archive_offsets`/
  `raw_archive_batches`、内部鉴权 API、keyset 分页和 ACK 后精确前缀清理。
- P2（已完成代码、待生产演练）：Feishu adapter 已复用同一百度 OAuth、单并发
  raw worker、gzip JSONL、有界上传和失败重试；状态已接入 `/health` 和研究台。
- P3（未启用）：需要在非交易时段用测试 stream 做一次真实百度上传、下载校验、
  ACK/offset 重启恢复演练，再将两个环境开关切到 `true`。

启用前的具体开关为 `QUANT_RAW_OVERFLOW_ARCHIVE_ENABLED=true`（量化服务）和
`BAIDU_PAN_RAW_OVERFLOW_ENABLED=true`（适配器）。两者必须同时开启；任一关闭时
批次接口只返回 `disabled`，不会触碰 raw 数据。生产启用应先把
`QUANT_RAW_OVERFLOW_CAPABILITIES` 限定为实时 allowlist，观察队列/磁盘至少一个
交易日后再评估是否扩展到其他证据流。
