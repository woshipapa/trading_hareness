# quant-service 热部署

`scripts/hotfix-quant-service.sh` 提供一个不重建 Docker image 的 source-only
热部署路径，适合小范围 Python 应用修复和研究代码迭代。

## 允许的范围

- 只覆盖 `quant-service/app/`；覆盖目录在容器内以只读方式挂载到
  `/app/hotfix`。
- 每次生成独立的 `hotfix-...` release，并通过 `current.next -> current` 原子切换。
- 切换后使用 `docker compose up -d --no-build --pull never --force-recreate`，
  只重建容器，不构建或拉取 image。
- 启动后检查 `/health` 和容器内 `/app/hotfix/current/app/main.py`，失败会恢复上一份
  source release 并关闭 overlay。
- 保留有限的旧 release，可用 `--rollback ID` 回滚，`--disable` 恢复 image 内代码。

## 不能使用热部署的情况

以下变更必须走正常的 immutable image/release 流程：

- `requirements.txt` 或任何 Python/系统依赖变化；
- Dockerfile、基础镜像、entrypoint、compose、迁移或数据库 schema 变化；
- 需要新增环境变量、secret 或 systemd 任务的变更；
- 需要重建 wheelhouse 的变更。

## 本机示例

```bash
scripts/hotfix-quant-service.sh
scripts/hotfix-quant-service.sh --apply
scripts/hotfix-quant-service.sh --list
scripts/hotfix-quant-service.sh --rollback hotfix-20260921T010000Z-abc1234 --apply
scripts/hotfix-quant-service.sh --disable --apply
```

## shared-peer 示例

```bash
QUANT_HOTFIX_COMPOSE_FILES='deploy/shared-peer/compose.yaml deploy/shared-peer/compose.intraday-owner.yaml' \
QUANT_HOTFIX_ENV_FILE=deploy/shared-peer/.env \
QUANT_HOTFIX_HOST_DIR=/home/stockpeer/trading_hareness/hotfix/quant-service \
QUANT_HOTFIX_SERVICES='quant-research quant-research-scheduler' \
QUANT_HOTFIX_HEALTH_URLS='http://127.0.0.1:15682/health http://127.0.0.1:15683/health' \
scripts/hotfix-quant-service.sh --apply
```

shared-peer 的 quant-research 和 scheduler 会在同一次切换中使用同一个 overlay。首次启用前仍应
在非交易窗口做 `/health`、owner contract、5432/5433 和任务租约验收。
