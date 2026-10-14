# 更换 owner 中转主机

owner 的量化服务跑在一台中转主机上（2026-10-14 17:08 起是 `116.62.30.225`，在另一个阿里云账号下，内网地址 `172.26.62.126`；此前是 `118.178.133.37`，2026-10-13 起；更早是 `47.110.79.189:3535`）。旧主机已停止写入并屏蔽 Docker，不要解除。
数据库不在这台主机上：它在对方的 Windows 工作站，工作站用反向 SSH 把库和对方自己的接口送到中转主机的
回环端口。所以换主机只搬“容器、发布目录树、配置和几把钥匙”，不搬数据。

本文是换主机的步骤。工具是 `scripts/shared-peer/owner-host-switch.sh`（在操作员工作站上运行）；
2026-10-13 那次迁移是手工做的，过程记在 `docs/work/BACKLOG.md` B041，本文把它整理成可重复的顺序。

## 这台主机上有什么（2026-10-14 清点，`docs/work/probes/2026-10-14-owner-host-inventory.out.txt`）

| 东西 | 现状 | 换主机时 |
|---|---|---|
| 系统 | （清点于旧主机）Ubuntu 24.04，x86_64，2 核，3.5 GB 内存，30 GB 盘（已用 6.8 GB）；新主机 4 核 7.3 GB、69 GB 盘（已用 6.8 GB） | 新主机内存不少于 3 GB：894 MB 的那台在 2026-10-14 开盘 6 分钟后失去响应 |
| 服务用户 | `stockpeer`，`loginctl` linger 已开，没有免密 sudo | 用户名保持 `stockpeer`（发布目录树里的软链接是绝对路径）；开 linger 和装 rootless Docker 要 root，由对方或用户做 |
| Docker | rootless，29.1.3，compose 2.40.3 | 同上 |
| 镜像 | `trading-hareness-peer-quant-research`（1.12 GB）和两个隧道镜像（各 18.8 MB） | **搬过去，不重建**：主机上 `~/wheelhouse` 是悬空的软链接，本来也建不出来 |
| 发布目录树 | `~/trading_hareness` → `~/.local/share/trading-hareness/releases/<全量发布标签>/trading_hareness`（83 MB），里面的 `hotfix/quant-service/current` 指向当前的代码覆盖层 | 整棵搬过去，路径不变 |
| 配置 | `deploy/shared-peer/.env` 和 `intraday-secrets.env` 都是指向 `~/.secrets/owner/.env.owner`（600）的软链接，46 个键 | 文件主机对主机地搬，不落到工作站；`PEER_SSH_HOST` 是**主机自己的内网地址**，新主机上要改（见第 3 步） |
| 自连隧道 | 两个隧道容器用 `~/.ssh/peer_tunnel_ed25519` 登录主机自己（`PEER_SSH_HOST`），去够工作站送来的回环端口 | 新主机上重新生成：`scripts/shared-peer/configure-peer-self-tunnel.sh` |
| 工作站的反向隧道 | 回环 `15432`（库）、`15433`（批量库）、`15681`、`15680`（对方自己的接口） | **对方把 Windows 上的反向隧道指到新主机**；我们只核对端口到了没有 |
| `authorized_keys` | 5 把：Windows 的反向隧道、操作员工作站（两行）、自连隧道、edge 到 owner | 新主机上要有前两类和最后一类；自连那把在新主机上重新生成 |
| systemd 用户单元 | `peer-session-guard-heal.timer`、`peer-session-guard-preopen.timer`、`peer-daily-backfill.timer` | 单元文件随目录树搬；**定时器到切换时才启用** |
| 数据卷 | `peer_quant_data`（132 KB）、`peer_quant_research_data`（8 KB） | 搬，都很小 |
| 别的 | 80/443 上有 nginx（站点 `default`、`stockbrain`） | 不是本仓库的东西，归对方 |

## 指向这台主机的地方

| 位置 | 是什么 | 谁改 |
|---|---|---|
| `scripts/shared-peer/owner-host.sh` | 所有发布和状态工具的默认地址，仓库里只此一处 | agent，提交 |
| 工作站 `config/secrets/.env.local` 的 `LONGHU_SSH_HOST`、`LONGHU_SSH_PORT`（和 `OWNER_PEER_SSH_HOST` 一组，后者是主机的内网地址） | 本机 `owner-tunnel` 和探针用 | 用户（会话的安全检查拦“改变流量去向”的改动） |
| 工作站 `~/.ssh/known_hosts` | 新主机的指纹 | 用户：对着对方给的指纹核对后加入 |
| 工作站的服务管理器 | `owner-tunnel` 的目标在启动时就定了 | 用户：`launchctl kickstart -k gui/$(id -u)/com.papa.svc-supervisor` |
| edge 主机的 `quant-longhu-owner-tunnel.service` 和 `/etc/quant-intraday-edge/known_hosts` | 控制台和中继读 owner 的路 | 用户在 edge 上改（命令见第 5 步） |
| `scripts/teacher_review_daily.py` 的默认地址 | 老师复盘导入 | 随环境变量 `PEER_SSH_HOST`；文件里的默认值单独改 |
| 文档（`AGENTS.md`、上一级 `AGENTS.md`、`OPERATIONS.md`、`docs/RELEASE_SYNC_47.md` 等，`git grep -n <旧地址>` 列全） | 说明文字 | agent；历史记录行不改 |

## 步骤

每一步写了谁做。会话不能代做的（装 Docker、改工作站的密钥和隧道目标、写新主机）由用户执行，命令在这里。

### 0. 要先知道的

- 新主机的地址、SSH 端口、主机指纹（对方给，或在主机控制台上读）。
- 切换那一刻旧主机上的两个服务容器必须先停：两台同时跑就是两套采集和两个调度进程往同一个库写。
- 切换放在不能发布的时段之外（`config/release-windows.json`），最好是收盘后：盘中换主机会丢那几分钟的快照，09:15–09:30 之间换会丢当天的竞价。

### 1. 新主机的底子（对方或用户，root）

用户 `stockpeer`；`sudo loginctl enable-linger stockpeer`；rootless Docker 和 compose 插件；内存不少于 3 GB。
`scripts/shared-peer/provision-lightserver-rootless.sh` 是当初给这类主机装 rootless Docker 的脚本，先读它再用。
把操作员工作站的公钥、edge 到 owner 的公钥、Windows 反向隧道的公钥加进 `~stockpeer/.ssh/authorized_keys`
（清点里有这几把的注释名；公钥从旧主机的同名文件里取）。

### 2. 预检，然后搬（工作站；搬是往新主机写，用户执行）

```bash
scripts/shared-peer/owner-host-switch.sh inventory                       # 旧主机现在有什么
scripts/shared-peer/owner-host-switch.sh preflight stockpeer@<新主机>    # 新主机缺什么（只读）
scripts/shared-peer/owner-host-switch.sh copy stockpeer@<新主机>         # 会搬什么（只读）
scripts/shared-peer/owner-host-switch.sh copy stockpeer@<新主机> --apply # 搬：镜像、目录树、配置、数据卷、单元文件
```

`copy` 只读旧主机、只写新主机；不构建镜像，不启停任何服务，不把配置文件的内容显示出来或落到工作站。
1.1 GB 的镜像经工作站中转，几分钟。

### 3. 新主机自己的两件事（在新主机上，用户或经 SSH）

```bash
bash ~/trading_hareness/scripts/shared-peer/configure-peer-self-tunnel.sh <新主机的内网地址> 22
```

然后把 `~/.secrets/owner/.env.owner` 里的 `PEER_SSH_HOST` 改成新主机的内网地址（`hostname -I` 的第一个；
`PEER_SSH_PORT`、`PEER_KNOWN_HOSTS_PATH` 一般不用动）。只改这一个键，别的键是对方的生产配置。

请对方把 Windows 上的反向隧道指到新主机。再跑一次 `preflight`，到没有 `MISSING` 为止；其中
“the Windows forward on 15432 has arrived” 三行就是对方那一步做完的标志。

### 4. 切换（收盘后；用户执行，会话核对）

1. 旧主机：停两个服务容器和三个定时器（隧道容器不用管）。
   ```bash
   systemctl --user disable --now peer-session-guard-heal.timer peer-session-guard-preopen.timer peer-daily-backfill.timer
   cd ~/trading_hareness/deploy/shared-peer && docker compose -f compose.yaml -f compose.intraday-owner.yaml stop quant-research-scheduler quant-research
   ```
2. 新主机：起隧道，主服务健康后再起调度进程（与全量发布的顺序相同），再启用定时器。
   ```bash
   cd ~/trading_hareness/deploy/shared-peer
   C="docker compose -f compose.yaml -f compose.intraday-owner.yaml"
   $C up -d --no-build --pull never --wait db-tunnel db-batch-tunnel
   $C up -d --no-build --pull never --wait quant-research
   $C up -d --no-build --pull never --wait quant-research-scheduler
   curl -fsS http://127.0.0.1:15682/health | head -c 300; curl -fsS http://127.0.0.1:15683/health | head -c 300
   systemctl --user enable --now peer-session-guard-heal.timer peer-session-guard-preopen.timer peer-daily-backfill.timer
   ```
   两个 `/health` 报告的版本应与旧主机切换前的相同（`inventory` 里的 overlay）。

### 5. 把指向旧主机的地方改过去

1. 工作站（用户）：核对指纹后把新主机加进 `~/.ssh/known_hosts`；改 `config/secrets/.env.local` 的
   `LONGHU_SSH_HOST`、`LONGHU_SSH_PORT`、`OWNER_PEER_SSH_HOST`（新主机内网地址）；重启服务管理器。
   **不要跑 `sync-secrets.sh owner`**：新主机的配置是从旧主机原样搬来的，重新生成的那份要先按键名对过。
2. 仓库（agent）：改 `scripts/shared-peer/owner-host.sh` 的一行地址，`scripts/test_release_unit_isolation.py`
   的 `OWNER_MARKERS` 加上新地址，文档里的地址跟着改；过 `python -m pytest scripts -q`，提交。
3. edge（用户，在 edge 上）：
   ```bash
   cp /etc/systemd/system/quant-longhu-owner-tunnel.service /root/quant-longhu-owner-tunnel.service.bak-$(date +%Y%m%d)
   ssh-keyscan -t ed25519 -p <端口> <新主机> >> /etc/quant-intraday-edge/known_hosts   # 再对一遍指纹
   sed -i 's/stockpeer@<旧主机>/stockpeer@<新主机>/' /etc/systemd/system/quant-longhu-owner-tunnel.service
   systemctl daemon-reload && systemctl restart quant-longhu-owner-tunnel && curl -fsS http://127.0.0.1:15682/health | head -c 200
   ```
   这个单元没有握手超时，对端挂死时会停在握手里而显示 active（2026-10-14 发生过，BACKLOG B060）：
   改地址时顺手给它的 ssh 加上 `-o ConnectTimeout=15`。

### 6. 核对（会话，只读）

```bash
curl -s http://127.0.0.1:15682/health | head -c 300        # 工作站经隧道：版本、提交
scripts/release-sync-status.sh --owner-source-sha <提交> --edge-source-sha <提交>
curl -s -o /dev/null -w '%{http_code}\n' http://127.0.0.1:18300/api/research/market/limit-up-risk/daily   # 经 edge 中继
scripts/release plan                                       # 能读到新主机
```

再看下一个交易时段：两条分钟快照每分钟一份（`docs/work/probes/2026-10-14-session-capture-gaps.py` 改日期后跑），
09:25 的竞价处置表，16:15 之后晚间阶段的回执。写进 `docs/work/FACTS.md`，并在本文件顶部和 `AGENTS.md` 里
更新“现在是哪台”。

### 7. 换到 4 核 8 GB、200 Mbps 之后的推进（用户 2026-10-14 的计划规格）

先说清楚边界：决定速度的主要不是中转主机。库在对方 Windows 工作站上，每条语句往返约 52 毫秒（经反向 SSH）；
授权接口约 7.4 次/秒封顶（`docs/work/FACTS.md` F027）。这两样不随主机变快。2026-10-14 13:03 现主机（2 核
3.5 GB）盘中一次采样：负载 0.82，主服务 CPU 44%、内存 325 MiB，调度 129 MiB，db-tunnel 两小时进出各约
4.9 GB（`docs/work/probes/2026-10-14-owner-load-1306.out.txt`）。所以顺序是先量、再一项一项放开，每放开一项
记一条事实、量一次，再做下一项。待办里对应的条目是 `docs/work/BACKLOG.md` B062。

**7.0 先量一个完整交易日（会话，只读）。** 新主机跑满一个交易日后：各容器 CPU 与内存的峰值（盘中每 10 分钟
一次 `docker stats`，存进 `docs/work/probes/`）；db-tunnel 与主机网卡的流量；扫描和两条分钟采集每轮的耗时；
晚间循环各阶段的耗时（回执里的时间）。和上面那次采样对照，写进 FACTS。

**7.1 逐行写库改批量（代码，收益最大，与硬件无关）。** 每条语句 52 毫秒，逐行写几千行就是几分钟（2026-10-14
补一只股票 48 天的复权因子：290 条语句 18 秒，FACTS F029）。先列出盘后和补数据路径上逐行写的地方，按耗时排序，
改成一条语句一批。

**7.2 补回为省内存拿掉的两个晚间阶段。** `market_temperature_intraday`（盘中温度序列）和 `minute_panel_export`
（分钟面板导出）要把当天全部分钟文档读进内存，894 MB 的主机上没放进晚间循环
（`docs/work/plans/peer-evening-stages.md:102`）。先在新主机上单独跑一次量峰值，再加进
`quant-service/app/peer_close_research.py` 的阶段表，走纯代码发布。

**7.3 数据库并发。** 主服务的阻塞线程 `QUANT_DB_BLOCKING_MAX_WORKERS` 是 8（已是代码上限，
`quant-service/app/runtime_executors.py:131`），调度是 4（`deploy/shared-peer/compose.intraday-owner.yaml:46,149`），
连接池上限 `QUANT_DB_POOL_MAX_SIZE` 12（上限 32，`quant-service/app/database.py:1863`）。瓶颈是往返延迟，
加并发比加 CPU 有用；但**先问对方工作站上的库能承受多少连接**，再定数。放到 16 要改代码上限；compose 的默认值
不改（改 compose 会让发布变成全量），改主机配置里的 `PEER_*` 键。

**7.4 晚间重计算移出盘中进程。** 两个容器各只有一个 Python 进程，战法总结、历史回放、风险分这些晚间计算和盘中
采集抢同一把全局锁。4 核下把晚间计算放到单独的进程（或第三个容器），盘中采集不受影响。这一项要方案和审查。

**7.5 公共数据源并发。** `MARKET_SNAPSHOT_PUBLIC_CONCURRENCY` 2、`REALTIME_VENDOR_MAX_WORKERS` 4、
`LONGHU_MINUTE_BATCH_WORKERS` 4（`quant-service/app/settings.py`）。主要受各家限流约束，换机后按各源的限流
逐个试；授权接口的批量读取仍守 6 个、最多 8 个并发、盘中不跑的规矩（F027）。

**7.6 镜像构建。** 构建前的内存检查（可用不足 700 MB 拒绝）在 8 GB 上会通过；但主机上的依赖包目录
（`~/wheelhouse`）是空的，要先定依赖从哪里来（B050），否则仍只能搬镜像。

**7.7 顺带修的。** edge 到 owner 的隧道单元加握手超时（B060）；`scripts/teacher_review_daily.py` 的默认地址
改成读 `scripts/shared-peer/owner-host.sh`；本文件顶部和 `AGENTS.md` 里“现在是哪台”的说法更新。

### 回退

第 4 步之后、第 5 步之前出问题：在新主机上停两个服务容器和定时器，在旧主机上把它们起回来（第 4 步的
命令对调）。旧主机什么都没删，Windows 的反向隧道若已改到新主机，要请对方改回去。第 5 步做完之后回退，
还要把工作站、仓库和 edge 的三处地址改回去。旧主机至少保留到新主机跑满两个交易日。

## 换完之后别忘的

- 旧主机上的服务容器保持停止，不要让它的定时器把服务拉起来（第 4 步已禁用；`peer-session-guard` 会重启
  不健康的服务，所以必须是 disable，不只是 stop）。
- 下一次全量发布是在新主机上构建镜像：先看 `free -m`，可用内存不足 700 MB 脚本会拒绝。
- 新主机的 `~/wheelhouse` 也是空的：要重建镜像时依赖从哪里来，是另一件事（BACKLOG B050）。
