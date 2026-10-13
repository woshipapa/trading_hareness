下面这段可以直接转发给对方或他的 agent（2026-10-13 21:20 写）：

---

我是协作者这一侧（peer，仓库 `trading_hareness` 的 `main`）的 agent。按你的迁移说明，本机已切到新量化服务器，下面先回报，再列需要你那边确认或处理的事。**以下所有检查都是只读的；我没有重启任何云端服务，没有同步凭据，没有动旧服务器。**

## 一、回报

1. **SSH**：新服务器 `118.178.133.37:22` 的 ED25519 指纹与说明一致（`SHA256:JSgjaKCz873b9Na6b+DF6DBv1Ni+cToO0gY9O9bhZA8`），原私钥登录成功，`id -un` 为 `stockpeer`。
2. **云端健康接口**（20:33 起多次）：`15682` 与 `15683` 的 `/health` 都是 `status: ok`，`release: owner-code-20261013-04`，`git_sha: 261702310f06138bd2113d8dee838139b636230c`；四个容器 healthy。
3. **本地隧道**：`127.0.0.1:15682` 已指向新服务器，`/health` 返回 200、版本同上。`https://stock.toufai.top` 不带凭据返回 401（可达；我没有登录）。
4. **旧地址残留**：发布工具与凭据脚本的默认地址已改到新服务器（`main` 提交 `4bb8c7ff`），`scripts/release-sync-status.sh` 对新服务器 ALL CHECKS PASSED。还没改的两处在我们这边，正在处理：edge（`47.114.113.152`）上的 `quant-longhu-owner-tunnel` 服务仍指向旧机 3535（edge 用的那把密钥在新机上已能登录，只差改目标）；`scripts/teacher_review_daily.py` 的默认地址。`deploy/shared-peer/compose.yaml` 里 `PEER_SSH_PORT` 的默认值（3535）没有动，靠新机上的环境变量覆盖。
5. **没有同步 `.env.owner`**，也不会在核对之前同步（见下面第 4 条）。

## 二、需要你确认或处理的

1. **盘后流程由谁跑我们后加的阶段。** 我今晚才查清：完整的盘后流程只在你自己的服务上定时运行，那边的代码是 `20260920T194536-bc69bee85233-clean`，阶段表里没有 9 月 20 日之后加的阶段；我们这一侧每晚只跑 `peer_close_research` 的五个阶段。所以这些阶段从来没有自己跑过：`limit_performance_lists`、`limit_up_risk`、`market_temperature`、`market_temperature_intraday`、`broad_etf_flow`、`market_timing`、`candidate_ledger`、`minute_panel_export`、`daily_close_completion`（它只被对账循环调用）。今晚 10 月 13 日的涨停榜和大面风险分因此没有生成。
   - 我们今晚会手工补一次，只做两件事：在 `trading-hareness-peer-quant-research-1` 容器里取当天 5 张实时涨停榜（5 次授权接口只读调用）并写入 `quant.raw_market_observations`（`longhuvip` / `limit_performance_live`，5 份文档），再写 1 份 `limit_up_risk_daily` 读数。进程很轻（不加载整个服务），已不写库试跑过。**如果你不希望今晚有这次写入，请在 23:30 前告诉我。**
   - 请确认一个方向：你那边的服务近期会不会升级到当前 `main`？如果不会，我们就让 peer 自己的晚间循环负责这些 peer 独有的阶段（先只放榜单和风险分，都很轻），不改你那边的流程。
2. **日线控制面今晚是 blocked。** 20:52 调度容器 `/health` 的 `daily_control_plane`：`daily_rows 5560`、`adjustment_rows 5560`、`limit_rows 5560`，但 `fundamental_rows 5138`，原因 `latest canonical equity daily bars are missing same-date daily fundamentals 5138/5560`。请帮忙看一下：每日指标缺这 422 只是今天迁移造成的，还是平时也这样？它由你那边哪个阶段、哪个数据源写入？另外今天你那边 16:02 的那一轮里 `adjustment_factors` 有一次失败（随后是 completed）、`research_snapshot` 是 blocked，供参考。
3. **新机内存。** 20:33 量得总共 894 MB、可用 267 MB，交换区已用 410 MB，调度进程约 166 MB。我们会把在这台机器上跑的东西控制得很轻，不在上面构建镜像。想确认两点：这个规格是长期的吗？需要较多内存的一次性任务（例如三年历史的回放、当日全部分钟文档的导出）你希望我们放在哪里跑，还是干脆不要在生产这一侧跑？
4. **下一次凭据同步前的核对。** 你提到要保留新机上的生产角色和写入开关。请给我新机 `deploy/shared-peer/.env` 和 `intraday-secrets.env` 里**需要保留、且与我们 `env-split.py` 生成结果不同的键名**（只要键名和非机密的取值，如角色、开关；不要密钥、口令）。拿到之前我们不会运行 `sync-secrets.sh owner`。
5. **发布冻结。** 迁移之后你是否希望我们暂停发布一段时间？我们手上有一次纯代码发布待做（从 `26170231` 到 `main`，运行时文件 2 个：数据源目录里的一条比对声明、通达信并行采集的当日汇总多两个字段；不改任何数据源的状态，不涉及迁移和镜像）。如果你希望我们先暂停，请告知；否则会在允许发布的时段按原流程发。
6. **供你知晓，不需要处理**：新机调度容器 18:35:11 启动后，18:38–18:42 有一阵数据库连接失败（约 85 次 `error connecting … server closed the connection`，连接池排队到 64），所有后台循环在 18:38:49 丢过一次租约，18:42 之后恢复，此后日志里没有再出现。

## 三、请不要

- 不要为了以上任何一条重启新机上的容器或恢复旧机的量化服务。
- 回复里不要带私钥、令牌或完整的凭据文件。
