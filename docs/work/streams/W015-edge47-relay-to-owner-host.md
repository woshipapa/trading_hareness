# W015 edge47 上的 feishu-relay 和 itougu 迁到新 owner 主机

- state: building
- holder: Claude Code 会话 d39e7e11，2026-10-14
- goal: feishu-relay（adapter、bridge、xhs、n8n）和 itougu（内参、业绩报告）搬到 116.62.30.225 上跑；配置里的令牌、cookie、会话状态原样带过去；edge47 上的这些服务停掉，只留一套在写
- plan: docs/work/plans/edge-relay-move.md
- branch: main
- head: 154fd970
- next: 传输跑完（镜像、库第一遍）→ 写 compose 覆盖和新写入者 ID → 用户确认切换 → 按步骤文档切换并核对；今夜 07:40（XHS 第一个定时）之前完成验证
- updated: 2026-10-14

## Requirements

- 2026-10-14 17:5x 「你在研究一下当前edge47那边服务器部署的feishu-relay还有itougu的这些也可以迁移到这台机器了。你可以直接ssh到我的新机器上部署。」
- 2026-10-14 17:5x 「内容值你也要查看，因为里面会有我的一些服务的token等。这些都需要迁移的」（主会话的理解：配置里的值要核对并带过去；值不进对话、不进仓库，只在主机之间管道传输）

## Plan

`docs/work/plans/edge-relay-move.md`：先决条件表、切换步骤、核对、回退。依据 Codex 的只读审计（线程 `01a13b03`）和下面 Log 的核实。没有单独的审批；切换那一步由用户确认后才执行。

## Steps

- [x] 只读清点 edge47 和新主机 — `docs/work/probes/2026-10-14-edge47-inventory{,-2,-3}.out.txt`、`2026-10-14-edge47-config-shapes.out.txt`（每个配置键的空/非空、长度、是否指向本机地址；不含值）、`2026-10-14-newhost-readiness.out.txt`
- [ ] 用户决定三项（见下）
- [ ] 步骤文档、在新主机上预备（镜像 save/load、状态目录、配置）
- [ ] 切换：先停 edge 上的消费者再启新主机的；Mac 上三条隧道改指向
- [ ] 核对一个交易日

## Log

- 2026-10-14 17:5x 清点结果。edge47（2 核 3.5 GB，已用 2.7 GB，盘 40 GB 用 26 GB）上与本线有关的：
  - 容器（compose `/opt/feishu-relay-edge/docker-compose.yml`，全部 `network_mode: host`）：`feishu-relay-edge-adapter`（18300，Node，118 MiB）、`feishu-relay-edge-xhs`（18790，Python，镜像 1.15 GB，采样时 CPU 184%）、`feishu-relay-edge-n8n` + `n8n-runners`（5678/5679，镜像 2.47 GB + 0.72 GB，5 个工作流都 active）。
  - 主机服务：`larkagentx-group-relay`（bridge，用户 larkagentx，venv `/opt/supervisor/.venv`，状态 `/var/lib/larkagentx` 61 MB）、`itougu-neican`（15 秒轮询，仅交易时段）、`itougu-perf-daily`/`-weekly` 两个定时器、nginx 18301（只允许 tailnet 来源，转 18300）。
  - 数据：主机 Postgres 的库 `n8n_relay` 2.4 GB（`execution_data` 1.5 GB、`baidu_pan_archive_jobs` 878 MB，其余很小；n8n 和 adapter 共用，adapter 用 `PGHOST=127.0.0.1`）；`/opt/feishu-relay-edge/adapter-ingestion` 119 MB、`xhs-state` 14 MB、`/var/lib/itougu-neican` 208 KB。
  - 配置与令牌：`/etc/feishu-relay-edge/runtime.env`（55 个键）、`/etc/larkagentx-group-relay.env`（37）、`/etc/itougu-neican.env`（12）、`/etc/itougu-perf-report.env`（7）、`/opt/itougu-neican/itougu_auth.json`（登录头）、`/etc/feishu-relay-edge/xhs-cookie`、`/opt/feishu-relay-edge/certs/market-review-api.pem`，以及 compose 同目录 `.env`（`RELAY_PGPASSWORD`、`N8N_ENCRYPTION_KEY`、各 webhook 和令牌；n8n 的凭据用 `N8N_ENCRYPTION_KEY` 加密，搬库必须带同一把）。
  - 指向本机或旧地址的值（搬过去必须重看）：`QUANT_SERVICE_URL=http://127.0.0.1:15682`（新主机上同样是 127.0.0.1:15682，但见下面的网络问题）；`PAPER_KB_*_WEBHOOK`=`127.0.0.1:15678`（工作站反向隧道落在 edge 的 sshd 上）；`LARKX_INGRESS_URL`/`LARKX_ROUTE_CATALOG_URL`=`127.0.0.1:18300`；`BAIDU_PAN_REDIRECT_URI`=`http://localhost:8080`；n8n 的几个 `*_WEBHOOK_URL`=`127.0.0.1:5678`。
  - 工作站这一侧依赖 edge 地址的：监督进程里的 `feishu-tunnel`（:18300）、`xhs-edge-tunnel`（:18790）、`paper-kb-webhook-tunnel`（反向，落到 edge 的 15678）、`xhs-ai-worker`；`scripts/svc_supervisor.py` 主检出里有别的会话未提交的修改。
  - edge47 上还有不属于本线的：stock-reports（3.8 GB）、sub2api（3.5 GB 库）、paper-*、codex-model-router、mihomo、derper、tailscale 节点、Postgres 里的 `sub2api` 库。这些不动。
- 2026-10-14 17:5x 新主机现状（只读）：4 核 7.3 GB（用了 1.1 GB）、盘 59 GB 空；rootless Docker（用户 stockpeer），linger=yes；**`sudo` 要密码**；Docker Hub 不可达（000），pypi 可达；没有 node、psql、tailscale、redis；Python 3.12.3 但缺 `python3-venv`（`ensurepip` 报错）；nginx 二进制在、但改系统配置要 root；owner 容器用端口映射把 15682 暴露在宿主 127.0.0.1（由 rootlesskit 监听），不是 host 网络。
- 2026-10-14 17:5x 由此得出的四个技术问题：(1) rootless Docker 里 `network_mode: host` 是 rootlesskit 的网络命名空间，不是宿主的，所以 compose 里写的 `127.0.0.1:15682`、`127.0.0.1:5432` 在新主机上要重做（端口映射 + 同一个 compose 网络或 host-gateway），不能原样搬；(2) 没有 root 就装不了 Postgres 包、nginx 站点、tailscale；Postgres 只能用容器，但 Hub 不可达，镜像得在 edge 上 `docker save` 再传过去（edge 上的 Postgres 是 apt 包，没有现成镜像）或用国内镜像；(3) bridge 和 itougu 是宿主 Python 进程，可以用 `systemd --user` 跑，venv 可以整个拷过去（同为 Ubuntu 24.04、Python 3.12）；(4) 两边同时消费同一个飞书会话/同一个机器人，会重复或互相踢，所以切换必须先停 edge 再起新主机。
- 2026-10-14 17:5x 和仓库规矩的冲突：`AGENTS.md` 规定 edge 与 owner 是独立发布单元，edge 的脚本正文不出现 owner 地址，`test_release_unit_isolation.py` 用 `OWNER_MARKERS` 守这一点；edge 搬到 owner 主机后，edge 的热发布脚本的目标地址就是 owner 地址。需要先改规矩（决策记录）和测试，不能只改地址。另外：10 月 14 日上午 owner 旧主机失去响应（B058）时，飞书中继会跟着一起停，这正是当初分开放的理由。
- 2026-10-14 17:5x 关于值：配置文件的值我没有打印，也不会写进对话或仓库；核对用的是键名、是否为空、长度和是否含本机地址（探针脚本 `docs/work/probes/2026-10-14-edge47-config-shapes.py`）。搬运打算用 `ssh edge 'cat 文件' | ssh 新主机 'cat > 文件; chmod 600'` 的管道，不落在工作站磁盘上。`config/secrets/` 的规矩（只用 `sync-secrets.sh`、不手工拷 env 文件）管的是仓库托管的那些；这几个 `/etc/*.env` 不在 `config/secrets/` 里，是 edge 自己的，所以走主机间管道，这一点请用户确认。
- 2026-10-14 22:2x 条件变化（用户转来对方的说明）：新主机 stockpeer 已开免密 sudo，所以第一项阻塞（要 root）解除；对方明确要求 edge 等 10 月 15 日收盘后再搬进来（明天是新量化机第一次实盘验证），交易时段 09:15–15:10 不重启 Docker、不升级系统软件、不重启机器，动 nginx 前先说一声，对外开端口或加域名要找他们在控制台或 Cloudflare 上做，edge 的容器要设内存上限（新机 8 GB，量化开盘约 1.6 GB）。其余三个技术问题（rootless 网络、Hub 不可达、同时消费）不变。
- 2026-10-14 22:3x 密钥：新主机 authorized_keys 里 `stockpeer-self-tunnel`、`quant-edge-to-owner` 已改成只许端口转发（详见 W014 Log 22:3x）。
- 2026-10-14 22:5x 方案定了：新主机上已装好 `docker.io` 29.1.3（和 edge 同版本，系统级 `docker.service` 被禁用着、`/var/lib/docker` 在）；在它上面起系统级 Docker，与量化用的 rootless Docker 并存，edge 的 compose（`network_mode: host`）就能原样跑，所有 `127.0.0.1` 地址不用改（rootless 里 host 网络是 rootlesskit 的命名空间，容器到宿主 127.0.0.1 不通，已用临时容器实测：到宿主私网 IP 的 22 通、到 127.0.0.1 上的 15682 拒绝；加入量化的 compose 网络后能解析 `quant-research`）。路径、用户、systemd 单元都照 edge 的原样复制。PostgreSQL 用 apt 装 16（只带来 perl 的安全更新，没有 Docker、内核、libc、openssl）。库角色：`relay_n8n`（`n8n_relay` 的属主）、`quant_edge`、`quant_edge_export`；扩展 `uuid-ossp`、`pgcrypto`。
- 2026-10-14 22:5x 发现：工作站的 `config/secrets/.env.edge`（sha256 前 8 位 3cfbd837）和 edge 上 `/root/.secrets/edge/.env.edge`（110e13be）不一致，所以不能用工作站的衍生文件当来源；搬运以 edge 上实际在用的那份为准（主机间管道，不落盘）。edge 上 `.env` 是指向它的软链接。
- 2026-10-14 22:5x 这一步（装 PostgreSQL、启动系统级 Docker）被自动模式拦截，没有执行，也没有换别的方式绕过。已写成 `state/edge-move-A1.sh`（本机、被 git 忽略）等用户决定。
- 2026-10-14 23:3x 决定与执行：用户 22:3x「现在是闭盘时间，可以开始迁移了，n8n 执行历史也一起迁移过去」（库整库带，含执行历史）；A1（装 PostgreSQL、起系统级 Docker）由用户跑 `state/edge-move-A1.sh` 完成。edge 的出口带宽约 0.3 MB/s 且有重传，直连新主机也一样，所以公共镜像（n8n、runners）改从 DaoCloud 镜像站按 digest 拉（docker.n8n.io 会重定向到被墙的 Docker Hub），只从 edge 传自制的两个；数据库先全量再补差量。新主机上加了一把临时的传输钥匙（在 edge 上生成，私钥不出 edge，只能从 edge 的 IP 登录，只能跑 `tar-x`、`docker-load`、`psql <库名>` 三条白名单命令），传完要从 authorized_keys 里删除并删 `~/bin/edge-ingest.sh`。
- 2026-10-14 23:4x Codex 审计（`mcp__codex-teleai__codex`，只读，线程 `01a13b03-49b2-7bc3-ab83-d5232e42a271`）。24 条，我对着代码核实后的取舍：采纳并写进步骤文档的有写入者围栏（`feishu-relay/adapter/ledger.mjs:304-312` 只比较、不升代，所以新 ID 加手工升代）、归档与投递 worker 不受围栏约束（旧 adapter 必须先停）、WAL 文件要停服后复制、XHS 与 itougu 没有跨主机围栏、n8n 数据卷与加密密钥、五个工作流和热更新覆盖层回落到镜像内旧代码、15678 与量化 compose 的 `n8n` profile 冲突、监督进程三条隧道（18791 与 stock-reports 18081 不改）、发布工具和隔离测试需扩、failover 脚本表清单落后、`Persistent=true` 的补跑、PG 的先决条件。已过时的：第 1、2 条（A1 已完成）。第 3 条（密钥来源矛盾）如实保留为切换后的对账项。第 23 条（5680）：那是 runners 的健康端口，不是 5679，不预留。
- 2026-10-14 23:4x 反向依赖：留在 edge 上的服务（stock-reports、paper-*、codex-model-router）的配置和单元里没有出现被搬走的端口，也没有到这些端口的连接；18081 的 nginx 无来源限制，所以 n8n 搬走后从新主机调 `https://47.114.113.152:18081` 不会被来源限制挡住（P9 仍要实测）。

## Waiting on the user
- [ ] 2026-10-14 23:4x 确认切换：停 edge 上的 adapter、bridge、xhs、n8n、itougu，改工作站三条隧道，在新主机起栈（步骤文档“切换”一节）。为什么等：这一步会让飞书中继中断十几分钟并改变线上流量去向。之后：按步骤文档执行并核对，旧栈保持停止但完整，两个完整交易日后再清理。
- [ ] 2026-10-14 22:5x 在新主机上装 PostgreSQL 16 并启动系统级 Docker（`bash state/edge-move-A1.sh`，或给我这类命令的权限）。为什么等：自动模式拦截了生产主机上装包和起守护进程。 → 用户 22:3x 自己跑了 `state/edge-move-A1.sh`（2026-10-14）
- [x] 2026-10-14 17:5x 新主机 stockpeer 要 root：装 `python3-venv`（或我整套拷 edge 的 venv 就不用）、Postgres（或走容器）、tailscale 入网、nginx 站点。为什么等：`sudo` 要密码，我没有。 → 用户 22:2x 告知已开免密 sudo（对方 `/etc/sudoers.d/90-stockpeer`，2026-10-14）
- [ ] 2026-10-14 17:5x 数据库方案：n8n_relay 库 2.4 GB 搬法。选项：只搬业务表（不含 `execution_data` 1.5 GB 的历史执行）进新主机上的 Postgres 容器（镜像从 edge `docker save` 过去）。为什么等：丢不丢执行历史、要不要保留 baidu 归档队列 878 MB 是用户的取舍。
- [ ] 2026-10-14 17:5x 同机共用的风险是否接受，以及切换时刻：owner 与飞书中继同机后，owner 主机故障会同时带走中继；切换会有几分钟两边都不消费。为什么等：需要用户定。之后：写决策记录、改 `OWNER_MARKERS` 与 edge 脚本的主机默认值。

## Running

## Due

## Evidence

- `docs/work/probes/2026-10-14-edge47-inventory.out.txt`、`-2.out.txt`、`-3.out.txt`
- `docs/work/probes/2026-10-14-edge47-config-shapes.out.txt`（与 `.py`）
- `docs/work/probes/2026-10-14-newhost-readiness.out.txt`

## Open questions

- 工作站反向隧道 15678 落在 edge 的 sshd 上：搬后落到新主机哪个用户的 sshd（stockpeer 的 authorized_keys 里是否允许 `-R`）。
- itougu 的登录头（`itougu_auth.json`）是否绑定来源 IP；xhs cookie 在新主机出口 IP 下是否触发风控（B058 之前的 xhs-cookie 记忆：家 IP 的 cookie 到机房有过风控）。
- 飞书开放平台里用户 OAuth 回调地址（`FEISHU_USER_OAUTH_REDIRECT_URI`，值在 compose 的 `.env` 里，没有核对）是否写死 edge 地址。

## Resume here

先读本文件和三份只读清点。没有用户的答复前，不要在新主机上启动 adapter、bridge、xhs、n8n 或 itougu 的任何服务，也不要停 edge 上的服务。
