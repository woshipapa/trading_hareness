# 47 双机统一发布与同步手册

更新时间：2026-09-26。适用范围：`woshipapa/trading_hareness#2`（已合并三条同步分支，`e54cd2f` 及之后）以及之后的每一次发布。

本手册写给在**操作员工作站**上运行的本地 agent（Codex 或其他）。云端会话连不到 47 主机，也没有部署密钥；本手册就是完整的执行计划。按阶段顺序执行，每个阶段末尾都有**通过条件**，不满足就停止、回滚（第 12 节）并向用户报告，不要跳过或"先继续再说"。

目标有两个：

1. 把 47 edge 与 47 owner 同步到同一个 Git 提交 `X`，并用脚本证明它们一致；
2. 消除让两台机器无法统一发布的漂移，使以后每次发布都只需按第 13 节的清单执行。

---

## 1. 拓扑与组件

| 位置 | 组件 | 部署方式 | 版本核验 |
| --- | --- | --- | --- |
| **47 edge** `root@47.114.113.152`，密钥 `feishu_relay_edge_ed25519` | `feishu-relay-edge-adapter` 容器（`127.0.0.1:18300`） | GHCR 不可变镜像 `ghcr.io/woshipapa/trading-hareness-feishu-adapter:<sha>`，`scripts/deploy-feishu-relay-edge-release.sh` | `/health` 的 `build.git_sha`、`build.release`、`runtime_source` |
| | LarkAgentX bridge，systemd `larkagentx-group-relay.service`（`127.0.0.1:8090`） | 源码 overlay，`scripts/hotfix-feishu-relay-edge.sh`（bridge 目前**只有**这一条发布路径） | `/health` 的 `release`（overlay 发布 ID 含基准提交前 12 位） |
| | n8n `feishu-relay-edge-n8n`（`127.0.0.1:5678`）的 4 个 webhook 工作流 | `scripts/deploy-edge-relay-workflows.sh` | `scripts/verify-edge-relay-workflows.sh`（只读漂移比对） |
| | `stock-reports-import` watchdog | `scripts/install-edge-import-watchdog.sh` | systemd 状态 |
| | **已退役**：`quant-intraday-edge.service` 及其 timers | 不部署 | 必须保持 `inactive`/`disabled` |
| **47 owner**（Longhu 主机 / lightServer1）`stockpeer@118.178.133.37 -p 22`（2026-10-13 迁到这台；此前是 `47.110.79.189 -p 3535`，那台上的量化容器已停用，不要恢复），密钥 `stockpeer_ed25519`，rootless Docker，compose 项目 `trading-hareness-peer` | `db-tunnel` | release 归档 + `scripts/shared-peer/activate-peer-release.sh` + compose | 容器健康 |
| | `quant-research`（`127.0.0.1:15682`，`QUANT_RUNTIME_PROFILE=intraday_edge`，唯一盘中写入者） | 同上，镜像在主机上用 wheelhouse 构建 | `/health` 的 `build.git_sha`；容器 profile |
| | `quant-research-scheduler`（`127.0.0.1:15683`，`research`） | 同上，复用同一镜像 | `/health` 的 `build.git_sha` |
| | systemd `--user`：`peer-session-guard-{heal,preopen}.timer`、`peer-daily-backfill.timer` | 从 release 目录安装单元文件 | `systemctl --user list-timers` |
| **owner Windows 工作站**（本地） | G: PostgreSQL `127.0.0.1:55432`，库 `trading_hareness`，**唯一数据写入库**；Alembic 迁移在这里执行 | `alembic upgrade head` | `quant.alembic_version` |
| | 本地 quant API（`127.0.0.1:5681`，Longhu 授权网关），代码在 `F:\AIWorkflow\trading_hareness` | `git checkout` + `scripts/windows/restart-local-quant-api.ps1` | `/health` 的 `build.git_sha` |
| | 到 lightServer 的反向隧道（`15432` 库、`15681` API） | 计划任务 | `scripts/shared-peer/verify-shared-runtime.ps1` |

路径说明：同步分支把飞书中继整体移到了 `feishu-relay/`。adapter 源码在 `feishu-relay/adapter/`，bridge 在 `feishu-relay/bridge/`，edge 部署文件在 `feishu-relay/deploy/edge/`，edge 脚本在 `feishu-relay/scripts/edge/`。上表和下文里的 `scripts/*-feishu-relay-edge*.sh`、`scripts/*-edge-relay-workflows.sh`、`scripts/install-edge-import-watchdog.sh` 仍然可以直接用，它们只是转发到 `feishu-relay/scripts/edge/` 的入口。

owner 容器设置了 `QUANT_SKIP_MIGRATIONS=true`，服务启动时只检查几张基础表是否存在，**不检查迁移版本**。因此如果先换代码、后迁移，服务照样能启动，但新代码用到的新列/新表会在运行时报错。迁移必须在换代码之前完成。

---

## 2. 铁律（违反任何一条就停止）

1. **不要**在 edge 上运行 `scripts/deploy-intraday-edge-release.sh`，**不要**启用 `quant-intraday-edge.*` 单元。edge 上的量化采集已于 2026-09-05 退役（`deploy/intraday-edge/README.md`、`docs/EDGE_FUTURE_WRITER_CUTOVER.md`），再启用会出现第二个盘中写入者。
2. owner 上所有 `docker compose` 命令都必须**同时**带 `-f compose.yaml -f compose.intraday-owner.yaml`。只用前者会把主容器悄悄降级成 `research` profile，容器仍显示 healthy，但盘中采集全部停止。
3. **先迁移数据库，后换代码。永远不要执行 `alembic downgrade`**（会删列删表，丢数据）。本仓库的迁移都是新增型，旧代码可以在新 schema 上继续运行。
4. 只发布已经合并进 `origin/main` 的提交。例外情况见第 6 节，需要用户明确同意。
5. 不提交、不打印任何密钥：`.env`、`intraday-secrets.env`、`runtime.env`、`secrets.env`、`relay.env`、OAuth 状态。不要在任何主机上执行 `git add -A`，提交前逐个文件 `git add <path>`，并用 `git diff --cached` 检查。
6. 交易时段不发布 owner。交易日 08:30–11:30 和 13:00–15:10 不要重启 owner 的 `quant-research` 或 Windows API；18:45–22:05 不要重启 owner 的 `quant-research-scheduler`（盘后调度在跑）。feishu-relay（edge adapter 和 bridge）是独立的发布单元，不受这些窗口限制，任何时间都可以发布（2026-10-12 操作者决定，见 `docs/decisions/0014-feishu-relay-release-independent-of-owner-windows.md`）；盘中发布会让 WebSocket 断开约 15 秒、控制台请求失败几秒。午间休市 11:31–12:59 可以发布（2026-10-12 操作者决定），发布要在 13:00 前完成并核验。安全窗口：交易日 22:10 至次日 08:00，或周末。
   这些时间窗写在 `config/release-windows.json` 里，由 `scripts/release_window.py` 检查。全量发布、code-only 发布、edge 镜像发布和 edge 热更新在重启之前都会先问它：落在窗口内就拒绝（退出码 3）。edge 的对象 `edge-adapter` 登记在该文件的 `unrestricted` 里，检查总是放行。经批准的紧急操作，把要越过的那个窗口名写进 `RELEASE_WINDOW_OVERRIDE`；检查器会放行，同时打印警告。
7. 不删除旧的 release 目录、旧镜像、旧 overlay，回滚依赖它们。
8. 任何通过条件不满足时停止，按第 12 节回滚，再向用户报告实际输出。

---

## 3. 前置条件

- 工具：`git`、`ssh`、`scp`、`rsync`、`python3`、`node` 22、`npm`、`jq`、`docker`（本机拉取镜像回退时使用）、`shasum`、`diff`。Windows 步骤需要 PowerShell 7（`pwsh`）。
- 密钥（不要拷贝进仓库）：

  ```bash
  export RELAY_EDGE_SSH_KEY=~/.ssh/feishu_relay_edge_ed25519   # 47 edge root
  export OWNER_PEER_SSH_KEY=~/.ssh/stockpeer_ed25519           # 47 owner stockpeer，端口 22
  ```

  脚本默认路径是 `/Users/papa/.ssh/...`，在其他机器上必须 export 上面两个变量。
- 本地仓库干净，并已同步远端：`git fetch origin --tags && git status --porcelain` 输出为空。
- GitHub 推送标签的权限（`edge-*` 标签会触发镜像构建）。
- 能登录 owner Windows 工作站执行 PowerShell（迁移、Windows API）。

---

## 4. 阶段 A：只读盘点（每次发布前必做）

```bash
mkdir -p ~/release-sync-logs
scripts/release-sync-status.sh --sha origin/main | tee ~/release-sync-logs/$(date +%Y%m%d-%H%M)-pre.txt
```

`scripts/release-sync-status.sh` 是只读的：只对回环 `/health` 执行 `curl`、执行 `readlink`、`grep` 非密钥的发布字段、查询 `systemctl`，以及一条 `SELECT version_num FROM quant.alembic_version`。它会逐项打印 PASS/FAIL，全部通过时退出码为 0。

**记录回滚点**：把盘点输出里的以下三项抄进发布记录（附录 C）：

- edge `adapter git_sha` / `adapter release`
- owner `~/trading_hareness` 的指向
- owner `alembic revision`

首次同步前，预计会出现这些 FAIL，它们正是第 5 节要消除的漂移：

- edge `adapter release` 是 `hotfix-…`，`runtime_source` 是 `source-overlay`，bridge `release` 含 `-dirty`；
- owner 的 `git_sha` 是 `unknown`（旧镜像构建时没有注入版本号，本次已修）；
- owner `alembic revision` 是仓库里不存在的另一条迁移线的末端（2026-09-26 巡检实测为 `20260923_0117`）；
- owner `intraday-secrets.env` 可能缺失。

补充盘点（阶段 B 需要用到）：

```bash
E=root@47.114.113.152
ssh -i "$RELAY_EDGE_SSH_KEY" $E 'readlink /opt/feishu-relay-edge/hotfix/current; cat /opt/feishu-relay-edge/hotfix/current/.base-git-sha'
ssh -i "$OWNER_PEER_SSH_KEY" -p 22 stockpeer@118.178.133.37 \
  'ls -1 ~/.local/share/trading-hareness/releases/; ls ~/.local/share/trading-hareness/releases/*/trading_hareness/quant-service/migrations/versions/ 2>/dev/null | grep -E "_0(09[5-9]|1[0-9][0-9])_" | sort -u'
```

在 Windows 工作站上：

```powershell
cd F:\AIWorkflow\trading_hareness
git status --porcelain
Get-ChildItem quant-service\migrations\versions | Where-Object Name -match '_0(09[5-9]|1[0-9][0-9])_'
```

通过条件：拿到三台机器的盘点输出，并已记录回滚点。

---

## 5. 阶段 B：把漂移收回 Git（首次同步必做；以后只要存在未提交的 hotfix 就要做）

### B0. 先把本机从未推送的提交推到 GitHub（2026-09-26 巡检发现；**已完成**）

2026-09-26 已完成：`sync/owner-runtime-d5c359d`（`d5c359d`）、`sync/edge-runtime-6271d88`（`6271d88`）、`sync/local-worktree-20260926`（`b743d7e`，包含前两者和本地 74 个未提交文件）都已推送，并已合并进 PR #2（合并提交 `269c5c2`）。以后再出现未推送的运行代码时，按下面的原步骤处理。

2026-09-26 的巡检结果：owner 运行的是 `d5c359d`，edge overlay 基于 `6271d88`，owner 库版本为 `20260923_0117`。这三样在 GitHub 上**都不存在**，任何分支、标签和 PR 里都没有。它们只存在于操作员工作站的本地提交里，以及主机上已部署的代码中。本地还有大量未提交改动。

所以第一步是把它们原样推到 GitHub 的独立分支上，其他所有步骤都在这之后。**不要**改写历史，**不要**推到 `main`，**不要**在这一步合并任何东西：

```bash
git branch -a --contains d5c359d; git branch -a --contains 6271d88     # 看它们在哪个本地分支
git log --oneline origin/main..d5c359d | wc -l                         # 相对 origin/main 领先多少提交
git ls-tree -r --name-only d5c359d -- quant-service/migrations/versions | grep -E "_0(09[5-9]|1[0-9][0-9])_"
git push origin d5c359d:refs/heads/sync/owner-d5c359d
git push origin 6271d88:refs/heads/sync/edge-6271d88
```

本地未提交的改动：先逐个文件检查，确认没有 `.env`、`*-secrets.env`、`runtime.env`、token 或密码，再提交到一个单独分支（例如 `sync/local-worktree-20260926`）并推送。拿不准的文件不要提交，列出来报告用户。

推送完成后，由 PR #2 的维护方把这些分支与 PR #2 做三方合并：解决冲突，补上 B2 的合并迁移，跑通测试和 CI 后合入 `main`。这样 `origin/main` 才会同时包含主机上正在运行的代码和 PR #2 的改动。在此之前，任何发布都会造成代码回退。

### B1. edge 上的 overlay 代码（内容已随同步分支入库，发布前只需做一次比对）

2026-09-26 更正：下面列出的 overlay 内容已经在 `sync/local-worktree-20260926` 里，只是换到了新路径，现已随 PR #2 合并：`feishu-relay/bridge/larkagentx_image_property.py`、`feishu-relay/deploy/edge/{docker-compose.yml,larkagentx-bridge-entrypoint.sh,larkagentx-group-relay-hotfix.conf}`，以及 `feishu-relay/adapter/index.mjs` 里的 `POST /internal/larkagentx/gap-repair` 和 `FEISHU_ADAPTER_HOTFIX_ENABLED`/`runtime_source` 处理。之前说"仓库任何分支都没有"，是因为只查了旧路径，这个结论是错的。

现在剩下的工作是：先按第 1 步取回 edge 正在运行的 overlay，再与 PR #2 合并后的 `feishu-relay/` 做 `diff -r`。只有 edge 上比仓库更新的内容才需要走第 2 步，拿不准就报告用户。第 3、4 步照做，作为发布前的校验。

以下是原始记录。当时的 overlay 里有这些内容，而旧路径下找不到：

- `integrations/larkagentx/larkagentx_image_property.py`：仓库里的 `bridge.py` 第 33 行就 `import` 了它，所以仓库版本的 bridge 无法启动；
- `deploy/feishu-relay-edge/larkagentx-bridge-entrypoint.sh`、`deploy/feishu-relay-edge/larkagentx-group-relay-hotfix.conf`：`hotfix-feishu-relay-edge.sh` 要求这两个文件存在；
- `deploy/feishu-relay-edge/docker-compose.yml` 中的 overlay 挂载，以及 adapter 对 `FEISHU_ADAPTER_HOTFIX_ENABLED`/`runtime_source` 的处理；
- `POST /internal/larkagentx/gap-repair` 路由（bridge 重连补读调用它，仓库版本的 adapter 返回 404）。

如果不先提交这些内容，用仓库代码做任何正式发布都会把 LarkAgentX 补读功能覆盖掉。

1. 从 edge 取回正在运行的版本（它是运行事实的来源，只读拷贝）：

   ```bash
   E=root@47.114.113.152; K="$RELAY_EDGE_SSH_KEY"; D=/tmp/edge-drift-$(date +%Y%m%d)
   mkdir -p "$D"
   rsync -a -e "ssh -i $K" "$E:/opt/feishu-relay-edge/hotfix/current/" "$D/overlay/"
   scp -i "$K" "$E:/opt/feishu-relay-edge/docker-compose.yml" "$D/"
   scp -i "$K" "$E:/opt/larkagentx/bridge-entrypoint.sh" "$D/"
   scp -i "$K" "$E:/etc/systemd/system/larkagentx-group-relay.service.d/10-hotfix-entrypoint.conf" "$D/"
   grep -rIlE 'SECRET|TOKEN|PASSWORD|app_secret|Bearer ' "$D" && echo "发现疑似密钥，停止并报告" || true
   ```

   overlay 目录包含 `adapter/`、`bridge/`、`frontend-dist/`、`source-registry.json`、`ops/` 和 `.base-git-sha`，其中不应该有密钥。如果上面的 grep 命中了真正的密钥，停止并报告。

2. 以 overlay 记录的基准提交为起点新建分支，把 overlay 覆盖上去，再合并 `origin/main`，让 Git 做三方合并。PR #2 改过 `group-relay.mjs`、`summary-listener.mjs`、`index.mjs`，必须用合并而不是直接覆盖：

   ```bash
   BASE=$(cat "$D/overlay/.base-git-sha")
   git switch -c sync/edge-overlay-$(date +%Y%m%d) "$BASE"
   rsync -a --exclude node_modules --exclude '*.test.mjs' "$D/overlay/adapter/" feishu-relay/adapter/
   for f in "$D"/overlay/bridge/*.py; do cp "$f" feishu-relay/bridge/; done
   cp "$D/overlay/source-registry.json" feishu-relay/config/source-registry.json
   cp "$D/docker-compose.yml" feishu-relay/deploy/edge/docker-compose.yml
   cp "$D/overlay/ops/larkagentx-bridge-entrypoint.sh" feishu-relay/deploy/edge/
   cp "$D/overlay/ops/larkagentx-group-relay-hotfix.conf" feishu-relay/deploy/edge/
   git status --short          # 逐个检查
   git add <逐个文件>; git commit -m "chore: capture the edge's running overlay into Git"
   git merge origin/main       # 解决冲突时两边的行为都要保留
   ```

   如果操作员工作站上还有更新的、未提交的 hotfix 工作树（`git status` 能看到这些文件），拿它与 `$D` 对比。取两者中较新的一份，拿不准就报告用户。

3. 校验：

   ```bash
   (cd feishu-relay/adapter && npm install --no-audit --no-fund && node --test *.test.mjs)
   python3 -m unittest discover -s feishu-relay/bridge -p 'test_*.py'   # 缺 larkx 依赖时部分测试会跳过
   node feishu-relay/scripts/edge/deploy-feishu-relay-edge-release.test.mjs
   scripts/hotfix-feishu-relay-edge.sh      # 只做演练；overlay_release 不得含 -dirty
   ```

4. 确认 overlay 开关语义与发布脚本一致：`deploy-feishu-relay-edge-release.sh` 会写入 `FEISHU_ADAPTER_HOTFIX_ENABLED=false`，并在 `/health` 仍报告 `"runtime_source":"source-overlay"` 时拒绝发布。用 `grep -rn FEISHU_ADAPTER_HOTFIX feishu-relay/adapter feishu-relay/deploy/edge` 确认（2026-09-26 已核对：compose 读的就是这个键）刚提交的 compose/adapter 读的是这个键。如果实际用的是别的键，就修改发布脚本及其测试，让它关掉真正生效的开关。

5. 推送分支、开 PR，CI 通过后合并（CI 现状见第 6 节）。

### B2. owner 库的迁移分叉（实测 `20260923_0117`）

2026-09-26 合并后的仓库迁移链只有一条，唯一 head 是 `20260926_srg0001`：

`… 20260906_0094 … 20260918_ds0001 → 20260919_ds0002 → ds0003 → ds0004 → ds0005 → 20260926_pit0001 → 20260926_t1s0001 → 20260926_trl0001 → 20260926_srg0001`

（`pit0001` 原本接在 `ds0001` 后面，合并时改为接在 `ds0005` 后面，所以没有额外的合并迁移。它还没有在任何共享库上执行过。）

`20260923_0117` 的源码在 Mac、owner 主机历史 release、owner 镜像、Git 对象和可用部署归档中均未找到。用户已明确决定不再追溯这条旧迁移源码，因此本次采用仓库中的显式空版本标记和合并迁移。该标记只收敛 Alembic 版本，不声称恢复未知 DDL；新版本需要的表和列仍由仓库中的后续迁移创建，并必须通过 schema 回读确认。

现状：owner 库的 `quant.alembic_version` 记录的是另一条迁移线（0095 起，2026-09-26 实测末端为 `20260923_0117`；以实际查询结果为准，下文用 `<owner_head>` 表示）的末端，本仓库没有这些迁移文件。在这种状态下执行 `alembic upgrade head` 会报 `Can't locate revision identified by '<owner_head>'`。本仓库以前处理过同样的情况：`20260902_0089_legacy_owner_bridge.py` 是一个空的版本标记，`20260905_0093_merge_legacy_owner.py` 是合并迁移。

1. 在 Windows 工作站上读取实际版本。先按第 7 节的方式把 `runtime.env` 加载进当前进程（`PGPASSWORD` 取自其中），再执行：

   ```powershell
   $psql = (Get-ChildItem G:\StockPlatform\runtime -Recurse -Filter psql.exe | Select-Object -First 1).FullName
   & $psql -h 127.0.0.1 -p 55432 -U quant_app -d trading_hareness -tAc "SELECT version_num FROM quant.alembic_version"
   ```

2. 找回这条迁移线的原始迁移文件。首选来源是 B0 推送的 `sync/owner-d5c359d` 分支（owner 正在运行的代码）。其次是 Windows 工作站 `F:\AIWorkflow\trading_hareness` 里未跟踪的文件、owner 主机的历史 release 目录（阶段 A 的补充盘点）。
3. **如果找到了**：新建分支 `sync/owner-migration-lineage-<date>`。
   - 把这些文件原样加入 `quant-service/migrations/versions/`。
   - 核对它们的 `down_revision` 链能接到仓库已有的某个版本（通常是 `20260906_0094`）。
   - 新增一个空的合并迁移，例如 `20260927_mrg0001`，`down_revision = ("<owner_head>", "<当前仓库 head>")`，写法照抄 `20260905_0093`。
   - 检查这条迁移线上的各个迁移与本仓库 `20260918_ds0001` 至 `20260926_srg0001` 是否创建了同名对象。本仓库这些迁移都用了 `IF NOT EXISTS`、`to_regclass` 判断或 `ON CONFLICT`，重复执行是安全的，但对方的写法需要逐个确认。
4. **找不到**（本次已得到用户明确同意）：
   - 新增空的版本标记 `<owner_head>`，`down_revision = "20260906_0094"`，写法照抄 `0089`；本次实际文件为 `20260926_0117_legacy_owner_bridge.py`；
   - 新增 `20260926_own0001_owner_schema_gap.py`，合并 `20260923_0117` 与 `20260919_ds0002`，幂等补建实测缺少的 `research_model_registry`；`ds0003` 从该修复点继续。
   - `20260926_mrg0001_owner_lineage.py` 再合并 `20260926_own0001` 与当前仓库链 `20260926_srg0001`。
   - 然后用 `pg_dump --schema-only -n quant` 导出 owner 库的 schema，与"空库迁移到仓库 head"导出的 schema 做 diff。只存在于 owner 库的对象，要补写成真正的新增迁移（`IF NOT EXISTS`），这样新环境才能和 owner 一致。
   - 这一步没做完之前，在发布记录里注明"schema 差异未收敛"。本次发布记录还必须注明：旧 0117 的未知 DDL 未恢复，空标记只收敛版本线。
5. 验证：
   - 在一个空库上执行 `node feishu-relay/adapter/initialize-ledger.mjs` 和 `python quant-service/database_bootstrap.py`，必须能迁移到新的唯一 head。
   - 把 owner 的 schema-only dump 恢复到一个临时库，执行 `alembic upgrade head`，必须成功。
   - 用 `python3 - <<'PY'` 解析 `migrations/versions`，确认只有一个 head（`scripts/release-sync-status.sh` 内置的就是这套逻辑）。
6. 推送、开 PR，合并。

### B3. 其他工作站的未提交改动

对每个用于发布的检出目录（Mac 仓库、Windows `F:\AIWorkflow\trading_hareness`）执行 `git status --porcelain`。有未提交的内容就拿去和 `origin/main` 比对：已经在 main 里的丢弃；需要保留的走 PR；拿不准的报告用户。**不要**把 `.env` 或 `*-secrets.env` 加进提交，`.gitignore` 现在已经忽略了 `intraday-secrets.env`。

**B 阶段通过条件**：edge overlay 的内容和 owner 迁移线都已进入 `origin/main`；每个发布用的检出目录 `git status --porcelain` 都为空；owner 的 schema 回读通过。旧 `20260923_0117` 的未知 DDL 不作为本次发布的完成条件，但必须保留在发布记录中。

---

## 6. 阶段 C：合并并确定发布 SHA

1. PR #2 的 CI 一直是红的，原因在 `main` 的 CI 配置（见 PR 评论 `#issuecomment-5842937183`）。附录 A 的修复已经按用户要求加进 PR #2（`1daae6e`）。合并同步分支后（`e54cd2f`），CI 在 2277 个后端测试上只剩 2 个依赖真实行情数据的测试失败。按用户 2026-09-26 的决定，这 2 个测试改为只在 `QUANT_REAL_DATA_TESTS=1` 时运行（附录 A 第 3 项）。**在 CI 仍为红色时合并，必须先得到用户明确同意。**

   `verify-api-contract.mjs` 和 `api:check` 读的是**正在运行的**服务的 OpenAPI 文档。必须先用待发布的检出启动服务（例如在该检出里执行 `docker compose up -d --build postgres quant-research`），再做这两项检查。本机 5681 上原有的旧服务不能用来检查：拿它校验，得到的是旧代码的结果（例如 180 个 operations，而 PR #2 是 194 个）；拿它执行 `api:generate`，会删掉 PR 新增的路由类型。

   注意：同步分支自带的测试以前是在旧镜像的容器里跑的（`docker compose exec quant-research …`），所以当时报告的 "1874 tests OK" 验证的是旧代码。以后必须在当前检出上运行，例如 `cd quant-service && python -m unittest discover -s tests -q`，或者先重建镜像再在容器里跑。
2. PR #2 和阶段 B 的 PR 全部合并后：

   ```bash
   git fetch origin --tags
   X=$(git rev-parse origin/main)
   L=sync-$(date +%Y%m%d)-1        # 发布标签，只能包含 A-Za-z0-9._-
   ```

3. 构建 edge 镜像：推送一个指向 `X` 的 `edge-*` 标签。`Release edge images` 工作流会发布 `ghcr.io/woshipapa/trading-hareness-{quant,feishu-adapter}:$X`。

   ```bash
   git tag "edge-$(date +%Y.%m.%d)-sync" "$X"
   git push origin "edge-$(date +%Y.%m.%d)-sync"
   # 等 GitHub Actions 的 "Release edge images" 成功
   docker manifest inspect "ghcr.io/woshipapa/trading-hareness-feishu-adapter:$X" >/dev/null && echo image-ok
   ```

**通过条件**：`X` 已在 `origin/main`；`main` 在 `X` 上的 CI 为绿色，或用户明确豁免；GHCR 镜像已存在。

---

## 7. 阶段 D：数据库迁移（Windows 工作站，必须先于任何代码切换）

### D0. 停止点：先确认 owner 接受哪些 DDL（2026-09-26 新增）

合并同步分支之后，本仓库从 `20260918_ds0001` 到 head 之间有 8 个迁移：

| 迁移 | 内容 | 本次发布的代码是否依赖 |
| --- | --- | --- |
| `20260919_ds0002` | 新建 `replay_readiness_daily_coverage` 投影表 | 是：全市场 controls 同步会刷新它 |
| `20260919_ds0003` | 新建 `research_model_trials` | 仅离线训练 worker |
| `20260919_ds0004` | 给行情/因子大表**加列加约束**（`adjustment_state` 等），`_cold` 孪生表 | 否 |
| `20260919_ds0005` | 上述列上的 guard 索引 | 否 |
| `20260926_pit0001` | `instrument_lifecycle_evidence` 上的部分索引 | 否（只影响速度） |
| `20260926_t1s0001` | 4 张结果表新增 T+1 结算列 | **是**：结果重算会写这些列，缺列就报错 |
| `20260926_trl0001` | 新建 `research_trials` | **是**：因子评估和 `/api/v1/research/trials` |
| `20260926_srg0001` | 3 条 disabled、权重 0 的策略登记行 | 否（缺行等同 disabled） |

`docs/archive/PLAN_COMPLETION_MATRIX.md` 的 2026-09-20 owner clarification 写明：**owner 不会为 peer 执行 ds0004/ds0005 的 DDL**。所以在 owner 库上直接 `alembic upgrade head`，会把 owner 已经拒绝的 DDL 一起执行，而且 ds0004 要改写几张大表。

**决定（2026-09-26）：用户选择第 1 项**，owner 执行全部 8 个迁移。前提不变：必须先完成 B2，也就是从 Windows 找回 `20260923_0117` 的源码，让 `<owner_head>` 能接上本仓库的链。在此之前不要执行 `upgrade head`。第 2 项保留，作为以后同类情况的备选。

两个选项：

1. owner 同意执行全部 8 个迁移：按下面的原步骤 `upgrade head`（先完成 B2，让 `<owner_head>` 能接上本仓库的链）。
2. owner 只执行本次发布需要的迁移：先生成 SQL 交给 owner 审阅，不连接数据库：

   ```powershell
   ..\.venv\Scripts\python.exe -m alembic -c alembic.ini upgrade 20260919_ds0005:20260926_srg0001 --sql > G:\StockPlatform\backups\ddl-pit0001-srg0001.sql
   ```

   这份 SQL 只包含 `pit0001`、`t1s0001`、`trl0001`、`srg0001`。`ds0002` 是否执行要单独确认：如果 owner 库里已经有 `quant.replay_readiness_daily_coverage`（`SELECT to_regclass('quant.replay_readiness_daily_coverage')` 不为空），就不需要。生成的 SQL 里每一步都带一条 `UPDATE quant.alembic_version … WHERE version_num='<上一步>'`；owner 库的版本是 `<owner_head>`，这些 UPDATE 不会匹配任何行，也就不会改动版本记录。执行后 `quant.alembic_version` 该怎样记录，由用户和 owner 决定。

无论选哪一项，都必须等 `t1s0001` 的列和 `trl0001` 的表在 owner 库里存在之后，才能进入阶段 F。可以用下面的查询核对，应返回 `28|t`（4 张结果表 × 7 列，外加试验表）：

```sql
SELECT count(*) FILTER (WHERE table_name IN ('outcomes','post_close_strategy_candidate_outcomes',
                                             'ten_day_leader_rotation_candidate_outcomes','strategy_daily_candidate_outcomes')
                          AND column_name IN ('exit_date','net_return','benchmark_key','sessions_held',
                                              'exit_rolled_sessions','price_basis','settlement_version')),
       to_regclass('quant.research_trials') IS NOT NULL
  FROM information_schema.columns WHERE table_schema='quant';
```

以下是原步骤（选第 1 项时执行）。

```powershell
cd F:\AIWorkflow\trading_hareness
git fetch origin
if (git status --porcelain) { throw 'worktree is dirty' }
git checkout --detach <X>

# 只把 runtime.env 加载进当前进程，不打印
foreach ($line in [IO.File]::ReadAllLines('G:\StockPlatform\config\runtime.env', [Text.Encoding]::UTF8)) {
  if ($line -match '^([A-Za-z_][A-Za-z0-9_]*)=(.*)$') { [Environment]::SetEnvironmentVariable($Matches[1], $Matches[2], 'Process') }
}
cd quant-service
..\.venv\Scripts\python.exe -m pip install -r requirements.txt   # 仅当 requirements.txt 在上次发布后有改动时执行
..\.venv\Scripts\python.exe -m alembic -c alembic.ini current
..\.venv\Scripts\python.exe -m alembic -c alembic.ini heads        # 必须只有一个 head
```

迁移前备份。先找到 `pg_dump.exe`：

```powershell
$pgdump = (Get-ChildItem G:\StockPlatform\runtime -Recurse -Filter pg_dump.exe | Select-Object -First 1).FullName
New-Item -ItemType Directory -Force G:\StockPlatform\backups | Out-Null
& $pgdump -h 127.0.0.1 -p 55432 -U quant_app -d trading_hareness --schema-only -f "G:\StockPlatform\backups\schema-before-<X>.sql"
```

确认最近 24 小时内有一份完整备份。没有的话，再用 `-Fc -f G:\StockPlatform\backups\full-before-<X>.dump` 做一份全量备份。库较大，这一步耗时较长，也要放在安全窗口内执行。

然后迁移：

```powershell
..\.venv\Scripts\python.exe -m alembic -c alembic.ini upgrade head
..\.venv\Scripts\python.exe -m alembic -c alembic.ini current     # 必须等于 heads 的输出
```

迁移清单见 D0 的表。`20260918_ds0001` 此前已在 owner 上手工执行过，其中的插入语句是幂等的。所有迁移都是新增型的，旧代码不受影响。

**通过条件**：`alembic current` 等于唯一的 head；出错时不要重试"半截"迁移，保存完整输出并报告。PostgreSQL 的 DDL 是事务性的，失败的那个版本会整体回滚。

阶段 F 的 `deploy-full-release.sh` 会从运行中服务的 `/health` 读库版本，库落后于发布的 head 时拒绝切代码，所以漏掉这一步不会被静默放过。要看缺哪些迁移：`python3 quant-service/scripts/migration_lineage.py quant-service/migrations/versions --pending <owner 库版本>`。

---

## 8. 阶段 E：部署 47 edge

顺序不能颠倒。`hotfix-feishu-relay-edge.sh` 会拒绝 `package.json` 与当前运行镜像不同的 overlay，所以必须先把镜像换成 `X`。

```bash
git switch --detach "$X"; test -z "$(git status --porcelain)"

# E1 固定镜像：关闭 adapter overlay，并校验 /health 的 git_sha、release 和 runtime_source
scripts/deploy-feishu-relay-edge-release.sh "$X" "$L"            # 演练
scripts/deploy-feishu-relay-edge-release.sh "$X" "$L" --apply

# E2 LarkAgentX bridge：用干净的 X 生成 overlay（发布 ID 不得含 -dirty）
scripts/hotfix-feishu-relay-edge.sh                              # 演练，检查 overlay_release
scripts/hotfix-feishu-relay-edge.sh --apply
#    这一步会把 adapter 也切到同样是 X 代码的 overlay 上，所以接着执行 E3：

# E3 再次固定 adapter 镜像（bridge 保持在 X 的 overlay 上）
scripts/deploy-feishu-relay-edge-release.sh "$X" "$L" --apply

# E4 n8n 工作流：先做漂移比对；有漂移或工作流在两次发布之间有改动时才发布
scripts/verify-edge-relay-workflows.sh || scripts/deploy-edge-relay-workflows.sh "$X" --apply

# E5 watchdog：只有 feishu-relay/deploy/edge/stock-reports-import-watchdog.* 有改动时才执行
# scripts/install-edge-import-watchdog.sh --apply
```

说明：

- E2/E3 是 bridge 没有独立发布路径的临时做法。建议在阶段 B 的 PR 里给 `hotfix-feishu-relay-edge.sh` 加一个只更新 bridge、不动 adapter 的 `--bridge-only` 模式，之后就可以省掉 E3。
- 发布脚本在 GHCR 拉取卡住时，会自动改为本机拉取、再 `docker save | ssh docker load`，不需要人工处理。

**通过条件**：

```bash
scripts/release-sync-status.sh --sha "$X" --skip-owner    # ALL CHECKS PASSED
ssh -i "$RELAY_EDGE_SSH_KEY" root@47.114.113.152 'curl -fsS http://127.0.0.1:18300/api/group-relay/status | head -c 600'
```

---

## 9. 阶段 F：部署 47 owner

在安全窗口内执行（第 2 节第 6 条）。

**先看计划**（只读）：`scripts/release plan <X>`。它读两台主机正在运行的版本，逐台说明这次
是 code-only 覆盖、全量发布（并列出是哪些路径要求全量）还是无需发布；列出 owner 必须先执行的
迁移；并检查现在是否落在禁止重启的时间窗里。

**推荐入口**（2026-10-09 起）：

```bash
OWNER_PEER_SSH_KEY=... scripts/shared-peer/deploy-full-release.sh "$X" "$L"          # 只检查
OWNER_PEER_SSH_KEY=... scripts/shared-peer/deploy-full-release.sh "$X" "$L" --apply  # 发布
```

它把下面第 1–3、5 步做成一条命令：拒绝交易时段和盘后 scheduler 窗口、拒绝不在
`origin/main` 上的提交；打包补 `certs/`；发布前从 owner 读出回滚点（上一个 release、
wheelhouse、发布元数据、镜像 ID，并给旧镜像打 `rollback-<L>` 标签）；激活后持有守护锁，
把发布元数据写进软链接背后的真实文件；隧道不重建，主服务健康后才启动 scheduler；
核验两端 `git_sha`、主服务 profile 和凭据软链接，任何一项失败都自动回滚。
下面的手工步骤保留作参考，与脚本等价。

**迁移闸门**（2026-10-09 起，"只检查"也会执行）：脚本从归档读出迁移 head，再经 SSH 读 owner
`/health` 里的 `owner_storage.database_lineage.alembic_version`，两者比较（退出码 6 表示拒绝）：

| owner 库的版本 | 结果 |
| --- | --- |
| 等于 head | 继续 |
| 是 head 的祖先（库落后于代码） | 拒绝，**不可覆盖**。脚本会列出缺的迁移，并给出生成离线 SQL 的命令；先按阶段 D 在 Windows 上迁移，再发布 |
| 不在本仓库的迁移链里 | 拒绝。可能是 owner 有本仓库没有的迁移（去找回源码，不要凭记忆重建），也可能是这次发布比库还旧。查明后用 `RELEASE_ALLOW_SCHEMA_REVISION=<看到的版本号>` 重跑 |
| 读不到（服务宕机） | 拒绝。如果这次发布就是修复，先在 Windows 上用 `alembic current` 确认版本，再用 `RELEASE_ALLOW_SCHEMA_REVISION=none` 重跑 |

覆盖值必须写出当时看到的那个版本号，所以一次覆盖不会被下一次发布顺带沿用。

2026-10-10 起，owner 侧的迁移链已经并入本仓库（`925dec8a`），共享库在本仓库链内的 `20261010_ow0121`，这张表恢复到第一行的常态。
- **正常发布**：不带 `RELEASE_ALLOW_SCHEMA_REVISION`，-11、-12、-13 都是这样发布的。
- **双方都没有持久的外来版本放行**：脚本里的覆盖值只在单次运行中有效，没有可以“移除”的配置；Windows 侧也确认没有设置。
- **要升级共享库时**：库落后于代码，先迁移再发布。这次由我们迁移，做法见附录 C 的 -11 行，事先要请对方暂停 Windows 端的 Alembic，事后请他们切换源码。
`deploy-code-only.sh` 遇到迁移文件有实质改动会直接拒绝，所以只有全量发布需要这道闸门。

1. **打包**（工作站上执行）：

   ```bash
   git archive --format=tar --prefix=trading_hareness/ -o "/tmp/trading_hareness-$X.tar" "$X"
   # 激活脚本要求 release 里有 certs/，但它被 .gitignore 忽略，git archive 带不出来；
   # 只补一个空目录，证书本身由 config/secrets/sync-secrets.sh 推到 ~/.secrets/owner/certs。
   python3 - "/tmp/trading_hareness-$X.tar" <<'PY'
   import sys, tarfile
   with tarfile.open(sys.argv[1], "a") as archive:
       info = tarfile.TarInfo("trading_hareness/certs"); info.type, info.mode = tarfile.DIRTYPE, 0o755
       archive.addfile(info)
   PY
   ```

   wheelhouse：如果 `quant-service/requirements.txt` 自 owner 上次发布以来没有变化（PR #2 没有改它），就直接复用 owner 上现有的 wheelhouse，在 owner 上打包：

   ```bash
   ssh -i "$OWNER_PEER_SSH_KEY" -p 22 stockpeer@118.178.133.37 \
     'tar -C ~ -chf /tmp/wheelhouse-current.tar wheelhouse && tar -tf /tmp/wheelhouse-current.tar | grep -c SHA256SUMS'
   ```

   如果依赖有变化，在 Windows 上执行 `pwsh .\scripts\shared-peer\build-peer-wheelhouse.ps1`，再执行 `tar -C G:\StockPlatform\peer\staging -cf wheelhouse.tar wheelhouse` 并上传。

2. **上传并激活**。首次同步必须使用新版本里的激活脚本，因为旧版脚本不会保留 `intraday-secrets.env`：

   ```bash
   O="-i $OWNER_PEER_SSH_KEY -P 22"
   scp $O "/tmp/trading_hareness-$X.tar" stockpeer@118.178.133.37:/tmp/
   ssh -i "$OWNER_PEER_SSH_KEY" -p 22 stockpeer@118.178.133.37 "set -euo pipefail
     tar -xOf /tmp/trading_hareness-$X.tar trading_hareness/scripts/shared-peer/activate-peer-release.sh > /tmp/activate-peer-release.sh
     RELEASE_ID='$L' bash /tmp/activate-peer-release.sh /tmp/trading_hareness-$X.tar /tmp/wheelhouse-current.tar
     test -s ~/trading_hareness/deploy/shared-peer/.env
     test -s ~/trading_hareness/deploy/shared-peer/intraday-secrets.env && echo secrets-ok"
   ```

   如果 `intraday-secrets.env` 缺失，从上一个 release 目录拷回来（路径见阶段 A 记录的回滚点）：`install -m 0600 <上一个release>/deploy/shared-peer/intraday-secrets.env ~/trading_hareness/deploy/shared-peer/`。

3. **构建并重建容器**。`/health` 的版本号来自 `deploy/shared-peer/.env` 里的 `PEER_APP_GIT_SHA`、`PEER_APP_RELEASE`、`PEER_APP_BUILD_CREATED_AT`（compose 把它们作为构建参数传入）。`verify-owner-cutover.py` 则读 `PEER_EXPECTED_RELEASE`。激活脚本只会在这些键还是占位值时才填写，所以第二次及以后的发布会沿用上一次的值，必须在构建前显式改写。下面的写法只改这 4 个非密钥键，不打印 `.env`。

   `.env` 是指向 `~/.secrets/owner/.env.owner` 的软链接，必须写软链接背后的真实文件（`resolve()`），否则 `replace` 会把软链接换成普通文件，从此脱离集中凭据。整个重建期间持有守护锁，heal 定时器不会在中途重启容器；主服务 `--wait` 健康之后才启动 scheduler，二者同时启动会抢写同一批目录表：

   ```bash
   ssh -i "$OWNER_PEER_SSH_KEY" -p 22 stockpeer@118.178.133.37 "set -euo pipefail
     export XDG_RUNTIME_DIR=/run/user/\$(id -u) DOCKER_HOST=unix:///run/user/\$(id -u)/docker.sock
     LOCK=~/trading_hareness/scripts/shared-peer/release-lock.sh
     bash \$LOCK hold '$L' 3600
     trap 'bash \$LOCK release $L' EXIT
     cd ~/trading_hareness/deploy/shared-peer
     python3 - .env '$X' '$L' <<'PY'
   import datetime, os, pathlib, sys
   path, sha, label = pathlib.Path(sys.argv[1]).resolve(), sys.argv[2], sys.argv[3]
   wanted = {'PEER_APP_GIT_SHA': sha, 'PEER_APP_RELEASE': label, 'PEER_EXPECTED_RELEASE': label,
             'PEER_APP_BUILD_CREATED_AT': datetime.datetime.now(datetime.timezone.utc).strftime('%Y-%m-%dT%H:%M:%SZ')}
   lines = [line for line in path.read_text().splitlines() if line.split('=', 1)[0] not in wanted]
   tmp = path.with_suffix('.tmp'); tmp.write_text('\\n'.join(lines + [f'{k}={v}' for k, v in wanted.items()]) + '\\n')
   os.chmod(tmp, 0o600); tmp.replace(path)
   PY
     C='docker compose -f compose.yaml -f compose.intraday-owner.yaml'
     \$C config --quiet
     \$C build quant-research
     \$C up -d --no-build --wait db-tunnel
     \$C up -d --no-build --force-recreate --wait quant-research
     \$C up -d --no-build --force-recreate --wait quant-research-scheduler
     \$C ps"
   ```

   `quant-research-scheduler` 复用 `quant-research` 构建出来的镜像（`PEER_QUANT_IMAGE`，默认 `trading-hareness-peer-quant-research:latest`），所以只需要构建一次。

4. **systemd 单元**：只有 `deploy/shared-peer/systemd/` 有改动时才执行：

   ```bash
   ssh ... 'install -m 0644 ~/trading_hareness/deploy/shared-peer/systemd/* ~/.config/systemd/user/ && systemctl --user daemon-reload && systemctl --user list-timers | grep -E "peer-(session-guard|daily-backfill)"'
   ```

5. **核验**：

   ```bash
   ssh -i "$OWNER_PEER_SSH_KEY" -p 22 stockpeer@118.178.133.37 'set -a; . ~/trading_hareness/deploy/shared-peer/intraday-secrets.env; set +a
     ~/trading_hareness/scripts/peer-session-guard.sh preopen
     curl -fsS http://127.0.0.1:15683/api/v1/intraday/services/status | head -c 800; echo
     curl -fsS "http://127.0.0.1:15682/api/v1/research/trials?limit=3" | head -c 400'
   scripts/release-sync-status.sh --sha "$X" --skip-edge
   ```

   `peer-session-guard.sh preopen` 只在发现问题时才发飞书告警。

**通过条件**：`--skip-edge` 的巡检全部通过；guard 没有报出问题；scheduler 的服务状态正常（周末应显示 `standby`）。

### F5. 日常 Python 代码快速发布（不重建镜像）

owner 的量化镜像固定 Python、系统库和 wheelhouse；`/app/hotfix` 是只读的
源码挂载。日常只改 `quant-service/app/` 或入口 Python 文件时，使用：

```bash
scripts/shared-peer/deploy-code-only.sh <target_sha> <release_label> \
  --from-sha <active_sha> --apply
```

脚本先比较两个 Git SHA 之间改动的每个路径，分六类（规则在
`scripts/shared-peer/classify-owner-paths.sh`，`scripts/release plan` 用的是同一份）：

- **`runtime`，随本次发布**：`quant-service/app/*`、`entrypoint.py`、`run_server.py`、
  `database_bootstrap.py`。
- **`migration`，随本次发布，但先过库版本闸门**：改了代码的迁移文件、`alembic.ini`、
  `quant-service/migrations/` 下的其他文件。迁移由对方在 Windows 工作站上执行，我们这一侧
  的服务从不自己执行；闸门（`scripts/shared-peer/schema-gate.sh`，全量发布用的是同一个）
  读对方服务报告的库版本：已到本次发布的 head 才继续，落后就拒绝并列出要先执行的迁移。
- **`skip`，不进入 owner 运行时**：
  - `quant-service/tests/`、`docs/`、任何 `*.md`、`.github/`；
  - `feishu-relay/`、`frontend/`、`frontend-shared/`、`xhs-intel/`，它们在 edge 发布；
  - 本机和其他主机的部署文件：根目录 `compose.yaml`、`launchd/`、`workflows/`、
    `deploy/` 里列明的网关配置、服务器 compose 和已停用的 edge 运行时（`deploy/intraday-edge/`）；
  - `scripts/` 下的脚本，按 `scripts/CATALOG.md` 的“Runs on”一列：`workstation…`、`owner-windows`、
    `never run`、`no host runs it` 跳过；这几个和 `47owner…` 之外的登记值一律按 `unclassified` 拒绝；
  - 只改了注释或 docstring 的迁移文件，脚本会比对去掉 docstring 后的语法树。
- **`full`，主机自己的目录树**：`deploy/shared-peer/`（compose、systemd 单元）和
  目录里“Runs on”为 `47owner` 的脚本。输出 `full release required for: <path>` 并退出。
  某个脚本的“Runs on”在两个提交之间跨过了 `47owner` 这条线（登记错了被改对，或反过来）时，即使
  这次差异里没有这个脚本，它也按 `full` 输出：登记成别的主机期间，对它的改动都被跳过了，主机上
  那份可能是旧的。核对过主机上那份就是目标提交的内容之后，把它的路径写进
  `RELEASE_RELABELLED_CURRENT`（空格分隔）再发布。
- **`tunnel-image`，隧道镜像的输入**：`deploy/shared-peer/Dockerfile.ssh-tunnel`、
  `deploy/shared-peer/ssh-tunnel-entrypoint.sh`。纯代码发布拒绝。全量发布会把文件带到主机的目录树里，
  但不重建隧道镜像、也不重建隧道容器：在跑的隧道继续用旧的入口脚本，要和对方约时间手工做。
- **`image`，镜像的输入**：`quant-service/requirements.txt`、`quant-service/Dockerfile.peer`、
  `.dockerignore`。输出 `image build required for: <path>` 并退出；只有全量发布会构建镜像。
- **`unclassified`，没有规则提到的路径**：输出 `unclassified path: <path>` 并退出，什么都不
  构建。处理办法是给它加一条规则（脚本：在 `scripts/generate_scripts_catalog.py` 里登记它在
  哪台机器上运行），不是去做全量发布。2026-10-14 之前这类路径一律被当成“需要全量发布”，
  而全量发布要在只有 894 MB 内存的主机上构建镜像。

所以合并进 `main` 的普通 PR（代码连同测试和文档）可以直接走快速通道。如果两个 SHA
之间没有 owner 运行时改动（例如只改了 edge），发布只会刷新记录的 SHA，这样
`release-sync-status.sh --sha <main>` 在两台机器上都能通过。先不带 `--apply` 演练，
它会列出每一类包含哪些文件。

需要完整发布时，按完整镜像发布和数据库迁移流程执行。纯代码发布
会把 Git archive 写入 owner 的保留 release 目录，原子切换 `hotfix/current`，并用
同一个基础镜像重建两个容器。健康接口会显示源码 SHA 和 release label。启动失败
会自动恢复上一个源码指针并重启旧代码。旧 release 和镜像不会删除，便于回滚。

---

### F6. 日常 Feishu relay 代码快速发布（不重建镜像）

edge 的 adapter 镜像固定 Node 运行时和生产依赖；`hotfix/current` 是只读源码
overlay。日常只改 `feishu-relay/adapter/`、`feishu-relay/bridge/`、配置注册表
或前端源码时，使用：

```bash
feishu-relay/scripts/edge/hotfix-feishu-relay-edge.sh --apply
```

脚本会先运行 adapter、bridge 和两个前端的检查，再把当前 Git 检出生成保留
overlay，原子切换 `hotfix/current`，重启 adapter 和 bridge，不构建也不拉取镜像。
它会校验 overlay 依赖 hash 与当前镜像一致；依赖、Dockerfile、compose 或 Node
依赖清单变化时，必须回到固定镜像发布流程。edge 的 immutable image 发布仍使用
`deploy-feishu-relay-edge-release.sh`，并在发布时强制关闭 overlay。

## 10. 阶段 G：owner Windows 工作站 API

代码在阶段 D 已切到 `X`，这里只需重启并登记版本号。在安全窗口内执行，因为重启会让 Longhu 网关短暂不可用。

```powershell
cd F:\AIWorkflow\trading_hareness
# 在 G:\StockPlatform\config\runtime.env 中新增或替换两行非密钥字段：APP_GIT_SHA=<X>、APP_RELEASE=<L>
pwsh .\scripts\windows\restart-local-quant-api.ps1
(Invoke-RestMethod http://127.0.0.1:5681/health).build | ConvertTo-Json
pwsh .\scripts\shared-peer\verify-shared-runtime.ps1
```

**通过条件**：`build.git_sha` 等于 `X`；`verify-shared-runtime.ps1` 全部通过。

---

## 11. 阶段 H：全局核验与登记

```bash
scripts/release-sync-status.sh --sha "$X" | tee ~/release-sync-logs/$(date +%Y%m%d-%H%M)-post.txt
```

必须输出 `ALL CHECKS PASSED`。然后在附录 C 的发布记录表里追加一行，通过一个只改这一处的 PR 提交。

**`--sha` 不是可选项**：省略时脚本回退到 `origin/main`，在 overlay 模型下会把正常状态
报成一堆失败。始终显式传入本次发布的 SHA。

校验脚本从 2026-09-26 起同时支持两种发布模型（此前写死了固定镜像那一种，于是本规范
规定的 overlay 常态被报成 4 项失败，校验反而失去意义）：

- 先认出实际在用哪一种（`runtime_source=source-overlay` 或 `FEISHU_ADAPTER_HOTFIX_ENABLED=true`
  即判为 overlay），再校验那一种的契约，并把结果登记成 `release model` 一行；
- **overlay**：`release` 必须含干净的期望 SHA 且不带 `-dirty`、`runtime_source` 必须是
  `source-overlay`、overlay 标志必须为 `true`；overlay 可以复用旧的基础镜像，
  因此 `runtime.env` 中的镜像标签可以为空或仍指向旧镜像。脚本同时读取运行容器的
  image ID 和 `/app/package.json` 哈希，要求依赖清单与本次 Git SHA 一致；
- **pinned-image**：仍按原来的契约校验（release 非 `hotfix*`、`runtime_source` 非 overlay、
  镜像标签含期望 SHA、overlay 标志为 false/unset）。

同时修了一个会误报数据库血统的解析 bug：Alembic heads 是用正则从迁移文件里抽的，而正则
要求 `revision` / `down_revision` **紧跟** `=`，于是带类型标注的迁移文件
（`revision: str = "..."`、`down_revision: tuple[str, str] = (...)`）整个被跳过。
2026-09-26 有 3 个这样的文件，正好是最新的 owner lineage 链（`own0001` → `mrg0001`），
结果合并节点的父节点仍被当成 head，`alembic revision` 被报成不一致——**数据库本来是对的，
是检查在骗人**。以后新增迁移写不写类型标注都能被正确识别。

下一个交易日：08:40 的 preopen 检查不应报警；15:05 收盘复盘之后，`GET /api/v1/research/trials` 应能看到 `candidate_ledger`、`xiaojie_leader_flow` 两个族的数据。部署后第一次盘后结果重算会把全部历史结果按 T+1 规则重新结算，耗时会比平时长。

---

## 12. 回滚

| 组件 | 做法 |
| --- | --- |
| edge adapter | `scripts/deploy-feishu-relay-edge-release.sh <上一个sha> <上一个label> --apply`（旧镜像仍在 GHCR） |
| edge bridge | `scripts/hotfix-feishu-relay-edge.sh --list`，然后 `--rollback <id> --apply`。这会把 adapter 也切回那个 overlay，需要时再对 adapter 执行一次固定镜像发布 |
| owner 容器 | `ln -sfn <上一个release>/trading_hareness ~/trading_hareness`，然后按第 9 节第 3 步用上一个 SHA 重新构建并执行 `up -d`（镜像 `:latest` 已被覆盖，必须重新构建） |
| Windows API | `git checkout --detach <上一个sha>`，再执行 `restart-local-quant-api.ps1` |
| 数据库 | **不回退**。新增型迁移对旧代码兼容；迁移本身失败时，该版本会整体回滚，修复后重新执行 `upgrade head` |

回滚后运行 `scripts/release-sync-status.sh --sha <上一个sha>` 确认，并把回滚原因记入发布记录。

---

## 13. 以后每次同步的标准流程

1. 所有改动经 PR 合入 `main`。普通源码改动按 F5/F6 使用干净 Git SHA 的源码 overlay，
   overlay 可以长期作为常规发布模型运行，但**不允许**运行在 `-dirty` release 上；依赖、
   镜像、compose、systemd 变化必须按完整发布流程切换到新的 immutable image。迁移文件随 overlay 带上，
   前提是对方的库已经到本次发布的 head（每次纯代码发布都查，没到就拒绝）。
2. `scripts/release-sync-status.sh --sha origin/main`：记录回滚点，看清漂移。
3. 有 hotfix 或未提交内容时，先做阶段 B。
4. `X=origin/main`，推送 `edge-*` 标签，等镜像发布完成。
5. 如果 `git diff --name-only <owner当前sha> $X -- quant-service/migrations` 有输出：先做阶段 D（迁移，包括 D0 的停止点）。
6. 阶段 E（edge：E1 → E2 → E3，E4/E5 按需）。
7. 阶段 F（owner：打包、激活、改写 `PEER_APP_*` 后构建、`up`、guard）。
8. 阶段 G（Windows API）。
9. `scripts/release-sync-status.sh --sha $X` 输出 `ALL CHECKS PASSED`。
10. 登记发布记录。

平时（不发布时）可以每周运行一次第 2 步：任何 FAIL 都说明有机器偏离了 `main`，要当作问题处理。

---

## 附录 A：CI 修复补丁（已于 `1daae6e` 应用到 PR #2）

`main` 的 `Verify platform contracts` 从 09-05 起一直是红的，原因有三：

1. `compose.yaml` 要求 `FEISHU_APP_ID`/`FEISHU_APP_SECRET`，但 CI 没有提供；
2. CI 的 Postgres 是空库，需要数据库的测试没有 schema 可用；
3. 两个依赖真实行情数据的测试（`test_event_research.PostCloseBacktestRealDataTests`、`test_market_regime_daily.MarketRegimeDailyIntegrationTests`）在任何空库上都会失败。

1、2 两项的补丁如下（`.github/workflows/verify-platform.yml`）：

```yaml
    env:
      POSTGRES_PASSWORD: ci-postgres-password
      QUANT_WRITE_API_KEY: ci-quant-write-key
      N8N_ENCRYPTION_KEY: ci-n8n-encryption-key
      QUANT_BACKGROUND_TASKS_ENABLED: "false"
      FEISHU_APP_ID: ci-feishu-app-id
      FEISHU_APP_SECRET: ci-feishu-app-secret
...
      - name: Build and test research service
        run: |
          docker compose up -d --wait --wait-timeout 120 postgres
          docker compose build quant-research feishu-adapter
          # initialize-ledger.mjs is not copied into the adapter image; mount it.
          docker compose run --rm --no-deps \
            -v "$GITHUB_WORKSPACE/feishu-relay/adapter/initialize-ledger.mjs:/app/initialize-ledger.mjs:ro" \
            feishu-adapter node initialize-ledger.mjs
          docker compose run --rm --no-deps quant-research python database_bootstrap.py
          docker compose run --rm --no-deps quant-research python -m unittest discover -s tests -q
```

第 3 项：按用户 2026-09-26 的决定，这两个测试只在 `QUANT_REAL_DATA_TESTS=1` 时运行。要验证它们，就连到一个有真实行情的库（例如 owner 库的只读副本），设置 `QUANT_REAL_DATA_TESTS=1` 后单独运行。

## 附录 B：本次为统一同步所做的修复

- `feishu-relay/scripts/edge/deploy-feishu-relay-edge-release.sh`（`scripts/` 下同名文件是转发入口）：发布固定镜像时写入 `FEISHU_ADAPTER_HOTFIX_ENABLED=false`；如果 `/health` 仍报告 `runtime_source=source-overlay` 则拒绝。以前 hotfix 脚本的提示说发布脚本会关掉 overlay，但实际上没有，adapter 会一边报告新的 SHA、一边继续运行 overlay 里的旧代码。
- `scripts/shared-peer/activate-peer-release.sh`：切换版本时，除了 `.env`，也保留 `deploy/shared-peer/intraday-secrets.env`（0600，去掉 CRLF）。同步分支里已有同样的保留逻辑，合并时采用了它的写法，另外补上去 CRLF。
- `deploy/shared-peer/compose.yaml`：构建参数采用同步分支的 `PEER_APP_GIT_SHA`/`PEER_APP_RELEASE`/`PEER_APP_BUILD_CREATED_AT`（来自 `.env`），见阶段 F 第 3 步。
- `.gitignore`：忽略 `intraday-secrets.env`。以前 `git add -A` 会把它提交进仓库。
- 新增 `scripts/release-sync-status.sh`（只读巡检）及其测试 `scripts/release-sync-status.test.mjs`。

2026-09-26 状态：B0（推送本地提交）已完成；edge overlay 的代码已经在同步分支里，并已合并进 PR #2；`20260923_0117` 原始源码未找到，已按用户决定加入空版本标记和合并迁移；owner 执行全部迁移；bridge 继续随 edge overlay 发布；CI 中 2 个依赖真实行情数据的测试已改为按需运行（附录 A 第 3 项）。

## 附录 C：发布记录

| 日期（上海） | X | L | edge 回滚点 | owner 回滚点（release / alembic） | 结果 | 备注 |
| --- | --- | --- | --- | --- | --- | --- |
| 2026-10-08 | `0442006` | `owner-full-20261008-1` | 未发布 edge | `20260926T164500Z-source-434e379` / `20260926_mrg0001`；镜像回滚标签 `rollback-20260929`；DB 已前进到 `20261008_sep0002`，不回退 | 已切换（17:23）；当晚两次失稳，19:23 恢复，稳定性仍在观察 | • 本地 `test-release-path` 2336 个里 4 项失败，由操作者豁免：`circuit_open_public_providers` 在线上现行 `ca975eb` 上同样失败（既有）；另 3 项是本地测试库状态（缺 `sep0001`、残留 2099-03-06 行）。CI 状态未核实（令牌无 Actions 读权限）。<br>• 激活脚本会把 `.env`、`intraday-secrets.env` 复制成普通文件，需在激活后链回 `~/.secrets/owner/.env.owner`；发布元数据写入该真实文件，不能用 `mv` 覆盖软链接。<br>• 主服务与 scheduler 同时启动会抢写同一批目录表（`quant.sectors`、`quant.sector_taxonomies`），一方的长事务占锁，另一方启动超过 30 秒即失败。重启顺序必须是 隧道 → 主服务（等 `ok`）→ scheduler。`peer-session-guard-heal` 会间隔几秒同时重启二者，会重演此问题。<br>• db-tunnel 经公网地址绕回本机时，高吞吐下重传约 15%、发送队列积压，健康检查翻转；已改回迁移前的内网地址。该因果为推断，尚未在高负载下复证。<br>• 所有 owner 上的 `docker compose` 必须同时带两个 compose 文件；只带 `compose.yaml` 会把主容器降级为 `research` profile 且仍显示 healthy。 |
| 2026-10-08 | `4a340bf` | `tushare-optional-20261008` | 未发布 edge | 无上一版 overlay（首次 overlay 发布）；回滚 = 删除 `~/trading_hareness/hotfix/quant-service/current` 后按 主服务 → scheduler 顺序重建，回到镜像内的 `0442006` 代码；alembic 不变 `20261008_sep0002` | 成功（22:11 切换，22:16 完成凭据清理后重建，均稳定） | • code-only，owner 运行时文件仅 `main.py`、`intraday_fast_quote_runtime.py`：没有配置 `super_get` 路由时不再启动每秒 `rt_k` 循环。<br>• `deploy-code-only.sh` 的内容摘要校验首次在真实发布里通过（宿主机 release 与容器内 `/app/hotfix/current` 逐字节一致，PID 1 从 overlay 启动）。<br>• 发布脚本改为 隧道不重建 → 主服务（等 `ok`）→ scheduler；`xhs-intel/*`、`config/secrets/*` 工具文件归为不进 owner 运行时；首次 overlay 发布的自动回滚此前是空操作，已补上。<br>• 同次移除 Tushare：本机、owner、edge 的集中凭据与 `env-split.py` 输出均不含 `TUSHARE_*`，owner 旧备份中的该前缀行已清理；Tushare 系提供方全部为 `unconfigured`，`realtime_quote` 由扶摇同花顺、龙虎榜综合源等供数。Tushare 代码未删除，仅在未配置时自动失效。 |
| 2026-10-08 | `5fae013` | `owner-full-20261009-1` | 未发布 edge | 上一版 release 与镜像标签 `rollback-owner-full-20261009-1`，记录在 `~/.local/state/owner-release/owner-full-20261009-1.rollback`；alembic `20261008_sep0002` 不变 | 23:21:58–23:24:31 成功；两端 ok、无 overlay、凭据软链接完好；三次并发重启各 21–26 s 恢复，启动失败 0、锁等待 0 | • 首个 `deploy-full-release.sh` 全量发布 |
| 2026-10-08 | `bfcdba5` | `owner-full-20261009-2` | 未发布 edge | 上一版 release 与镜像标签 `rollback-owner-full-20261009-2`，记录在 `~/.local/state/owner-release/owner-full-20261009-2.rollback`；alembic `20261008_sep0002` 不变 | 23:33:17–23:35:32 成功 | • 事务审计上线：`/health` 的 `database_pool.transactions`，23 个 site、慢事务 0 |
| 2026-10-08 | `e32d2bb` | `owner-full-20261009-3` | 未发布 edge | 上一版 release 与镜像标签 `rollback-owner-full-20261009-3`，记录在 `~/.local/state/owner-release/owner-full-20261009-3.rollback`；alembic `20261008_sep0002` 不变 | 23:47:05–23:49:20 成功；迁移闸门 at_head | • 按调用方分作用域的写密钥（尚未分配调用方密钥，`write_boundary` 只有 legacy） |
| 2026-10-09 | `b41d39f` | `owner-full-20261009-4` | 未发布 edge | 上一版 release 与镜像标签 `rollback-owner-full-20261009-4`，记录在 `~/.local/state/owner-release/owner-full-20261009-4.rollback`；alembic `20261008_sep0002` 不变 | 00:17:58–00:20:09 成功 | • 修复 Tushare 无凭据后的两条盘中崩溃路径（观察池扫描量能兜底、板块报告涨停锚点），赶在 09:30 前上线 |
| 2026-10-09 | `f9fefae` | `owner-full-20261009-5` | 未发布 edge | 上一版 release 与镜像标签 `rollback-owner-full-20261009-5`，记录在 `~/.local/state/owner-release/owner-full-20261009-5.rollback`；alembic `20261008_sep0002` 不变 | 00:44–00:46:39 成功；`tushare/catalog` 404、`tushare/raw` 200 | • 删除只服务 Tushare 的路由；新增盘后 `trade_calendar` 阶段（Fuyao 前向日历），首次运行在周五盘后 |
| 2026-10-09 | `86e795e` | `owner-full-20261009-6` | 未发布 edge | 上一版 release 与镜像标签 `rollback-owner-full-20261009-6`，记录在 `~/.local/state/owner-release/owner-full-20261009-6.rollback`；alembic `20261008_sep0002` 不变 | 至 00:53:22 成功；主服务 10 分钟内错误日志 0 | • 启动不再投影 Tushare 能力目录 |
| 2026-10-09 | `9847f08` | `owner-full-20261009-7；edge `console-20261008T170107Z-9847f08f25ee` + `hotfix-20261008T170124Z-9847f08f25ee-13`` | relay 覆盖层的上一版用 `hotfix --list` 查看、`--rollback` 回滚；控制台用 `deploy-quant-console-edge.sh --rollback` | 上一版 release 与镜像标签 `rollback-owner-full-20261009-7`，记录在 `~/.local/state/owner-release/owner-full-20261009-7.rollback`；alembic `20261008_sep0002` 不变 | 01:02:17–01:04:26 owner 成功；edge 控制台 01:01、relay 覆盖层 01:02 激活；`release status` 全部 PASS | • 配置注册表、竞价脉冲拆出 main.py；edge：研究路由表、去掉 Tushare 面板（旧面板每次加载都请求已删除的 catalog） |
| 2026-10-09 | `f48ad29` | edge `console-20261008T175901Z-f48ad297b674`（仅控制台） | `deploy-quant-console-edge.sh --rollback console-20261008T170107Z-9847f08f25ee` | 未发布 owner：仍为 `owner-full-20261009-7`（`9847f08`），alembic `20261008_sep0002` 不变 | 01:58:56 构建，01:59:01 激活；`--list` 显示 current 指向新版本，旧版保留；`release status --sha 9847f08 --edge-source-sha 9847f08` 全部 PASS | • 研究工作区 composable 按领域拆分（前后录制逐字节一致）；回补状态读不到时收盘复盘计数不再抛错。owner 侧 `3d4d3a0` 之后的运行时改动未发布：都在盘中路径上，留到周五 22:10 之后或周末 |
| 2026-10-09 | `512344e`（owner）；edge 覆盖层与控制台源 `e95071e`（与 `512344e` 相比 `feishu-relay/`、`frontend/` 无改动） | `owner-full-20261009-8`；edge `hotfix-20261009T051727Z-e95071e72801-13426` + `console-20261009T051845Z-e95071e72801` | relay 覆盖层上一版用 `hotfix --list` 查看、`--rollback` 回滚；控制台 `deploy-quant-console-edge.sh --rollback console-20261008T175901Z-f48ad297b674` | 上一版 release `owner-full-20261009-7`（镜像 `3760da87b49f`），记录在 `~/.local/state/owner-release/owner-full-20261009-8.rollback`；alembic 为对方的 `20261008_0118`，本次未改动、不回退 | 13:11:30–13:14:47 owner 成功；13:18:18 relay、13:18:47 控制台激活。`release status`：edge 全部 PASS；owner 两项 FAIL：`/health` 为 `busy`（重启后追赶，13:23 起连续 ok，偶发 2 s busy，远低于 180 s 宽限）、alembic `0118`≠`sep0002`（已知，经批准）。13:26 雷达 8 点、各项检查 ok，当日腾讯涨跌停价已写入 | • **盘中发布**：操作者明确批准在交易时段发布（`RELEASE_WINDOW_OVERRIDE=trading-session`），并批准 `RELEASE_ALLOW_SCHEMA_REVISION=20261008_0118`。owner 库被对方 Windows 流水线迁移到我们仓库没有的 `0118`（`quant.owner_deploy_events`：02:11 因找不到 `sep0002` 失败，02:26 起成功，11:38、12:30 又各发一次）；只读结构对比：是我们 `sep0002` 的超集（缺失 0、类型差 0；对方多 51 表、14 列、7 视图、3 函数）。我们下一个迁移前需要对方提供 0118 源文件。<br>• 内容：Tushare 能力全部替代（决策 0005）；盘面雷达、战法卡片、涨停明细、指标健康；同花顺成分与资金流改新口径；单位与来源标签修正。<br>• 发布前修复：`loadself` 回归（小杰每个交易日首扫会 NameError）、涨停锚点漏接腾讯取价失败、涨跌停价来源标签。<br>• 发布后发现、已在 `7325fb5`/`9989fa9` 修复（未发布）：单点雷达被判 missing、派生指标把警告升为失败。 |
| 2026-10-09 | `d104261`（owner 与 edge 同源） | `owner-full-20261009-9`；edge `hotfix-20261009T070612Z-d104261d39e3-26388` + `console-20261009T070717Z-d104261d39e3` | relay 覆盖层上一版 `hotfix-20261009T051727Z-e95071e72801-13426`（`hotfix --rollback`）；控制台 `deploy-quant-console-edge.sh --rollback console-20261009T051845Z-e95071e72801` | 上一版 release `owner-full-20261009-8`（镜像 `4c9b7cbf489b`），记录在 `~/.local/state/owner-release/owner-full-20261009-9.rollback`；alembic 仍为对方的 `20261008_0118`，不回退 | 15:03:24–15:06:02 owner 成功；15:07:09 relay、15:07:19 控制台激活。`release status`：仅 alembic `0118`≠`sep0002` 一项 FAIL（已知、经批准），owner `/health` ok。15:07 新接口全部 200（0.1–0.3 s）：数据源看板健康 16、唯一告警为盘后公开归档；策略看板就绪 10、降级 5、受阻 0 | • 收盘后 15:03 发布（操作者明确批准，仍带 `RELEASE_WINDOW_OVERRIDE=trading-session` 与 `RELEASE_ALLOW_SCHEMA_REVISION=20261008_0118`）；发布前确认收盘窗口数据已落库（雷达最后一点 14:58:48、板块资金流最后快照 14:59:12）。<br>• 内容：盘后台账阶段（`candidate_ledger`）、分钟面板导出（`minute_panel_export`）、数据源与策略看板、目录不再把 Tushare/新浪当在用来源、中继研究读 30 s、雷达分板块按需、盘面页错峰刷新、重读接口缓存、决策 0009 第 1–3 步（默认双写 `LEVEL1_STORAGE=both`）。 |
| 2026-10-09 | `d08e5c7` | `owner-full-20261009-10`（仅 owner） | 未发布 edge | **记录的上一版 `owner-full-20261009-9`（镜像 `fe3aa903a9fd`，标签 `rollback-owner-full-20261009-10`）不含对方 16:04–16:41 的三次修复，不能直接回滚到它**；可用的回滚目标是对方的 `critical-close-repair-20261009`（镜像 `b91359ee5349`），或从 main 重新发布。alembic 仍为对方的 `20261008_0118`，不回退 | 16:50:09 门禁通过，16:52:10 两个容器 healthy；容器内代码与 `d08e5c7` 逐文件一致（0 差异）；`/health` ok；指标 ok 5、待产出 5（盘后 17:30 到点）、缺 1（分钟文档，周一起）、警告 1（台账）；数据源：东财降级、公开归档故障（龙虎榜、估值）；策略就绪 10、降级 5 | • 对方 16:04–16:41 连上三个修复镜像（`post-close-repair`、`critical-close-repair`、`dashboard-transport-repair`），都建在 `owner-full-20261009-9` 之上，代码只在镜像里。逐文件指纹比对后原样并入 main（`9f319d8`、`32fc62a`、`d08e5c7`，经操作者批准），再发布。16:46 第一次发布被指纹门禁中止（对方 16:41 又换了镜像），并入第三个修复后于 16:50 重发。<br>• 镜像标签取自对方 `.env` 的 `PEER_QUANT_IMAGE`（`…:dashboard-transport-repair-20261009`），所以该标签现在指向我们的构建 `17253ae2923e`，对方原镜像没有标签了。<br>• 在发布窗口内（15:10 之后、18:45 之前），未用 `RELEASE_WINDOW_OVERRIDE`；带 `RELEASE_ALLOW_SCHEMA_REVISION=20261008_0118`（已批准）。<br>• 内容：对方三次修复；失败原因带异常类型；交易日历余量判断；阶段超时后收据记为失败；资金流结果“值没变就不写”。未含 `db478e9`（分钟文档按采集去重），留待下一次发布、周一盘前。<br>• **17:0x 按操作者明确要求清理 owner 旧物**（这是对“不删旧发布”规则的一次性豁免）：删除旧的 quant-research 镜像 11 个（`rollback-20260929`、`rollback-owner-full-20261009-1`…`-9`，以及对方已弃用的 `status-fix-13633be4` 和 `before-status-fix-13633be4`）；删除旧发布目录 18 个（09-19 至 `owner-full-20261009-8`），以及 -1…-9 的回滚记录和 -1…-8 的激活脚本；删除 `factor-maintenance` 的 3 个非当前版本；清理 24 小时前的构建缓存（1.67 GB）。磁盘使用率从 90% 降到 64%，可用空间从 4.1 GB 增至 14 GB。保留：当前 `owner-full-20261009-10`、上一版 `-9` 目录、对方修复链镜像 `fe3aa903a9fd`/`c1c4b111280f`/`b91359ee5349`、两个隧道镜像、对方 `auth-fix-13633be4`、`postgres:16-alpine`（compose 引用）。edge 未动（脚本自动只保留最近 5 版）。 |
| 2026-10-10 | `925dec8a`（owner 与 edge 同源） | `owner-full-20261010-11`；edge `hotfix-20261010T034716Z-925dec8ad591-2849` + `console-20261010T034854Z-925dec8ad591` | relay 覆盖层上一版用 `hotfix --list` 查看、`--rollback` 回滚；控制台上一版用 `deploy-quant-console-edge.sh --list` 查看、`--rollback` 回滚 | 记录的回滚点 `owner-full-20261009-10` 写的镜像 `fe3aa903a9fd` 是 -9 的，不能用；-10 实际运行的镜像是 `17253ae2923e`，与 `d08e5c7` 逐文件一致。**共享库已由我们从 `20261009_0119` 迁到 `20261010_ow0121`（只做校验，没有执行 DDL）。回滚代码不回滚迁移；不认识 ow0121 的旧版本不得启动** | 11:27 只读预检：容器与 `d08e5c7` 差异 0，库在 0119，磁盘用了 64%。ow0121 只读空跑：契约 223 个表/视图、2600 列、798 个约束、556 个索引，缺失 0、不符 0。迁移后 `alembic current` = ow0121。11:37 两个容器 healthy；容器与 `925dec8a` 逐文件一致（1039 个文件，差异 0）；/health ok（ow0121）；10 分钟内报错 0 | • 周六非交易日，经操作者确认在白天维护，窗口检查通过；手动模式下逐条批准。<br>• 合入协作分支：对方的 `0c774a4`、`6dab79a`，以及我们修复其 Python 3.12 测试的 `b52d3863`。经 collab_branch.py 全部检查（2565 项测试）合为 `925dec8a`。合并工具改为：生成文件冲突时重新生成（`3f60c14f`）。<br>• 发布脚本不跑迁移：迁移包放进容器的 `/tmp/mig-20261010-11`，先只读校验，再 `alembic upgrade head`。<br>• 10-09 估值投影执行唯一一次 `--apply`：插入 316 行（北交所 315 行，加 002074.SZ），记录覆盖 5416/5416。但这些行的 available_at 是 10-10，按 0010 的原时点口径，`core_daily_controls` 仍是 blocked，候选台账等研究阶段没有跑通。证据查询写死了 10 s 超时，缓存冷时被取消两次，缓存热时只要 2.0 s。<br>• 重跑 10-09 盘后：`market_flow_features` 超出 90 s 预算（重启后缓存冷）。新阶段中，温度、ETF、金/银指完成；分时温度因 10-09 只有 83 份分钟截面而 blocked。<br>• 回填：日线温度 391 条（10-09 为 72.6）；ETF 4800 根 K 线和 370 条放量比（10-09 为 1.5438）；金/银指 370 条（10-09 为银指） |
| 2026-10-10 | `6266941e` | `owner-full-20261010-12`（仅 owner） | 未发布 edge：与 `925dec8a` 相比，`feishu-relay/`、`frontend/` 没有改动 | 上一版 `owner-full-20261010-11`（`925dec8a`）；alembic `20261010_ow0121` 不变 | 12:00 指纹门：容器与 `925dec8a` 差异 0。12:02 healthy；容器与 `6266941e` 差异 0；/health ok；报错 0 | 情绪温度的回看从 420 天改为 600 天。420 天时，最近 30 条里有 16 条和完整历史回填的结果不同，每晚都会把正确值覆盖掉 |
| 2026-10-10 | `ca422423` | `owner-full-20261010-13`（仅 owner） | 未发布 edge（理由同上） | 上一版 `owner-full-20261010-12`（`6266941e`）；alembic 不变 | 12:13 指纹门：容器与 `6266941e` 差异 0。12:15 healthy；容器与 `ca422423` 差异 0；/health ok；报错 0 | • 分钟采集每天约 14:59 停止，拿不到收盘集合竞价。分时温度的收盘点因此改为 14:55 以后的最后一份截面，成交额占比的分母改为日线的全天成交额。截面的股票范围比日线全 A 名单宽约 1.5%，收盘前占比因此略大于 1，前后一致，不影响分位。<br>• 日线读取窗口改为“天数 × 3/2 + 20”个自然日。原来要 250 天，只拿到 240 条。<br>• 分时回填 09-07 至 10-09：7 天完成（09-18、09-21 至 09-24、09-28、09-29），其余日子分钟截面稀疏或缺失。10-09 的分时收盘点是 71.2，日线是 72.6 |
| 2026-10-10 | `c655016b` | `owner-full-20261010-14`（仅 owner） | 未发布 edge | 上一版 release `owner-full-20261010-13`（`ca422423`，镜像 `55dbf5f996a4`），记录在 `~/.local/state/owner-release/owner-full-20261010-14.rollback`（13:29:09 写入）；alembic `20261010_ow0121` 不变 | 两个容器 13:30:38 启动；19:09 只读核对：/health ok，git_sha 与 running_git_sha 均为 `c655016b`，无覆盖层，镜像 `:latest`，两个容器 healthy | • **补记**（22:30 写入）：当时的发布输出不在本文。以上事实来自 owner 上的回滚记录、`docker inspect` 和 19:09 的 /health。<br>• 内容：迁移闸门回到常规，不再需要外来版本号覆盖（`c655016b`） |
| 2026-10-10 | `7ba2412e` | `owner-full-20261010-15`（仅 owner） | 未发布 edge：`release plan` 判定自 `925dec8a` 起 edge 无改动 | 上一版 release `owner-full-20261010-14`（`c655016b`，镜像 `29505d3deb7a`），记录在 `~/.local/state/owner-release/owner-full-20261010-15.rollback`；alembic `20261010_ow0121` 不变 | 21:11 空跑：指纹门（容器与 `c655016b` 的 1033 个文件差异 0）、迁移闸门 at_head。22:20:07 发布前指纹门再次通过；22:19:53–22:22:23 成功，主服务和 scheduler 依次 healthy，新镜像 `68464e857263`。22:28 核对：/health ok；git_sha 与 running_git_sha 均为 `7ba2412e`；无覆盖层；alembic `ow0121`；容器与 `7ba2412e` 的 1059 个文件差异 0；两个容器启动后 5 分钟报错 0。22:31 容器内冒烟：20 台主机池，`115.238.56.198:7709/login_one` 返回 600519 1263.00、000001 11.59，与 21:04 的 MAC 实测收盘一致 | • TDX 数据源扩展（`n8n/.claude/plan/tdx-integration.md` 与 delta 1–3），六条车道：证券列表、全 A 快照与指数概况、F10 与 gpcw 财务史、7727 扩展行情、微观结构、MAC。新能力全部 UNSUPPORTED，不进入路由，也不参与决策。<br>• 运行时改动：LOGIN_ONE 握手，按主机回退到旧握手；owner 实测的 20 台主机池（`TDX_HQ_HOSTS` 可覆盖）；分钟 K 线按 float32 解码量额；盘后任务记录 TDX 健康状态。<br>• 不含迁移与依赖变化；P7（展示行迁移）留待以后。<br>• `release plan` 判定需全量发布：22 个路径，主要是 `scripts/data/` 下的证据文件。周六安全窗口发布。 |
| 2026-10-11 | `5088e844` | `owner-full-20261011-16`（仅 owner） | 未发布 edge：edge 正运行另一会话的源码热更新（`8c90141f`，未推送的 feishu-relay 提交），控制台数据源目录页与中转路由等该会话推送后随 edge 镜像发布（用户 2026-10-11 决定） | 上一版 release `owner-full-20261010-15`（`7ba2412e`，镜像 `68464e857263`），记录在 `~/.local/state/owner-release/owner-full-20261011-16.rollback`；alembic `20261010_ow0121` 不变 | 02:50 空跑：指纹门（容器与 `7ba2412e` 的 1059 个文件差异 0）、镜像启动时间 `2026-10-10T14:21:41Z` 未变、迁移闸门 at_head。09:39:23–09:42:03 发布成功，主服务和 scheduler 依次 healthy，新镜像 `91b504d1501a`。09:43 核对：/health ok；git_sha 与 running_git_sha 均为 `5088e844`；无覆盖层；alembic `ow0121`；容器与 `5088e844` 的 1074 个文件差异 0；两个容器启动后报错 0；`owner_deploy` 仍是 9 月 20 日的记录（本次未改 `owner_deploy_events.py`，原有状态）。容器内 `python -m app.datasources probe tdx_public bars.index_daily`（owner 出口，`115.238.56.198:7709/login_one`）返回 999999 2026-10-09 收 3813.79、涨跌家数 1341/956，与本机实测一致；`/api/v1/datasources/catalog` 与 `/openapi.json` 经 15682 返回 200 | • TDX 第四轮（`n8n/.claude/plan/tdx-next-plan.md` N1–N8、G10、G11）：旧协议日线/分钟线主路（优先级 65）、指数日线与成分宽度（`breadth.index_daily`）、tdxstat 估值与每日指标、板块成分（block_gn/fg）、交易日历、新股、北交所旧代码换新、按品种的报价读取（五档、板块指数、ETF IOPV）、由全 A 快照与 MAC 涨跌停价推算的涨停/炸板/跌停池、晋级流水线（probe 命令、`scripts/tdx-promote.py`、按键比对与参照）、周一验收脚本、数据源目录接口（`/api/v1/datasources/catalog`、`/capabilities/{capability}`、`/read/{source}/{capability}`）。新绑定全部 UNSUPPORTED，`decision_eligible` 全为 False。<br>• 新的盘后采集十项默认全部关闭（`PUBLIC_ARCHIVE_TDX_*_ENABLED`）；研究试读 `DATASOURCE_RESEARCH_READ_ENABLED` 默认开，有并发、频率和行数上限，永不进入决策。<br>• 不含迁移与依赖变化。周日安全窗口发布。 |
| 2026-10-11 | `68564e8e` | `owner-full-20261011-17`（仅 owner） | 未发布 edge：这次并入 main 的 14 个 feishu-relay 提交属于 edge 发布单元，edge 仍按 -16 行所述运行另一会话的源码热更新 | 上一版 release `owner-full-20261011-16`（`5088e844`，镜像 `91b504d1501a`，标签 `rollback-owner-full-20261011-17`），记录在 `~/.local/state/owner-release/owner-full-20261011-17.rollback`；alembic `20261010_ow0121` 不变 | 发布前只读盘点：-16 两端 ok、profile `intraday_edge`；空跑迁移闸门 at_head。主服务 19:49:38、scheduler 19:50:00 启动并 healthy，新镜像 `aa65436e6505`，重启次数 0。`release-sync-status.sh --sha 68564e8e --skip-edge` 全部 PASS；PID 1 的工作目录是 `/app`，没有覆盖层；容器内 `app/` 的 567 个文件与 `68564e8e` 逐文件一致（差异 0）；启动后主服务日志里 traceback 与 loop failed 为 0 | • `release plan` 判定全量发布：29 个路径在 `scripts/` 下（证据 JSON、探测脚本、升级工具），owner 运行时实际只变 5 个文件，依赖、Dockerfile、compose、迁移都没有改动。<br>• 内容：`public_evidence_capture` 各源独立运行，一个源被限流只丢它自己的读数（`5c8d8014`）；`tdx_protocol.call_sync` 换主机限时 15 秒；26 个通达信绑定从 unsupported 升为 declared（`68564e8e`）；MAC 日线行改用 `trade_date`、指数日线参照日期改为 `YYYYMMDD`（`608f7e3f`）。<br>• 发布前在 -16 的容器里按采集节奏压测通达信适配器（`scripts/data/tdx_realtime_load_2026-10-11_owner.json`）：1,118 次调用，失败 0。<br>• 发布后在 owner 出口补做 `tdx_public bars.daily` 的升级检查，与 `tdx_mac` 的 close、amount 一致，升为 declared；这个状态改动在 `68564e8e` 之后，随下一次发布进入 owner。<br>• `--apply` 由用户在终端执行：Claude Code 的自动模式把生产发布命令拦下，没有绕过 |
| 2026-10-12 | `a13870fc` | `owner-full-20261012-18`（仅 owner） | 未发布 edge：`release plan` 显示 edge 相对 `0d4649da` 没有待发布改动 | 上一版 release `owner-full-20261011-17`（`68564e8e`，镜像 `aa65436e6505`，标签 `rollback-owner-full-20261012-18`），记录在 `~/.local/state/owner-release/owner-full-20261012-18.rollback`；alembic `20261010_ow0121` 不变 | 午间休市窗口内发布。主服务 12:49:29 启动，scheduler 随后重建，两者 healthy，新镜像 `186862820cf2`，重启次数 0。`release-sync-status.sh --sha a13870fc --skip-edge` 全部 PASS；PID 1 的工作目录是 `/app`；容器内 `app/` 的 567 个文件与 `a13870fc` 逐文件一致（差异 0）；主服务的 8 个循环租约在 12:49:43 取得，持有者等于按容器主机名和 1 号进程算出的身份；启动后主服务日志里 traceback、Fatal Python、loop failed 为 0 | • 起因：当天 09:15:00.716 主服务以退出码 139（段错误）退出，Docker 一秒后拉起，但进程用随机身份持有循环租约，新进程等旧租约过期，9 个循环到 09:16:25–09:17:06 才恢复。崩溃的库没有查到：内核日志需要 root，进程当时没有崩溃栈输出。<br>• 内容：服务进程开启 `faulthandler`（`e8174a89`）；租约持有者由主机名和进程号派生，持有者可以取回自己未过期的租约，同步、异步两个获取函数同一规则（`85ad4179`）；`collect --tasks` 同名任务只跑一个循环（`6fe1d842`）。这次是全量发布，容器重建，租约取回要到下一次容器内重启才会用到。<br>• `release plan` 判定全量发布：`-17` 之后的提交里有 `scripts/data/` 证据文件。随本次进入 owner 的还有 `tdx_public bars.daily` 的 declared 状态（`853ba1da`）。<br>• 发布窗口：`config/release-windows.json` 当天按操作者决定拆成 08:30–11:30 与 13:00–15:10，午间可发布（`d15283a1`）。<br>• `--apply` 由用户在终端执行 |
| 2026-10-12 | `004e3f88`（owner）；量化控制台源 `e91a2d34` | `owner-full-20261012-19`；edge 控制台 `console-20261012T072415Z-e91a2d3414f4` | edge 只发布了量化控制台这一单元；edge adapter（feishu-relay）有别的会话未发布的改动，没有动 | 上一版 release `owner-full-20261012-18`（`a13870fc`，镜像 `186862820cf2`，标签 `rollback-owner-full-20261012-19`）；alembic `20261010_ow0121` 不变；控制台上一版 `console-20261010T034854Z-925dec8ad591` | 收盘后发布。主服务 15:21:15 启动，scheduler 随后重建，两者 healthy，新镜像 `7b3bbaa79015`，重启次数 0。`release-sync-status.sh --sha 004e3f88 --skip-edge` 全部 PASS；PID 1 工作目录 `/app`；容器内 `app/` 567 个文件与 `004e3f88` 逐文件一致（差异 0）；进程内 `storage_mode()` 为 `document`、温度版本为 `market-temperature-v2`；启动后日志里 traceback、Fatal Python、loop failed 为 0。控制台：`/research` 返回新构建的资源，温度页代码块含口径标注；经 edge 的 `/api/research/market/temperature/daily?days=120` 返回 v2、120 条、`stale_versions` 0 | • 内容：全 A 快照只写分钟文档（决策 0009，`LEVEL1_STORAGE` 默认 `document`，`a076d116`）；`derived_tdx_limits` 三个涨跌停池升为 declared（`a399866f`）；情绪温度日线分位只用之前的交易日、读数公布分项覆盖、日线接口只返回当前版本、指标自检检查版本（`004e3f88`）。<br>• 发布后数据操作：15:23 在 owner 主服务容器里运行 `python -m app.market_temperature_runtime --end 2026-10-09 --keep 400 --lookback-days 700 --apply`，写入 400 条 v2 读数（不删除旧行）；之后日线接口返回 250 条 v2、`stale_versions` 0。10-12 当天的读数由当晚盘后刷新写入。<br>• 慢写入是否消失要到下一个交易时段才能在 owner 上验证：发布时已收盘，没有新的快照写入。<br>• `release plan` 判定全量发布：提交里有 `scripts/data/` 证据文件。owner 的 `--apply` 由用户在终端执行；控制台发布由助手执行 |
| 2026-10-12 夜至 10-13 凌晨 | `dff8acee` → `b0bea637` → `857e4832` → `5e2d159a`（owner）；edge 中继与控制台源 `857e4832` / `5e2d159a` | `owner-full-20261012-20`、`owner-code-20261012-21`、`owner-code-20261013-01`、`owner-code-20261013-02`；edge 中继 `hotfix-20261012T162327Z-857e48320e2b-18399`；edge 控制台 `console-20261012T163536Z-5e2d159a1753` | edge 中继相对上一版 `c6ddf086` 只多了 `adapter/research-routes.mjs` 的两条读取路由；其他会话未提交的中继改动没有带上 | owner 全量发布的回滚点 `~/.local/state/owner-release/owner-full-20261012-20.rollback`（上一版 `owner-full-20261012-19`，`004e3f88`）；三次纯代码发布各保留上一版 overlay；alembic `20261010_ow0121` 不变；中继上一版 `hotfix-20261012T085004Z-c6ddf086bf1f-25695`；控制台上一版 `console-20261012T072415Z-e91a2d3414f4` | 四次 owner 发布后 `release-sync-status.sh` 的 owner 行全部 PASS，最后一次连同 edge 行 ALL CHECKS PASSED；`-20`：当日涨停榜落库 5 张、历史榜单补 9 个交易日、大面风险分从 2023-09-11 回算到 10-12，线上当日结果与研究逐只一致，回看接口留出段 8.5% 对 19.0%；`-21`：竞价处置表与大面风险分路由可访问，两个新指标在健康检查里为未到产出时点；`-01`：`?date=` 生效，回看 500 天 23 秒降到 7 秒；`-02`：在服务实际运行的 overlay 源码上读取，竞价池的上一交易日读数为 2026-10-12（46 只），池子 78 只。中继：新路由经 `:18300` 可访问，原有温度路由正常；控制台：`/research` 返回新构建，包里有“涨停风险分”页签、研究用途标注和两条路由 | • `-20`：通达信全市场快照并行采集（`tdx_all_a_shadow`）与涨停股次日大面风险分后端（决策 0016）。• `-21`：竞价池采集与 09:25 处置表（决策 0017），10-13 首次运行。• `-01`：大面风险分的日期查询修正、回看提速与“近期是否有效”判定，配套中继路由和控制台页面。• `-02`：修复 `limit_up_risk_runtime.daily` 返回窗口内最早读数而不是指定交易日的错误；该错误随 `-21` 上线，在首个交易时段之前发现并修复，否则竞价池会取到 2024 年的涨停股。• 同晚的手工数据修复（非代码发布）：9 月 21 日至 10 月 12 日补日线 818 条、复权因子 814 个、涨跌停价 811 条，老师复盘包以新编号重导，脚本与日志在 `state/research/owner_repairs_20261012/`。 |
| 2026-10-13 午间 | `29f3ffdf`（owner）；量化控制台源 `ac15bcde` | `owner-code-20261013-03`；edge 控制台 `console-20261013T031958Z-ac15bcdebbc8` | edge 中继没有改动，仍是 `hotfix-20261012T162327Z-857e48320e2b-18399`；控制台是独立单元，11:20 先行发布 | 上一版 overlay `owner-code-20261013-02`（`5e2d159a`）保留；alembic `20261010_ow0121` 不变；控制台上一版 `console-20261012T163536Z-5e2d159a1753` | `release-sync-status.sh --owner-source-sha 29f3ffdf --edge-source-sha 857e4832`：ALL CHECKS PASSED；主服务与 scheduler 两个容器实际导入的源码目录都是 `owner-code-20261013-03`，盘后阶段表里 `daily_close_completion` 位于 `full_market_daily` 之后、`limit_performance_lists` 之前；健康检查出现 `data.daily_bars_complete`（未到产出时点）；经 `:18300` 的雷达、大面风险分路由返回 200（重启后第一次请求曾返回一次 503）；控制台包里有新图的标题与图例。首次盘后运行在 10-13 晚核对 | • `-03`：日线收盘补全（决策 0018）——全市场日线之后按名册逐只补缺失日线、涨跌停价和复权因子，结果分类入账，新增健康检查。• 控制台：盘面雷达的“主动买/卖·主力净额”图重画（结论数字、四条线、9:25 与 9:30 标记、悬停说明），大面风险分页的日度请求改成两条字面路径。• 发布后发现并已在 main 修复、尚未发布：两个回看接口按日历取“最近 N 个交易日”时没有以当天为上界，把日历里未来的交易日也算了进去 |
| 2026-10-13 傍晚 | `26170231`（owner） | `owner-code-20261013-04`（仅 owner，17:41） | 未发布 edge：中继已由另一条工作线在当天发到 `hotfix-20261013T070041Z-49b44b1da5e4-5527`（源 `49b44b1d`），`release plan` 显示 edge 相对它没有待发布改动；控制台仍是 `console-20261013T031958Z-ac15bcdebbc8` | 上一版 overlay `owner-code-20261013-03`（`29f3ffdf`）保留；alembic `20261010_ow0121` 不变 | `release-sync-status.sh --owner-source-sha 26170231 --edge-source-sha 49b44b1d`：ALL CHECKS PASSED；主服务与 scheduler 两个容器实际导入的源码目录都是 `owner-code-20261013-04`，其中 `intraday_schedule.seconds_until_slot` 和目录里的 `SESSION_AGREEMENT_RULES` 可用；通达信当日汇总命令在生产上即刻返回（当天 238 份并行快照；比对行都是发布前写的、没有数据时间，所以可判分钟为 0）；回看接口 `track?days=60` 返回 59 天（修复前是 1 天）。两条采集循环只在交易时段运行，新节拍的第一次真实运行在 10-14 09:15，届时核对 | • 通达信补主行情快照的第一步（工作线 W008，方案 P1–P4）：主行情与通达信两条全市场采集固定在每分钟第 50、52 秒启动（原先“采完再等 60 秒”会漂移漏分钟）；通达信快照的完整性门槛；比对按数据时间记录；当日汇总不再读快照正文并报告当天是否达标。不改任何绑定的状态或可用于决策的标记。• 回看接口按日历取“最近 N 个交易日”时以当天为上界（`7bb29e71`）。• 当天 15:07–17:00 中转服务器不响应（原因未知，重启后恢复），发布在恢复之后进行；edge 读 owner 的路由在发布时仍返回 `fetch failed`，与本次发布无关，待 edge 一侧重连 |
| 2026-10-14 凌晨 | `0a57d7fb`（owner） | `owner-code-20261013-06`（仅 owner，00:43，用户执行） | 未发布 edge：中继由另一条工作线发布，当时是 `hotfix-20261013T162249Z-83b5d49e0b70-12319`（源 `83b5d49e`） | 上一版 overlay `owner-code-20261013-05`（`e8cb8dbf`）保留；纯代码发布不构建镜像（镜像 ID 这次没有另行读取）；alembic `20261010_ow0121` 不变 | `release-sync-status.sh --owner-source-sha 0a57d7fb --edge-source-sha 83b5d49e`：ALL CHECKS PASSED（输出 `docs/work/probes/2026-10-14-release-status-06.out.txt`）；主服务与 scheduler 都报告 `owner-code-20261013-06`；发布后 45 秒内晚间循环补跑了 10 月 13 日的五个新阶段和 10 月 12 日的两个补做阶段，回执全部 completed，日志里没有“left open”；10 月 12、13 日的情绪温度、宽基 ETF 放量、金银指读数已存（`docs/work/probes/2026-10-14-peer-evening-receipts.out.txt`）；发布后主机可用内存 336 MB、交换区已用 448 MB，scheduler 容器 27 MiB、主服务 173 MiB | • 我们这一侧每晚要的产出由自己的晚间循环启动（工作线 W011，决定 0021）：涨停榜、涨停股次日大面风险分、情绪温度、宽基 ETF 放量、金银指，排在老师复盘的五个阶段之后；回执只在产出存进库里时关闭；每轮在当天阶段之后补做前一交易日的榜单和风险分。运行时文件 3 个。• 没看到的：第一次真正的当晚自动运行（10 月 14 日 16:15 之后）；16:20 左右的风险分读数与 19:00 后重算是否一致 |
| 2026-10-13 夜 | `e8cb8dbf`（owner） | `owner-code-20261013-05`（仅 owner，22:38；迁到新服务器 `118.178.133.37` 后的第一次发布） | 未发布 edge：中继当晚由另一条工作线两次热更新，发布后是 `hotfix-20261013T142445Z-3c78e23ec2e8-28449`（源 `3c78e23e`） | 上一版 overlay `owner-code-20261013-04`（`26170231`）保留在新服务器上；镜像仍是 `58da6ff8766c`，没有重建；alembic `20261010_ow0121` 不变 | `release-sync-status.sh --owner-source-sha e8cb8dbf --edge-source-sha 3c78e23e`：ALL CHECKS PASSED；主服务与 scheduler 两个容器实际导入的源码目录都是 `owner-code-20261013-05`，目录的 `AGREEMENT_KEYS` 里有 `min_share`；发布后主机可用内存 323 MB、交换区已用 332 MB，四个容器 healthy；发布后 `limit-up-risk/daily` 仍返回当晚手工补的 10 月 13 日读数（67 行） | • 通达信补主行情快照的第二步（工作线 W008，方案 B3）：数据源目录里比对项可以按“达标占比”判定，通达信全 A 快照对主源的比对声明（价格 0.5% 以内占 95%）；通达信并行采集的当日汇总写明自己量的是哪个数据源和能力。不改任何绑定的状态或可用于决策的标记。运行时文件 2 个。• 发布工具的默认目标改为新服务器（`4bb8c7ff`）；两个手工脚本的默认地址因会把发布判成全量而撤回（`e8cb8dbf`）。• 发布输出里有两条 compose 警告（两个数据卷“已存在但不是 compose 建的”），是迁移时卷被手工恢复所致，不影响运行 |
| 2026-10-13 14:02 | edge 中继源 `ea3d5865` | edge 中继 `hotfix-20261013T060210Z-ea3d5865b40e-16279`（仅 edge 中继） | owner 和 edge 控制台没有动；中继相对上一版 `857e4832` 只多这一个提交：看板里登记新来源标签、配置新目标群的 webhook（决定 0019，工作线 W009） | 上一版 overlay `hotfix-20261012T162327Z-857e48320e2b-18399` 保留，回滚用 `hotfix-feishu-relay-edge.sh --rollback <该编号> --apply`；启动时新建表 `feishu_group_relay_webhook_targets`（`CREATE TABLE IF NOT EXISTS`，旧版代码不读它） | `release-sync-status.sh --skip-owner --edge-source-sha ea3d5865` ALL CHECKS PASSED；adapter 与 bridge 的 release 都是新编号，WebSocket 14:03:59 已重连、监听 24 个会话、10 条启用路由全部 healthy；新接口的五种拒绝情形按预期返回且不入库；看板构建里有新增的表单文字。输出存 `docs/work/probes/2026-10-13-feishu-relay-w009-live-check.out.txt`。没有在线上真的登记一个新标签或新增一个目标群 |
| 2026-10-13 15:00 | edge 中继源 `49b44b1d` | edge 中继 `hotfix-20261013T070041Z-49b44b1da5e4-5527`（仅 edge 中继） | owner 和 edge 控制台没有动；中继相对上一版 `ea3d5865` 只多这一个提交：目标群的自定义机器人由中继用账号自己的会话创建（决定 0020，工作线 W009） | 上一版 overlay `hotfix-20261013T060210Z-ea3d5865b40e-16279` 保留；启动时给 `feishu_group_relay_webhook_targets` 加两列 `bot_id`、`managed`（`ADD COLUMN IF NOT EXISTS`，上一版代码不读它们，回滚不受影响） | `release-sync-status.sh --skip-owner --edge-source-sha 49b44b1d` ALL CHECKS PASSED；发布前在 edge 上跑完整 bridge 测试 174 项通过；15:02 经 adapter 接口在运维通知群自动建机器人并登记目标群、重复调用沿用同一机器人、删除时机器人一并移除，群里机器人列表恢复原样；两边日志里没有 webhook 地址。输出存 `docs/work/probes/2026-10-13-feishu-chat-bot-trial.out.txt` |
| 2026-10-13 18:00 | edge 中继源 `10d5f55b` | edge 中继 `hotfix-20261013T100002Z-10d5f55bf233-24792`（仅 edge 中继） | owner 和 edge 控制台没有动；中继相对上一版 `49b44b1d` 多的是工作线 W010 的 base：群功能的接缝、目标群登记（现有目标群显示群名和机器人）、看板“目标群”栏目；其余功能的模块是空壳 | 上一版 overlay `hotfix-20261013T070041Z-49b44b1da5e4-5527` 保留；启动时新建表 `feishu_group_relay_target_groups`（`CREATE TABLE IF NOT EXISTS`，上一版代码不读它） | `release-sync-status.sh --skip-owner --edge-source-sha 10d5f55b` ALL CHECKS PASSED；发布前集成分支上 adapter 289 项、看板 10 项通过，edge 上 bridge 套件 179 项通过；线上 `/targets` 页面可开，手动触发识别后 4 个环境变量目标群和自测群都对上群名、机器人和群主，响应和日志里没有 webhook 地址；启动时的自动识别因 bridge 尚未就绪失败一次且不重试，记在 W010 |
| 2026-10-13 20:38 | edge 中继源 `5a25076a` | edge 中继 `hotfix-20261013T123830Z-5a25076a4894-10762`（仅 edge 中继） | W010 的 J（链接入群）和 C（一键建同步群）；owner 和控制台没有动 | 上一版 overlay `hotfix-20261013T100002Z-10d5f55bf233-24792` 保留；没有新表 | 状态脚本 ALL CHECKS PASSED；集成分支上 adapter 300 项、看板 16 项、edge 上 bridge 套件 190 项通过；线上重新生成邀请链接和凭链接预览正常；没有在线上真的建群和真的入群 |
| 2026-10-13 20:58 | edge 中继源 `85a5af5e` | edge 中继 `hotfix-20261013T125819Z-85a5af5ee34d-12722`（仅 edge 中继） | W010 的 M（目标群成员管理），以及飞书拒绝命令时显示飞书自己的原因 | 上一版 overlay 保留；没有新表 | 集成分支上 adapter 303 项、看板 18 项、edge 上 bridge 套件 197 项通过；线上成员列表、进退群记录、入群申请可读；发现看板不带请求体的 DELETE 被拒，下一版修 |
| 2026-10-13 21:04 | edge 中继源 `141fb2ea` | edge 中继 `hotfix-20261013T130447Z-141fb2ea8305-26916`（仅 edge 中继） | W010 的 D（送达核对与撤回同步），以及不带请求体的功能路由请求 | 上一版 overlay 保留；启动时新建表 `feishu_group_relay_target_deliveries` | 状态脚本 ALL CHECKS PASSED；集成分支上 adapter 310 项、看板 21 项、edge 上 bridge 套件 202 项通过；线上 DELETE 正常、bridge 每 60 秒收到观察名单；还没看到真实消息被核对和一次真实的同步撤回 |
| 2026-10-13 21:10 | edge 中继源 `764ca2b2` | edge 中继 `hotfix-20261013T131021Z-764ca2b22292-2811`（仅 edge 中继） | W010 的 U（加急告警） | 上一版 overlay 保留；启动时新建表 `feishu_group_relay_alert_settings` | 状态脚本 ALL CHECKS PASSED；集成分支上 adapter 317 项、看板 23 项、edge 上 bridge 套件 214 项通过；线上设置默认关闭、接收人列表可读；测试加急留给用户点 |
| 2026-10-13 21:34 | edge 中继源 `b311b53e` | edge 中继 `hotfix-20261013T133421Z-b311b53e2193-21767`（仅 edge 中继） | W010 的 E（会员期限与到期提醒），以及成员列表区分机器人和本账号 | 上一版 overlay 保留；启动时新建表 `feishu_group_relay_memberships`、`feishu_group_relay_membership_settings` | 状态脚本 ALL CHECKS PASSED；集成分支上 adapter 333 项、看板 29 项、edge 上 bridge 套件 216 项通过；线上自测群成员带两个标记、会员设置默认未启用、同步后没有给机器人和本账号建记录；到期提醒的真实发送没有验到 |
| 2026-10-13 22:24 | edge 中继源 `3c78e23e` | edge 中继 `hotfix-20261013T142445Z-3c78e23ec2e8-28449`（仅 edge 中继） | W010 的看板修复：“目标群”页的登记表和会员表读到的是未解包的 ref，表空、按钮恒转圈（用户报告）；另含 `WEB_GATEWAY.md` 的两处更正 | 上一版 overlay 保留；没有新表 | 状态脚本 ALL CHECKS PASSED；看板 32 项测试通过，其中 3 项用真实组件挂载面板和整页；没有在真浏览器里核对，等用户刷新确认 |
| 2026-10-13 22:50 | edge 中继源 `fb8feffd` | edge 中继 `hotfix-20261013T145014Z-fb8feffd6360-19358`（仅 edge 中继） | W010 会员期限：`date` 列按日历日期读回（此前状态恒为“正常”、接口回带时区的时间串）；预览和手动审查不再受“每天自动审查”开关限制 | 上一版 overlay 保留；没有新表 | 状态脚本 ALL CHECKS PASSED；anqiang 群 22 行日期和状态读回正确；预览回 400，暴露下一行修的问题 |
| 2026-10-13 22:55 | edge 中继源 `53ccf010` | edge 中继 `hotfix-20261013T145534Z-53ccf010c296-27015`（仅 edge 中继） | W010 会员期限：审查、预览、“再提醒一次”的语句在 PostgreSQL 上报 `timestamp with time zone < interval`，上线以来发不出任何提醒；参数加类型后可运行 | 上一版 overlay 保留；没有新表 | 状态脚本 ALL CHECKS PASSED；anqiang 群预览回 200、列出 3 人、不发送；本地临时 PostgreSQL 16 上模块 16 步全过（`docs/work/probes/2026-10-13-memberships-pg-probe.*`）；真实的一次发送未在线上验 |
| 2026-10-13 23:28 | edge 中继源 `a4680889` | edge 中继 `hotfix-20261013T152842Z-a46808893420-19935`（仅 edge 中继） | W010：“目标群”页列出本账号是群主的所有群（不只是转发目标）；会员行改按群的数字编号存，旧行第一次用到时搬过去；没有机器人的群可管成员和期限，发提醒前要先建机器人 | 上一版 overlay 保留；没有新表，`feishu_group_relay_target_groups` 多了以群编号为键的行，会员两张表的键从目标编号变为群编号（回退到旧代码会读不到已搬的行） | 状态脚本 ALL CHECKS PASSED；识别后 13 行（5 个目标＋8 个仅管理）；anqiang 群 22 行会员发布前后逐字段一致；擒龙群成员和会员接口可用；本地临时 PostgreSQL 16 上 21 步全过；没在真浏览器里核对 |
| 2026-10-13 23:51 | edge 中继源 `49253195` | edge 中继 `hotfix-20261013T155137Z-49253195dac9-7689`（仅 edge 中继） | W010：群可以借用已有的自定义机器人作发送出口（决定 0022）——bridge 多两个只读接口，adapter 存地址时不发测试消息、不接管机器人；看板多“用已有机器人” | 上一版 overlay 保留；没有新表 | 状态脚本 ALL CHECKS PASSED；bridge 全套在 edge 上 221 项通过；擒龙群接入 itougu 监听在用的机器人：回 200、回应里没有地址、会员 10 行前后一致、itougu 监听仍 healthy；经它真的发一条提醒未做 |
| 2026-10-14 00:13 | edge 中继源 `34ff843b` | edge 中继 `hotfix-20261013T161335Z-34ff843b4f50-21014`（仅 edge 中继） | W010：会员表显示每个成员的备注名，编辑框里给出按备注名读出的日期和期限供点选；可以指定到期日 | 上一版 overlay 保留；`feishu_group_relay_memberships` 启动时加两列 `alias`、`expiry_fixed`（`ADD COLUMN IF NOT EXISTS`，回退到旧代码不受影响） | 状态脚本 ALL CHECKS PASSED；bridge 全套在 edge 上 222 项；anqiang 群和擒龙群同步后备注名入库（22/22、9/10），日期和备注栏前后一致；本地临时 PostgreSQL 16 上从旧表结构起步 11 步全过；没在真浏览器里核对 |
| 2026-10-14 00:22 | edge 中继源 `83b5d49e` | edge 中继 `hotfix-20261013T162249Z-83b5d49e0b70-12319`（仅 edge 中继） | W010：保存会员时可一并改飞书里的备注名（bridge 多一个写备注名的接口；编辑框里“按下面的日期改写”） | 上一版 overlay 保留；没有新表新列 | 状态脚本 ALL CHECKS PASSED；bridge 全套在 edge 上 224 项；写备注名的命令由用户当天的探测证明可用；经线上接口的第一次真实写入留给用户在看板上做；没在真浏览器里核对 |
