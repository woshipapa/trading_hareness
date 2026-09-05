# Edge → owner future-writer cutover

状态：已切换（2026-09-05，非交易时段）。历史数据不迁移；edge47 的
`quant_intraday_edge` 保留为历史证据库。下一个交易日开始，新的盘中
evidence、signals 和 outcomes 由 Longhu peer runtime 写入 owner
`trading_hareness`（G: 12T）。

## 运行边界

```text
edge47: Feishu listener/adapter + n8n + analyst relay + delivery ledger
Longhu host: quant-service intraday_edge + research/post-close scheduler + provider polling + strategy scans
owner G: PostgreSQL trading_hareness (唯一新数据写入库)
```

历史 edge 库不删除、不回灌、不参与新 writer 的实时决策。跨库历史分析仍走
显式 archive/evidence 导入流程。

## 预热

在 Longhu 主机的 shared-peer 目录准备私有 provider 文件（0600）：

```bash
deploy/shared-peer/compose.yaml
deploy/shared-peer/compose.intraday-owner.yaml
```

覆盖文件默认 `PEER_RUNTIME_PROFILE=research`、
`PEER_BACKGROUND_TASKS_ENABLED=false`，不会抢占 edge lease。

## 切换窗口（已执行）

已在非交易时段执行：

1. 检查 owner PostgreSQL 隧道连续查询、schema revision 和磁盘水位。
2. 停止 edge `quant-intraday-edge.service`，确认所有 `background_loop:*` lease 已释放。
3. 在 Longhu 主机设置 `PEER_RUNTIME_PROFILE=intraday_edge`、
   `PEER_BACKGROUND_TASKS_ENABLED=true`，重建 `quant-research`。
4. 检查 `/health`、`/api/v1/intraday/services/status` 和唯一 writer lease。
5. 开盘前只做 provider/健康冒烟，不触发历史回填。
6. 交易日首日保持观察，不调整策略阈值。

验收结果：目标容器使用当前代码启动，owner schema 已完成增量 Alembic
迁移；`/health` 为 `ok`，唯一 writer leases 在 owner 库生成；edge
`quant-intraday-edge.service` 已停止，edge 的 n8n、飞书 relay 容器仍健康。
目标机处于周末 standby，下一交易日进入实际采集窗口。

## 研究/盘后调度迁移（2026-09-05）

edge 上的盘后研究代码此前随镜像存在，但 `intraday_edge` profile 会主动关闭
`strategy_review`、`post_close_strategy`、`ten_day_leader_rotation` 和
`daily_strategy_summary` 等 research loop。现增加 owner 上的
`quant-research-scheduler` 独立容器：

```text
quant-research              QUANT_RUNTIME_PROFILE=intraday_edge
  └─ 实时采集、观察池扫描、研究提醒（7 个 intraday leases）

quant-research-scheduler    QUANT_RUNTIME_PROFILE=research
  └─ 午盘/收盘 review、盘后候选、十日龙头、日终摘要（独立 research leases）
```

两个容器共享 owner PostgreSQL，但租约标签不同；research worker 不会获取
实时采集租约，也不启用 Feishu 直发。成员回填默认关闭，需单独启用并设批量上限。
edge 的历史实时证据不迁移，仍作为历史库保留；研究任务读取 owner 当前可用的
证据，并通过同日期幂等键重启安全地补跑。

观察池控制配置已在切换后单独对账：edge 与 owner 均为 82 条、81 条启用，
symbol/启用状态哈希一致。同步脚本只传观察池控制字段，不传行情、信号或历史
证据，也不会触发逐票历史 hydration。目标显式观察池上限设为 100；盘口与分钟
剖面按配置的高优先级子集采集，不是全池同时覆盖；秒级校验对所选子集轮询。
2026-09-05 复查发现远端遗漏上限配置，已纳入 Longhu 主源修复部署验收；
具体来源边界见 [实时源优先级](OWNER_REALTIME_PROVIDER_PRIORITY.md)。

edge 的量化 systemd unit 及其 daily、live-acceptance、materialize timers 已
disable；edge 保留 n8n/Feishu relay。relay 与 n8n 的 `QUANT_SERVICE_URL` 已
切到 edge 本地的 `15682` owner API 隧道，并使用 owner API 写密钥。目标镜像已
写入 release `owner-intraday-20260905` 与提交标识，便于审计。

## Feishu

edge47 的群监听、interactive 卡片转发、分析师同步和投递 ledger 保持运行。
策略提醒优先使用目标机的直接 Feishu 配置；如启用 edge relay，必须通过独立
SSH 隧道和 `X-Quant-Alert-Token`，不得开放公网端口。

## 回滚

关闭 Longhu writer，恢复 edge service 和原 `intraday_edge` 连接配置；owner
新增数据保留，不执行反向删除。只有连续多个交易日验证通过后，才考虑清理
edge 上的实时进程和旧容器，历史库继续保留。
