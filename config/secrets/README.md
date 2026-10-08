# config/secrets/ — 本地凭据集中目录

> ⚠️ **此目录下除 README.md 和 *.example 之外的所有文件已被 .gitignore 忽略。**
> **绝不可出现在 git 提交或推送中。**

## 目录结构

```
config/secrets/
├── README.md             ← 本文件，唯一入库的
├── env.example           ← 公开模板（只有键名，没有值）
├── .env.local            ← 本机开发/中继凭据（Mac）
├── .env.owner            ← 47owner 的凭据子集
├── .env.edge             ← 47edge  的凭据子集
├── certs/                ← PEM / 密钥文件
│   └── market-review-api.pem
└── sync-secrets.sh       ← 把 .env.owner/.env.edge 推到远端的脚本
```

## 设计原则

1. **一份凭据地图，一个存放目录。** 不再散落在 `n8n/.env`、
   `deploy/shared-peer/.env`、`state/wechat-relay.env` 等多处。根 `.env` 作为
   向后兼容的 compose 注入源保留，但**它只做符号链接**：
   `ln -sf config/secrets/.env.local .env`
2. **值永远不入库。** `.gitignore` 用 `config/secrets/*` + `!config/secrets/README.md`
   + `!config/secrets/env.example` 精准兜住。
3. **每个部署环境一个文件。** `.env.local`（Mac 本机 + 中继）、`.env.owner`
  （47owner quant-research）、`.env.edge`（47edge feishu-relay + xhs-intel）。
   键全部统一命名，环境间不同值的同名键在各自文件里分别给值。
4. **同步到远端走脚本。** `sync-secrets.sh` 用 rsync over SSH 把对应的 env 文件
   推到远端约定路径（`/etc/quant-intraday-edge.env` 等），不会传错文件。
5. **example 文件跟着仓库走。** `env.example` 列出所有键名和注释，新 agent 或
   新成员只需 `cp env.example .env.local` 然后填值。

## 迁移步骤

原来的 `n8n/.env` 继续可用（compose 的 `env_file` 指向它），但推荐改成符号链接：

```bash
# 1. 把现有 .env 移进来
mv .env config/secrets/.env.local

# 2. 根目录留一个符号链接
ln -sf config/secrets/.env.local .env

# 3. 把 deploy/shared-peer/.env 合并到 .env.local（如果值不同则分环境）
cat deploy/shared-peer/.env >> config/secrets/.env.local
# 删掉重复行，把 peer 专有值移到 .env.owner

# 4. 同理处理 deploy/intraday-edge/edge.env → .env.edge
# 5. state/wechat-relay.env → 并入 .env.local
```

## 远端同步

```bash
bash config/secrets/sync-secrets.sh owner   # 只推 .env.owner
bash config/secrets/sync-secrets.sh edge    # 只推 .env.edge
bash config/secrets/sync-secrets.sh all     # 两个一起
```

## 轮换

轮换一个凭据后：
1. 改 `config/secrets/.env.local`（和对应的 `.env.owner` / `.env.edge`）
2. `sync-secrets.sh <env>` 推到远端
3. 重启相关服务（本地 `launchctl kickstart`；远端 `docker compose restart <svc>`）

永远不在 chat/log/commit 里出现值本身。
