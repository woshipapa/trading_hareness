# W014 owner 中转主机再换一台：做好切换的准备

- state: released
- release: 2026-10-14 17:08 对方完成迁移；会话 17:3x–17:4x 切完工作站、仓库、edge 的三处地址（W014 Log）
- holder: Claude Code 会话 d39e7e11（接手 4a420907），2026-10-14
- goal: owner 的中转主机换成另一台时，服务不重建镜像、按一份步骤切过去，仓库里只改一行地址；数据库和 Windows 的隧道不动
- plan: docs/OWNER_HOST_SWITCH.md
- branch: main
- head: b8cdbc13
- next: 核对一个完整交易日：10 月 15 日 09:25 的竞价处置表、两条分钟快照每分钟一份（`docs/work/probes/2026-10-14-session-capture-gaps.py` 改日期）、16:15 之后晚间阶段回执；跑 `docs/OWNER_HOST_SWITCH.md` 7.0（盘中每 10 分钟一次 `docker stats`，存进 `docs/work/probes/`）。通过后状态改 `verified`。旧主机 118.178.133.37 保持停止、Docker 屏蔽，不要解除。
- updated: 2026-10-14

## Requirements

- 2026-10-14 12:05 「当前owner主机后续还需要更换为其他的，请你做好切换的准备。但是我们数据库这些windows和db-tunel都可以连通。只是owner更换」（主会话的理解：中转主机 118.178.133.37 还要再换；Windows 工作站上的库和它到中转主机的反向隧道由对方接到新主机；要准备的是我们这一侧：服务怎么搬过去、哪些地方指向旧主机）
- 2026-10-14 13:05 「我后续会升级为4c8g的服务器，200M得带宽上限，你可以看看对于我们的量化服务整体来说，那些硬编码部分或者有些功能可以升级加速」
- 2026-10-14 13:06 「请你把这些写到待更换后的推进计划」（写进了 `docs/OWNER_HOST_SWITCH.md` 第 7 步；待办 B062 是同一份清单的条目）
- 2026-10-14 16:50 「准备owner切换了，当前换为了4c8g，70G ssd。200mbps峰值带宽的」（主会话的理解：新主机的规格定了，4 核 8 GB、70 GB 盘、200 Mbps；地址、端口、指纹和谁装底子还没给）

## Plan

`docs/OWNER_HOST_SWITCH.md`（步骤 0–6 和回退）。没有单独的审查和批准：它是把 2026-10-13 那次手工迁移（BACKLOG B041）整理成步骤，加一个只读预检和一个只写新主机的搬运工具；真正的切换每一步都要用户执行。

## Steps

- [x] 清点现主机（只读）— `docs/work/probes/2026-10-14-owner-host-inventory.sh` 与 `.out.txt`
- [x] 地址收成一处 `scripts/shared-peer/owner-host.sh`；工具 `scripts/shared-peer/owner-host-switch.sh`（`inventory`、`preflight`、`copy`）；步骤文档；测试 — 3fa38658，合进 main b8cdbc13 — `python -m pytest scripts -q` → `385 passed, 3 skipped`；`node scripts/shared-peer/deploy-code-only.test.mjs` 退出 0；对真实主机 `release-sync-status.sh` → ALL CHECKS PASSED
- [x] 拿到新主机后：预检 — 搬运和新主机上的底子由对方（另一个阿里云账号下）在 2026-10-14 17:00–17:08 一次做完，没有用我们的 `copy`；会话用 `inventory` 和 `release-sync-status.sh` 核对（`docs/work/probes/2026-10-14-owner-host-inventory-new.out.txt`、`...-release-status-newhost-1.out.txt`：ALL CHECKS PASSED）
- [x] 搬运、新主机自己的两件事 — 同上，对方做的；新主机上 4 个容器 healthy、三个定时器 enabled、Windows 反向隧道的 15432/15433/15680/15681 都到了
- [x] 切换（收盘后）— 对方 17:00–17:08；旧主机已停止写入并屏蔽 Docker
- [x] 三处地址（工作站、仓库、edge）— 2026-10-14 17:3x–17:4x，见 Log
- [ ] 核对一个完整交易日
- [ ] 换机后的推进（`docs/OWNER_HOST_SWITCH.md` 第 7 步，B062）：7.0 先量一个完整交易日 → 7.1 逐行写库改批量 → 7.2 补回两个晚间阶段 → 7.3 数据库并发（先问对方库的连接上限）→ 7.4 晚间计算移出盘中进程（要方案）→ 7.5 公共数据源并发 → 7.6 镜像构建（先解决依赖包，B050）→ 7.7 顺带修的

## Log

- 2026-10-14 12:12 做完准备的代码部分。清点到的要点（都在步骤文档的第一张表里）：镜像 1.12 GB，主机上 `~/wheelhouse` 是悬空的软链接，所以新主机只能搬镜像、不能在上面重建；发布目录树 83 MB；配置是一个 600 的文件，两个 `.env` 都是指向它的软链接，其中 `PEER_SSH_HOST` 是主机自己的内网地址（172.19.35.9），新主机上必须改；`authorized_keys` 有 5 把钥匙（Windows 反向隧道、操作员工作站两行、自连隧道、edge 到 owner）；三个 systemd 用户定时器；两个数据卷共 140 KB；服务用户没有免密 sudo。工具的 `inventory` 和 `preflight` 对现主机实际跑过（预检把现主机当作新主机跑，22 项全是 ok 或 note），`copy` 只跑过参数检查和“目标就是现主机时拒绝”，没有真的搬过。
- 2026-10-14 12:12 仓库里指向主机地址的地方原来有六处（四个发布和状态工具、发布计划、消息特征同步脚本），现在都读 `owner-host.sh`；`scripts/teacher_review_daily.py` 的默认地址没有动（主检出里有别的会话未提交的修改）。一条测试保证以后不会再多出第二处。
- 2026-10-14 16:55 用户给出新主机规格（Requirements 16:50）。重新清点现主机（`docs/work/probes/2026-10-14-owner-host-inventory-1630.out.txt`，只读）：内存 3.5 GB（用了 918 MB）、盘 30 GB 用 7.2 GB（26%）、发布覆盖层已是 `owner-code-20261014-11`、镜像仍是 1.12 GB——所以 `copy` 搬的内容比 12:12 那次清点多了几次覆盖层发布，仍然很小；新主机的 70 GB 盘够用得多。两个 compose 文件里没有 `mem_limit`、`cpus` 一类的上限，8 GB 不需要改 compose 才能用上（调度容器的 129 MiB 是用量，不是上限）。200 Mbps 对我们几乎不是约束：db-tunnel 两小时进出各约 4.9 GB，平均不到 6 Mbps。
- 2026-10-14 13:06 按用户要求，把换到 4 核 8 GB、200 Mbps 之后的推进写进步骤文档第 7 步（先量、再按收益从大到小一项一项放开），Steps 加了对应的一行。依据是 13:03 现主机的一次盘中采样（`docs/work/probes/2026-10-14-owner-load-1306.out.txt`）和 FACTS F027、F029。
- 2026-10-14 17:3x 用户：「当前开始迁移吧」并转来对方的说明（量化云端 17:00–17:08 迁到另一个阿里云账号下的 116.62.30.225，旧主机停止写入并屏蔽 Docker，请不要解除；要改三项：`OWNER_PEER_HOST`、`LONGHU_SSH_HOST`、`.env.local` 的 `OWNER_PEER_SSH_HOST=172.26.62.126`）。
- 2026-10-14 17:37 会话做的：(1) 用 `ssh-keyscan` 独立取回新主机的 ed25519 指纹，与对方给的完全一致后写入工作站 `known_hosts`；(2) `config/secrets/.env.local` 改 `LONGHU_SSH_HOST`、`OWNER_PEER_SSH_HOST`（只这两个键；端口 22 和私钥不变），本地重新生成 `.env.owner`（只是本地文件，没有同步到任何主机；重新生成后 `PEER_SSH_HOST`、`PEER_SSH_PORT`、`PEER_SSH_HOST_KEY_ALIAS` 三个键变了，后两个是因为原来的衍生文件落后于 `.env.local`）；(3) 仓库 `scripts/shared-peer/owner-host.sh` 的默认地址、`scripts/teacher_review_daily.py` 的默认值、`test_release_unit_isolation.py` 的 `OWNER_MARKERS`，以及文档里“现在是哪台”的说法；`python -m pytest scripts -q` → 386 passed, 2 skipped；`node scripts/shared-peer/deploy-code-only.test.mjs` 退出 0。
- 2026-10-14 17:37 新主机核对（只读）：4 核、7.3 GB 内存（用了 1.0 GB）、69 GB 盘用 6.8 GB；覆盖层 `owner-code-20261014-11`；`/health` 两个端口都是 ok，提交 `7e9d61f3`，库版本 `20261010_ow0121`。
- 2026-10-14 17:3x edge：`quant-longhu-owner-tunnel.service` 的目标改成 `stockpeer@116.62.30.225`，并加了 `-o ConnectTimeout=15`（B060）；新主机指纹由 edge 自己再取一次核对后追加进 `/etc/quant-intraday-edge/known_hosts`；备份在 edge 的 `/root/quant-longhu-owner-tunnel.service.bak-20261014`、`/root/quant-intraday-edge-known_hosts.bak-20261014`；重启后单元 active、15682 有监听、`/health` ok。
- 2026-10-14 17:39 工作站：`launchctl kickstart -k gui/$(id -u)/com.papa.svc-supervisor`；本机 `owner-tunnel` 的 ssh 目标是 `stockpeer@116.62.30.225`，`127.0.0.1:15682` 的 `/health` 200，总结接口读到 `2026-10-14` 的读数（库在 Windows 上，没有动）。

## Waiting on the user
- [x] 2026-10-14 16:55 新主机的地址、SSH 端口、主机指纹，以及底子 → 用户 17:3x 给出：`116.62.30.225`，端口 22，指纹 `SHA256:X4ptjLwHVcvUe/3nCFdr5HGPy++vvz3oGTi6jWmHGfY`，内网 `172.26.62.126`，用户和公钥不变（2026-10-14）
- [ ] 2026-10-14 13:06 对方工作站上的库能承受多少并发连接（第 7.3 步要用）。为什么等：只有对方知道。之后：按这个数定两个容器的数据库线程和连接池上限。
- [x] 2026-10-14 12:12 新主机的地址、SSH 端口、主机指纹；大概什么时候换；它的底子（用户 `stockpeer`、linger、rootless Docker、内存不少于 3 GB、三类公钥）由对方装还是我们装。为什么等：这些只有用户或对方知道；第一次连接新主机和往上面写都要用户执行。之后：会话跑只读预检并把缺的列出来。 → 用户 17:3x 给出（见上一条，2026-10-14）

## Running

## Due

## Evidence

- 上一次迁移的全过程：`docs/work/BACKLOG.md` B041（2026-10-13）。
- 现主机的清点：`docs/work/probes/2026-10-14-owner-host-inventory.out.txt`。

## Open questions

- 新主机的内存：894 MB 的那台在 10 月 14 日开盘 6 分钟后失去响应（B058），现在这台是 3.5 GB；预检按 3 GB 卡。
- edge 的隧道单元没有握手超时（B060），换主机改它的地址时一并加上。
- 重建镜像所需的依赖包（wheelhouse）现主机上就没有；新主机上以后要重建镜像时从哪里来，没有定（B050）。

## Resume here

准备的代码已在 main（b8cdbc13）。接手的人先读 `docs/OWNER_HOST_SWITCH.md`，再看上面 Waiting on the user 有没有答复。只读的两个动作（`owner-host-switch.sh inventory`、`preflight <新主机>`）会话可以直接跑；其余每一步都是往主机上写或改流量去向，由用户执行。切换时旧主机的两个服务容器必须先停、三个定时器必须 disable（守护定时器会把停掉的服务拉起来），否则是两套采集往同一个库写。
