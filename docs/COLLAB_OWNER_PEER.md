# 与 owner 方的协作分支 `collab/owner-peer`

## 为什么要有它

2026-10-09 下午，owner 方三次直接在 owner 主机上构建并换上新镜像，改动只存在于镜像里。我们要发布，只能先逐文件比对容器、把改动并进 main，否则就会把对方的修复冲掉。那天我们第一次发布就被指纹门禁拦了下来，因为对方又换了一次镜像。

协作分支让双方的代码改动都走 git。发布仍然只从 `main` 出（见 `RELEASE_SYNC_47.md`）。

## 分支与角色

| 分支 | 谁提交 | 用途 |
| --- | --- | --- |
| `main` | 我方（含本地各代理） | 集成分支，也是唯一的发布来源 |
| `collab/owner-peer` | 双方 | 对方对 peer 侧代码的改动提交到这里；我方需要对方看到的改动，用 `sync` 带过来 |

提交作者要能区分双方，监听脚本靠作者名判断：

- 我方是 `woshipapa`；
- 对方请在自己的 clone 里固定一个名字，例如 `git config user.name owner-windows`。

## 规则

服务端拦不住这些行为：这个私有仓库在免费套餐上，没有分支保护和规则集。所以靠约定，再由下面的工具兜底。

1. **不改写历史。** 协作分支上不 rebase，不 amend 已推送的提交，不强推，不删分支。一旦改写，监听脚本会在群里告警，`check` 也会失败。
2. **改动不再只留在镜像里。** peer 侧的改动先提交到协作分支，由我方合并、发布。如果紧急修复必须先上线，也要在 24 小时内把同样的改动提交到协作分支。在此之前，我方的发布指纹门禁会拦下发布。
3. **只有一条迁移链。**
   - 新迁移只在协作分支上加，`down_revision` 必须是协作分支当前唯一的 head。
   - 编号用 `YYYYMMDD_<side><nnnn>`：我方 `pr`，对方 `ow`，例如 `20261010_ow0120`，避免撞号。
   - 已经合并的迁移文件永远不改。
   - 补交 0118、0119 这类已经在共享库上执行过的迁移时，要连同它们的前序迁移一起提交，保证空库也能从头初始化，release-path 测试会验证这一点。
4. **不提交密钥。** `.env`、`*-secrets.env`、私钥、证书一律不进仓库，`check` 会拦下。
5. **冲突在协作分支上用合并提交解决。** 冲突涉及对方的代码时，先在群里确认再解决。

## 日常流程

### 对方提交

```bash
git fetch origin
git checkout collab/owner-peer && git pull --ff-only
# 修改、跑测试、提交
git push origin collab/owner-peer        # 普通推送，不加 --force
```

我方的监听在 5 分钟内会把新提交发到运维群。

### 我方把 main 同步给对方

```bash
python3 scripts/collab_branch.py sync
```

- 协作分支上没有自己的提交时，直接快进到 main。
- 有自己的提交时，做一次合并提交（不 rebase），然后检查合并后的迁移链，并跑全量测试。
- 有冲突时停下，按规则 5 处理。

### 合并进 main（我方执行）

1. `python3 scripts/collab_branch.py check`：只读，逐项列出 PASS 或 FAIL。
2. 全部通过后执行 `python3 scripts/collab_branch.py merge`。它会再检查一遍，通过后用 `--no-ff` 合并并推送 main，再把协作分支快进到 main，双方回到同一起点。
3. 发布照旧：`scripts/release owner full <main 上的 sha> …`。发布前的容器指纹门禁也照旧。

整个过程都在临时工作树里进行，不碰共享检出，也从不强推。如果检查期间 main 或协作分支被别人推进，推送会被拒绝，重跑即可。

### `check` 与 `merge` 检查什么

| 检查 | 拦什么 |
| --- | --- |
| 历史未改写 | 上次合并进 main 的协作提交已不在分支上，说明有人强推过 |
| 无冲突 | main 与协作分支合不到一起 |
| 迁移链 | 合并后不止一个 head、编号重复、或前序不存在 |
| 已有迁移未改 | main 上已有的迁移文件被修改或删除 |
| 无密钥文件 | 带进来的改动里有 `.env`、密钥或证书 |
| 仓库检查 | 架构检查不通过，或架构索引、脚本目录没有更新；ruff 只作提示 |
| 全量测试 | 合并结果上的 quant-service 测试不通过。测试用我方 main 上的 `scripts/collab_isolated_tests.sh` 隔离运行：不带密钥、不连外网、一次性数据库 |

## 监听

- **我方**：supervisor 任务 `owner-collab.watch`，每 5 分钟跑一次 `scripts/owner_collab_watch.py`。它只报对方作者的新提交；分支历史一旦被改写，会单独告警。飞书 webhook 放在 `config/secrets/.env.local` 的 `OWNER_COLLAB_FEISHU_WEBHOOK_URL`，没配置时只写日志。
- **对方**：可以直接用同一个脚本，`OWNER_COLLAB_OUR_AUTHORS=<你们的作者名> python3 scripts/owner_collab_watch.py`，接上你们自己的通知方式；也可以用 GitHub 的 Watch 通知。

## 权限

仓库 `woshipapa/trading_hareness` 是私有的。对方要以协作者身份加入（Settings → Collaborators）才能推送；这一步由仓库所有者操作。
