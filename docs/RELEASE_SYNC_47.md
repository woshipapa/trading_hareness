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
| **47 owner**（Longhu 主机 / lightServer1）`stockpeer@47.110.79.189 -p 3535`，密钥 `stockpeer_ed25519`，rootless Docker，compose 项目 `trading-hareness-peer` | `db-tunnel` | release 归档 + `scripts/shared-peer/activate-peer-release.sh` + compose | 容器健康 |
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
6. 交易时段不发布。交易日 08:30–15:10 不要重启 owner 的 `quant-research`、Windows API 或 edge adapter；18:45–22:05 不要重启 owner 的 `quant-research-scheduler`（盘后调度在跑）。安全窗口：交易日 22:10 至次日 08:00，或周末。
7. 不删除旧的 release 目录、旧镜像、旧 overlay，回滚依赖它们。
8. 任何通过条件不满足时停止，按第 12 节回滚，再向用户报告实际输出。

---

## 3. 前置条件

- 工具：`git`、`ssh`、`scp`、`rsync`、`python3`、`node` 22、`npm`、`jq`、`docker`（本机拉取镜像回退时使用）、`shasum`、`diff`。Windows 步骤需要 PowerShell 7（`pwsh`）。
- 密钥（不要拷贝进仓库）：

  ```bash
  export RELAY_EDGE_SSH_KEY=~/.ssh/feishu_relay_edge_ed25519   # 47 edge root
  export OWNER_PEER_SSH_KEY=~/.ssh/stockpeer_ed25519           # 47 owner stockpeer，端口 3535
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
ssh -i "$OWNER_PEER_SSH_KEY" -p 3535 stockpeer@47.110.79.189 \
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

`docs/PLAN_COMPLETION_MATRIX.md` 的 2026-09-20 owner clarification 写明：**owner 不会为 peer 执行 ds0004/ds0005 的 DDL**。所以在 owner 库上直接 `alembic upgrade head`，会把 owner 已经拒绝的 DDL 一起执行，而且 ds0004 要改写几张大表。

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

1. **打包**（工作站上执行）：

   ```bash
   git archive --format=tar --prefix=trading_hareness/ -o "/tmp/trading_hareness-$X.tar" "$X"
   ```

   wheelhouse：如果 `quant-service/requirements.txt` 自 owner 上次发布以来没有变化（PR #2 没有改它），就直接复用 owner 上现有的 wheelhouse，在 owner 上打包：

   ```bash
   ssh -i "$OWNER_PEER_SSH_KEY" -p 3535 stockpeer@47.110.79.189 \
     'tar -C ~ -chf /tmp/wheelhouse-current.tar wheelhouse && tar -tf /tmp/wheelhouse-current.tar | grep -c SHA256SUMS'
   ```

   如果依赖有变化，在 Windows 上执行 `pwsh .\scripts\shared-peer\build-peer-wheelhouse.ps1`，再执行 `tar -C G:\StockPlatform\peer\staging -cf wheelhouse.tar wheelhouse` 并上传。

2. **上传并激活**。首次同步必须使用新版本里的激活脚本，因为旧版脚本不会保留 `intraday-secrets.env`：

   ```bash
   O="-i $OWNER_PEER_SSH_KEY -P 3535"
   scp $O "/tmp/trading_hareness-$X.tar" stockpeer@47.110.79.189:/tmp/
   ssh -i "$OWNER_PEER_SSH_KEY" -p 3535 stockpeer@47.110.79.189 "set -euo pipefail
     tar -xOf /tmp/trading_hareness-$X.tar trading_hareness/scripts/shared-peer/activate-peer-release.sh > /tmp/activate-peer-release.sh
     RELEASE_ID='$L' bash /tmp/activate-peer-release.sh /tmp/trading_hareness-$X.tar /tmp/wheelhouse-current.tar
     test -s ~/trading_hareness/deploy/shared-peer/.env
     test -s ~/trading_hareness/deploy/shared-peer/intraday-secrets.env && echo secrets-ok"
   ```

   如果 `intraday-secrets.env` 缺失，从上一个 release 目录拷回来（路径见阶段 A 记录的回滚点）：`install -m 0600 <上一个release>/deploy/shared-peer/intraday-secrets.env ~/trading_hareness/deploy/shared-peer/`。

3. **构建并重建容器**。`/health` 的版本号来自 `deploy/shared-peer/.env` 里的 `PEER_APP_GIT_SHA`、`PEER_APP_RELEASE`、`PEER_APP_BUILD_CREATED_AT`（compose 把它们作为构建参数传入）。`verify-owner-cutover.py` 则读 `PEER_EXPECTED_RELEASE`。激活脚本只会在这些键还是占位值时才填写，所以第二次及以后的发布会沿用上一次的值，必须在构建前显式改写。下面的写法只改这 4 个非密钥键，不打印 `.env`：

   ```bash
   ssh -i "$OWNER_PEER_SSH_KEY" -p 3535 stockpeer@47.110.79.189 "set -euo pipefail
     export XDG_RUNTIME_DIR=/run/user/\$(id -u) DOCKER_HOST=unix:///run/user/\$(id -u)/docker.sock
     cd ~/trading_hareness/deploy/shared-peer
     python3 - .env '$X' '$L' <<'PY'
   import datetime, os, pathlib, sys
   path, sha, label = pathlib.Path(sys.argv[1]), sys.argv[2], sys.argv[3]
   wanted = {'PEER_APP_GIT_SHA': sha, 'PEER_APP_RELEASE': label, 'PEER_EXPECTED_RELEASE': label,
             'PEER_APP_BUILD_CREATED_AT': datetime.datetime.now(datetime.timezone.utc).strftime('%Y-%m-%dT%H:%M:%SZ')}
   lines = [line for line in path.read_text().splitlines() if line.split('=', 1)[0] not in wanted]
   tmp = path.with_suffix('.tmp'); tmp.write_text('\\n'.join(lines + [f'{k}={v}' for k, v in wanted.items()]) + '\\n')
   os.chmod(tmp, 0o600); tmp.replace(path)
   PY
     C='docker compose -f compose.yaml -f compose.intraday-owner.yaml'
     \$C config --quiet
     \$C build quant-research
     \$C up -d --no-build --wait db-tunnel quant-research quant-research-scheduler
     \$C ps"
   ```

   `quant-research-scheduler` 复用 `quant-research` 构建出来的镜像（`PEER_QUANT_IMAGE`，默认 `trading-hareness-peer-quant-research:latest`），所以只需要构建一次。

4. **systemd 单元**：只有 `deploy/shared-peer/systemd/` 有改动时才执行：

   ```bash
   ssh ... 'install -m 0644 ~/trading_hareness/deploy/shared-peer/systemd/* ~/.config/systemd/user/ && systemctl --user daemon-reload && systemctl --user list-timers | grep -E "peer-(session-guard|daily-backfill)"'
   ```

5. **核验**：

   ```bash
   ssh -i "$OWNER_PEER_SSH_KEY" -p 3535 stockpeer@47.110.79.189 'set -a; . ~/trading_hareness/deploy/shared-peer/intraday-secrets.env; set +a
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

脚本会先比较两个 Git SHA，并**逐个路径**校验：只允许 `quant-service/app/*`、
`entrypoint.py`、`run_server.py`、`database_bootstrap.py`、`alembic.ini`，其余一律
`full release required for: <path>` 并退出。**注意 `quant-service/tests/**` 不在白名单里** ——
「改 `app/` 顺手改测试」放在同一个提交里会被拒；请把 `app/` 单独成一个提交，测试与文档另提。
横跨多个目录的批量提交（例如工作站同步产生的那种）同样过不了快速通道。
出现 `requirements.txt`、Dockerfile、compose、迁移或
其他构建文件变化时直接拒绝，必须回到完整镜像发布和数据库迁移流程；纯代码发布
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
  `source-overlay`、overlay 标志必须为 `true`；镜像标签可以为空（overlay 复用现有镜像），
  若存在则必须指向同一个 SHA；
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

1. 所有改动经 PR 合入 `main`。overlay hotfix 只能作为临时手段，24 小时内必须提交并走正式发布，**不允许**长期运行在 `-dirty` 或 `hotfix-*` 上。
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
| | | | | | | |
