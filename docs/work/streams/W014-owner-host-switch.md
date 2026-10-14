# W014 owner 中转主机再换一台：做好切换的准备

- state: building
- holder: Claude Code 会话 4a420907，2026-10-14
- goal: owner 的中转主机换成另一台时，服务不重建镜像、按一份步骤切过去，仓库里只改一行地址；数据库和 Windows 的隧道不动
- plan: docs/OWNER_HOST_SWITCH.md
- branch: main
- head: b8cdbc13
- next: 等用户给出新主机的地址、SSH 端口和主机指纹，以及谁来装它的底子（Waiting on the user）。拿到之后按 `docs/OWNER_HOST_SWITCH.md` 走：先 `scripts/shared-peer/owner-host-switch.sh preflight stockpeer@<新主机>`（只读，会话可以跑；第一次连接要先把指纹加进 `~/.ssh/known_hosts`，这一步由用户做），把输出存进 `docs/work/probes/`；`copy --apply`、新主机上的两件事、切换、三处地址，都是用户执行、会话核对。`copy --apply` 还没有对真实的新主机跑过，第一次跑先看不带 `--apply` 的输出。
- updated: 2026-10-14

## Requirements

- 2026-10-14 12:05 「当前owner主机后续还需要更换为其他的，请你做好切换的准备。但是我们数据库这些windows和db-tunel都可以连通。只是owner更换」（主会话的理解：中转主机 118.178.133.37 还要再换；Windows 工作站上的库和它到中转主机的反向隧道由对方接到新主机；要准备的是我们这一侧：服务怎么搬过去、哪些地方指向旧主机）

## Plan

`docs/OWNER_HOST_SWITCH.md`（步骤 0–6 和回退）。没有单独的审查和批准：它是把 2026-10-13 那次手工迁移（BACKLOG B041）整理成步骤，加一个只读预检和一个只写新主机的搬运工具；真正的切换每一步都要用户执行。

## Steps

- [x] 清点现主机（只读）— `docs/work/probes/2026-10-14-owner-host-inventory.sh` 与 `.out.txt`
- [x] 地址收成一处 `scripts/shared-peer/owner-host.sh`；工具 `scripts/shared-peer/owner-host-switch.sh`（`inventory`、`preflight`、`copy`）；步骤文档；测试 — 3fa38658，合进 main b8cdbc13 — `python -m pytest scripts -q` → `385 passed, 3 skipped`；`node scripts/shared-peer/deploy-code-only.test.mjs` 退出 0；对真实主机 `release-sync-status.sh` → ALL CHECKS PASSED
- [ ] 拿到新主机后：预检
- [ ] 搬运、新主机自己的两件事、再预检到没有 MISSING
- [ ] 切换（收盘后）
- [ ] 三处地址（工作站、仓库、edge）
- [ ] 核对一个完整交易日

## Log

- 2026-10-14 12:12 做完准备的代码部分。清点到的要点（都在步骤文档的第一张表里）：镜像 1.12 GB，主机上 `~/wheelhouse` 是悬空的软链接，所以新主机只能搬镜像、不能在上面重建；发布目录树 83 MB；配置是一个 600 的文件，两个 `.env` 都是指向它的软链接，其中 `PEER_SSH_HOST` 是主机自己的内网地址（172.19.35.9），新主机上必须改；`authorized_keys` 有 5 把钥匙（Windows 反向隧道、操作员工作站两行、自连隧道、edge 到 owner）；三个 systemd 用户定时器；两个数据卷共 140 KB；服务用户没有免密 sudo。工具的 `inventory` 和 `preflight` 对现主机实际跑过（预检把现主机当作新主机跑，22 项全是 ok 或 note），`copy` 只跑过参数检查和“目标就是现主机时拒绝”，没有真的搬过。
- 2026-10-14 12:12 仓库里指向主机地址的地方原来有六处（四个发布和状态工具、发布计划、消息特征同步脚本），现在都读 `owner-host.sh`；`scripts/teacher_review_daily.py` 的默认地址没有动（主检出里有别的会话未提交的修改）。一条测试保证以后不会再多出第二处。

## Waiting on the user

- [ ] 2026-10-14 12:12 新主机的地址、SSH 端口、主机指纹；大概什么时候换；它的底子（用户 `stockpeer`、linger、rootless Docker、内存不少于 3 GB、三类公钥）由对方装还是我们装。为什么等：这些只有用户或对方知道；第一次连接新主机和往上面写都要用户执行。之后：会话跑只读预检并把缺的列出来。

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
