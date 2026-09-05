# Owner 实时数据源优先级

更新时间：2026-09-05。此规则只改变数据获取与证据标注，不修改策略阈值、
样本晋级或自动交易权限。

## 按能力选主源

| 链路 | 优先来源 | 补充 / 降级边界 |
| --- | --- | --- |
| 显式观察池最新价、量比、换手 | Owner Longhu `GetStockPanKou` | 腾讯并行作独立对照；陈旧/缺失逐股回退，新浪补缺 |
| 十档盘口 | Owner Longhu | 腾讯五档补齐缺失/陈旧标的；单边封板盘口保留 |
| 分钟策略确认、盘末分钟剖面 | Owner Longhu 日期可验证的分钟 | 日期缺失/跨日/失败时腾讯兜底；不把 HHMM 补成“今天”冒充实时 |
| 特殊窗口独立价差校验 | Tushare Super GET | 保留独立来源，不能改成 Longhu 自己校验自己 |
| 全 A 横截面 | Fuyao / THS | Longhu 已验证的观察池报价不冒充全 A 快照 |
| 全板块净资金曲线 | 东财 | Longhu 板块排名的无字段字典数值不冒充净资金，不混用分类代码 |

Longhu 请求只经 owner shared gateway，不在执行机保存或直连上游供应商凭据。
`QUANT_LONGHU_DIRECT_ENABLED=false`。基础镜像/配置均保持其他源可用。

## 质量门禁

- 优先权在时间门禁之后：陈旧、缺时间、未来时间、非法价格不能覆盖新鲜备用源。
- Longhu `total_amount` 是手，观察池归一化为股（乘 100）；原始盘口及 raw 保留手。
  其 `total_turnover` 为元，不乘 100。原生量比/换手优先于本地推算。
- 前后盘口仅在同 `(symbol, source_name)` 且时间合格时计算 OFI，切源不混算。
- 板块、流量及价格分别标来源；得不到的字段保留缺失，不臆造主力净流入。
- 当前显式观察池上限 100；盘口与分钟仍是配置的高优先级子集，不能声称
  100 只同时具有完整十档/分钟记录。

## 本次只读探测

目标已有 82 只启用观察股：一次兼容行情批量请求 82/82，0 缺失、0 重复，
耗时约 1.56 秒，业务状态 completed。目录 89 个样例可读取。
盘口、分钟、市场宽度/情绪、板块异动、大单样例均返回业务成功。
这是周末连通性与批量覆盖验收，返回最近交易日为 2026-09-04，**不是盘中新鲜度验收**。

`GetPlateInfo_w38` 是涨停/连板相关板块及成员，不是所有板块目录；
`GetBKJJ_W36` 的部分数值列仍待字段/单位校验。它们可作为独立研究补充，
尚未替换全市场资金曲线。下一交易日需核查 scan 的
`licensed_watch_status.eligible_symbols/rejected_symbols/fallback_symbols`、
实际 observation source、延迟和分钟确认，而非仅看 HTTP 200。

## 部署核验

使用 `deploy/shared-peer/compose.intraday-owner.yaml`，确保**容器内**
`INTRADAY_WATCHLIST_MAX_SYMBOLS=100`，而非只有本地文件已修改。
重建后检查 `/health` 构建标识、`/api/v1/intraday/services/status`、owner DB lease，
并核实 edge 量化 writer 仍停用；edge 飞书/n8n 保留。
历史库不迁移，不在验收中回填数据或发送测试群消息。
