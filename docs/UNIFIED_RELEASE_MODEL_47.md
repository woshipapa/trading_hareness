# 47 多组件统一发布规范

更新时间：2026-09-29。

本仓库是一个 monorepo，但生产运行面不是一个进程，也不是一个镜像。`feishu-relay`、`xhs-intel` 和 `quant-service` 可以在不同主机、不同容器、不同发布节奏下运行。统一的目标是让它们共享同一套 Git 工作流、发布记录、健康检查和回滚规则，而不是强行让所有运行面使用同一个 Git SHA。

## 1. 组件边界

| 组件 | 代码范围 | 运行位置 | 版本身份 | 普通源码更新 | 必须完整发布 |
| --- | --- | --- | --- | --- | --- |
| `edge-relay` | `feishu-relay/adapter`、`feishu-relay/bridge`、`feishu-relay/dashboard`、对应 edge deploy 文件 | 47edge | `edge_git_sha`、overlay release、基础镜像 digest | `hotfix-feishu-relay-edge.sh --apply`，复用 adapter 镜像 | `package.json`、Node 依赖、Dockerfile、compose、systemd 或底层运行库变化 |
| `edge-workflows` | `workflows/edge-relay`、n8n workflow JSON | 47edge n8n | workflow ID、definition hash、n8n activation 状态 | `verify-edge-relay-workflows.sh`，漂移时按 workflow 脚本发布 | n8n 版本、数据库、加密配置或 runner contract 变化 |
| `edge-xhs` | `xhs-intel`、固定版本的 `Spider_XHS`、XHS workflow | 47edge | `xhs_git_sha`、source tree digest、collector image digest、workflow hash | 当前脚本会上传源代码并复用已有 collector image；变更后必须验证健康和 workflow activation | Python/Node 依赖、Dockerfile、基础镜像或采集器运行 contract 变化 |
| `owner-quant` | `quant-service/app`、策略与研究模块、owner deploy 文件 | 47owner | `owner_git_sha`、owner release、image digest、runtime profile | `scripts/shared-peer/deploy-code-only.sh`，复用 owner 基础镜像 | requirements、Dockerfile、compose、systemd、运行时 Python 依赖或 API contract 变化 |
| `owner-schema` | `quant-service/migrations/versions` | owner 数据库 | Alembic head、迁移 SQL 审计记录 | 不适用 | 任何迁移都先迁移数据库，再切代码；禁止 downgrade |

`frontend/` 是 quant shell，`feishu-relay/dashboard/` 是 Feishu shell。两者可以在同一次 edge relay overlay 中构建和发布，但 UI 源码仍由各自目录负责，不能互相导入业务组件。策略注册和老师策略仍保持 `live_effect=none`，代码发布不等于策略获得执行权限。

## 2. 发布身份

每次发布都记录一个组件 manifest，而不是只记录一个全局 `X`：

```json
{
  "release_id": "20260929-xxxx",
  "repo": "woshipapa/trading_hareness",
  "edge_git_sha": "...",
  "owner_git_sha": "...",
  "xhs_git_sha": "...",
  "edge_base_image_digest": "sha256:...",
  "owner_base_image_digest": "sha256:...",
  "xhs_image_digest": "sha256:...",
  "edge_workflow_hash": "...",
  "xhs_workflow_hash": "...",
  "database_head": "...",
  "migration_required": false
}
```

manifest 不包含 token、cookie、webhook URL、`.env` 或 SSH key。每个主机的 secret env 由主机单独保存，发布时只校验键是否存在和服务是否能工作。

发布验收使用组件 SHA：

```bash
scripts/release-sync-status.sh \
  --sha origin/main \
  --edge-source-sha <edge_git_sha> \
  --owner-source-sha <owner_git_sha>
```

如果某一侧使用固定镜像，不传对应的 `--*-source-sha`，巡检会要求镜像版本与 `--sha` 一致；如果某一侧使用源码 overlay，就传该组件实际运行的干净 SHA，巡检会检查 overlay base SHA、运行来源和 bridge release。不要把 owner 的量化提交 SHA 填给 edge relay，也不要为了让巡检变绿而修改 `/health` 元数据。

## 3. 开发规则

1. 所有运行代码仍进入本仓库的 `main`，不维护 edge、owner、XHS 的长期漂移分支。功能分支只承载一个可审阅的变更集，合并后从干净 detached worktree 发布。
2. 先按路径计算影响组件：

   ```bash
   git diff --name-only <previous-main> <candidate-main>
   ```

   只要包含迁移、依赖锁文件、Dockerfile、compose、systemd 或 workflow contract，就把发布级别提升为完整发布。
3. adapter、bridge、quant app、策略、前端的普通源码变更走源码 overlay，复用已经验证的基础镜像。overlay 必须来自干净 Git SHA，远端原子切换 `current` 指针，失败自动恢复旧指针和旧 metadata。
4. XHS 的 Cookie、collector token、Feishu webhook 和运行状态永远留在 edge 主机。`Spider_XHS` 目前位于仓库外，不能把“本机目录存在”当作可复现版本；在它纳入本仓库前，发布记录必须保存其 Git commit 或内容 digest。长期方案是将其作为固定 commit 的 submodule 或纳入受许可的 `xhs-source/` 目录。
5. owner 的数据库迁移先于 owner 代码切换。迁移完成后读回 `quant.alembic_version`、关键表/列和唯一 head，再发布 owner 容器。owner 服务使用 `intraday_edge`，scheduler 使用 `research`；禁止重新启用已经退役的 edge writer。
6. 一个组件失败只回滚该组件。owner schema 迁移只向前，应用必须先验证向后兼容；edge relay、XHS collector 和 n8n workflow 各自保留一个可激活的上一版本。

## 4. 发布顺序

1. 在安全窗口读取北京时间。交易日 `09:00-12:00` 和 `13:00-15:00` 禁止重启生产 writer；用户已批准的午间窗口 `12:00-13:00` 可以发布。周末和交易日 `15:00` 后至次日开盘前可发布，scheduler 仍需避开其盘后运行窗口。
2. `git fetch origin --tags`，创建干净 detached worktree，确认 `git status --porcelain` 为空。
3. 运行组件测试和 `git diff --check`，生成 component manifest，记录 edge、owner、XHS 和数据库回滚点。
4. 有迁移时先在目标数据库执行并核验；没有迁移时明确记录 `migration_required=false`。
5. 发布 edge relay overlay，验收 adapter `/health`、LarkAgentX `/health`、WebSocket state、监听群数量、接收/转发/失败/解码计数、webhook keyword coverage，并确认 retired quant writer 为 inactive/disabled。
6. 发布或核验 edge workflows；如果 XHS 源码、依赖或 workflow 有变，执行 XHS 部署脚本并核验 collector `/health`、n8n `/healthz`、runner registration 和 workflow activation。
7. 发布 owner code-only 或完整镜像，验收 `15682`、`15683`、runtime profile、数据库 lineage、scheduler 状态和关键 API。
8. 以组件 SHA 重新运行巡检，保存输出和 manifest。任何 FAIL 都停止，不用手工改 env 冒充成功。

## 5. 远端目录约定

- edge relay：`/opt/feishu-relay-edge/hotfix/releases/<release-id>`，`hotfix/current` 是原子 symlink；旧 release 保留有限窗口。
- edge XHS：`/opt/feishu-relay-edge/xhs-intel`、`xhs-source` 和 `xhs-state`；后续应迁移到带 release ID 的 `xhs/releases/<release-id>` 与 `xhs/current`。
- owner quant：`~/trading_hareness/releases/<release-id>/trading_hareness`，`~/trading_hareness` 是原子 symlink；容器只能通过 compose 读取当前 release。
- secrets：`/etc/feishu-relay-edge/runtime.env`、`/etc/feishu-relay-edge/secrets.env`、owner `deploy/shared-peer/.env` 和 `intraday-secrets.env`，禁止进入 release archive。

## 6. 当前核验结论

2026-09-29 午间窗口的实际状态是：owner 已运行 `20a897b` 的 teacher-review 修复；edge relay 已运行 `55213b0` 的源码 overlay；edge adapter、n8n、XHS collector 健康，LarkAgentX WebSocket 为 `connected`，webhook keyword coverage 检查通过。由于它们是独立组件，不能用一个 SHA 判定三者是否一致；必须使用本文件的 component manifest 和 `--edge-source-sha`/`--owner-source-sha` 巡检参数。

本次读取到的 relay health 还显示 `listen_chat_count=16`，因此“页面曾显示 19 个群”不能作为当前完整监听证明。后续 dashboard 应展示 source registry、实际 allowlist、动态绑定数和每个 chat 的接收/转发/失败计数，并将历史记录写入持久化 ledger，重启后不得归零。
