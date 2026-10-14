# 把 edge47 上的 feishu-relay、xhs、itougu 搬到 owner 主机（116.62.30.225）

W015 的步骤文档。依据：只读清点（`docs/work/probes/2026-10-14-edge47-*`、`2026-10-14-newhost-readiness.out.txt`）和 Codex 的只读审计（线程 `01a13b03`，对着代码核实过其中的写入者围栏、端口冲突和隧道目标）。

## 方案

新主机上起系统级 Docker（`docker.io` 29.1.3，已装，已启动，与量化用的 rootless Docker 并存），edge 的 compose 原样跑（`network_mode: host`，所有 `127.0.0.1` 地址不变）；路径、用户、systemd 单元与 edge 相同；PostgreSQL 16 来自 apt。edge 上不属于本仓库的服务（stock-reports、paper-arxiv-edge、paper-s2-gateway、sub2api 等）不动，其中工作站的 `s2-gateway-tunnel`（18791）和 n8n 工作流里写给 stock-reports 的 `https://47.114.113.152:18081` 都继续指向 edge。已核对：留在 edge 上的服务没有调用被搬走的端口（5678、18300、18790、15678、18084），18081 的 nginx 没有来源限制。

## 先决条件（切换前必须全部为真）

| # | 条件 | 状态 |
|---|---|---|
| P1 | 系统级 Docker 在新主机上 active，PostgreSQL 16 在线 | 完成（2026-10-14 22:30） |
| P2 | 数据库 `n8n_relay` 已建，角色 `relay_n8n`、`quant_edge`、`quant_edge_export` 已导入 | 完成 |
| P3 | 4 个镜像在新主机上：adapter、xhs（自制，从 edge 传）；n8n、runners（公共，从镜像站按 digest 拉） | 进行中 |
| P4 | 全库第一遍已导入（T0 记在 edge 的 `/root/mig-db-T0.txt`） | 进行中 |
| P5 | 文件、venv、单元、配置已就位，权限和属主与 edge 一致（`/etc/*.env` 为 640 或 600，bridge 状态属 `larkagentx`） | 文件已复制，权限待核 |
| P6 | compose 覆盖文件：每个容器的 `mem_limit`（adapter 512m、xhs 1g、n8n 1g、runners 512m），不改 edge 原来的 compose | 待写 |
| P7 | 新的 `FEISHU_RELAY_WRITER_ID` 值已定（`owner-116-62-30-225`），写进新主机的 `runtime.env` | 待做 |
| P8 | 工作站监督进程的三条隧道改址已备好（补丁），但未启用 | 待做 |
| P9 | 新主机上 `curl https://47.114.113.152:18081` 通（n8n 工作流调 stock-reports 的来源 IP 变了） | 待核 |
| P10 | 回退命令已写好并在 edge 上做过干跑 | 待做 |

## 切换（今夜，量化无新数据时段；必须在 07:40 的第一个 XHS 定时之前验证完）

1. **停 edge 的消费者**，且不让它们自己回来：
   `systemctl disable --now itougu-neican.service itougu-perf-daily.timer itougu-perf-weekly.timer larkagentx-group-relay.service`；
   `docker update --restart=no` 然后 `docker stop` 四个容器（先 n8n 与 runners，再 xhs，再 adapter）。edge 上的容器和数据保留，不删。
2. **核对已停**：edge 上没有 `node` adapter、`bridge.py`、`itougu_neican_relay.py` 进程，18300、18790、18084、5678 无监听。
3. **最后一次同步**（edge 此时只读）：
   - 库：小表整表（`--clean`）加 `execution_data` 的新行（`executionId` 大于新库最大值）、`baidu_pan_archive_jobs` 中 `created_at` 或 `updated_at` 在 T0 之后的行（`ON CONFLICT (archive_id) DO UPDATE`），其余表整表；序列用 `setval` 对齐；之后逐表行数比对。
   - bridge 的 `/var/lib/larkagentx`（含 `-wal`、`-shm`，停服后干净）、`/var/lib/itougu-neican`、`/opt/feishu-relay-edge/adapter-ingestion`、`xhs-state`（`queue.sqlite3` 带 WAL）、n8n 数据卷里 T0 之后变过的文件。
4. **新主机起栈**，顺序：PostgreSQL → n8n（+runners）→ itougu → adapter → bridge → xhs。
   - adapter 起之前执行：`UPDATE feishu_relay_writer_ownership SET writer_id='owner-116-62-30-225', generation=generation+1`（edge 的 adapter 即使被人启动，也只会处于"仅观察"，不再写）。
   - 每一步都等健康再下一步；adapter 和 xhs 要看到 `runtime_source` 是 source-overlay，bridge 要看到已认证和 WebSocket 在线，不只看 systemd active。
5. **工作站改隧道**（健康之后）：`feishu-tunnel`（18300）、`xhs-edge-tunnel`（18790）改指 `stockpeer@116.62.30.225`；`paper-kb-webhook-tunnel` 的 `-R` 端口由 15678 改为 25678（避开量化 compose 里 profile `n8n` 的 `127.0.0.1:15678`），同时把新主机 `runtime.env` 里三个 `PAPER_KB_*_WEBHOOK` 改为 `127.0.0.1:25678`。`s2-gateway-tunnel`（18791）不动。
6. **核对**：见下。

## 核对（切换后立刻）

- 飞书：bridge 认证与 WebSocket 在线；一条测试消息从源群到目标群走通；缺口修复一轮没有错误。
- adapter：`/health`、`runtime_source`、写入者归属（writer_id、generation）；`feishu_group_relay_*` 水位在前进。
- n8n：5 个工作流都 active（含 XHS 的定时触发），凭据可解密，一次手动执行成功。
- xhs：采集器 `/health`，cookie 可用（不触发风控），`queue.sqlite3` 的就绪作业数与 edge 停服时一致、没有被重复投递。
- itougu：状态文件在、`itougu_auth.json` 可用、下一次定时（盘中 15 秒；`--trading-hours-only`）正常；两个定时器的下次触发时间合理，没有因 `Persistent=true` 立刻补跑。
- 百度归档：`baidu_pan_archive_jobs` 无 `leased`、无重复上传；OAuth token 可刷新。
- 量化侧：`127.0.0.1:15682/health` 仍 200，四个量化容器 healthy，主机内存留有余量（总用量低于 6 GB）。

## 回退

边缘服务只是停止，没有删除。回退：新主机 `systemctl disable --now` 同样几项并 `docker stop`；把写入者归属改回 edge 的 ID 且 generation 再加一；edge `docker start` 四个容器、`systemctl enable --now` 单元；工作站三条隧道改回。分裂期间在新主机上产生的消息、作业和归档不会自动回灌，需要按表导出；所以回退窗口越短越好，旧栈在两个完整交易日验证通过之前保持停止但完整。

## 切换之后的后续（不在今夜做）

- 决策记录：两个独立发布单元同机共存（`config/components.json` 的 `47edge` 与 `47owner` 如何表示；`OWNER_MARKERS` 与隔离测试需扩到 workflow、xhs、itougu、failover、status 脚本）。
- 发布工具的默认主机：`release-sync-status.sh`、`release_plan.py`、`hotfix-*`、`deploy-*` 里写死的 `root@47.114.113.152`；切换后在改完之前，任何发布都要显式传 `RELAY_EDGE_HOST`。
- 现有 failover/failback 脚本的表清单落后（缺 `baidu_pan_archive_jobs` 等），不能当作新栈的回退工具。
- 工作站 `.env.edge` 与 edge 在用的不一致的原因，以及下一次 `sync-secrets.sh` 之前的对账。
- 18301 的 dashboard：新主机没有 tailscale，先走 SSH 隧道；要恢复 tailnet 访问，需要用户在 tailscale 控制台授权。
