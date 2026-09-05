# Owner 盘后研究调度

`quant-research-scheduler` 是 owner 上的第二个 quant-service 实例，专门承担
原 edge 量化服务中的 research/post-close loop。

自动任务包括：

- 11:31 午盘 review、15:05 收盘 review（含分析师日评/周评、结果结算和评分卡）；
- 18:50–20:30 同日盘后候选与 watchlist main-wave 研究；
- 18:55–20:30 十日龙头轮动研究；
- 19:15–22:00 日终策略摘要。

任务使用 `quant.runtime_leases` 和日期/会话幂等键。服务重启只会读取同日期完成记录，
不会重复生成同一批研究结果。该 worker 的 Feishu 直发关闭，结果写入 owner 数据库，
由前端和现有 edge relay 读取；不会自动下单。

部署方式：

```bash
docker compose --env-file .env \
  -f deploy/shared-peer/compose.yaml \
  -f deploy/shared-peer/compose.intraday-owner.yaml \
  up -d --no-build --wait quant-research quant-research-scheduler
```

核验：

```bash
docker compose ... ps
curl -s http://127.0.0.1:15683/health
curl -s http://127.0.0.1:15683/api/research/intraday/services/status
```

周末只应看到 `standby`，交易日应分别看到 research 租约：
`strategy_review`、`post_close_strategy`、`ten_day_leader_rotation`、
`daily_strategy_summary`。edge 的历史证据不回灌；这次迁移是代码和自动调度迁移。
