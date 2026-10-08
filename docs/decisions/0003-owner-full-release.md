# 0003 owner 全量发布：一条命令、守护锁、按序启动、失败即回滚

- 状态：生效
- 日期：2026-10-09

## 背景

2026-10-08 的手工发布暴露了几处问题。git 归档不带 `certs/` 目录，激活脚本因此失败；
自愈定时器会在发布中途重启服务；改写 `.env` 会把指向 `~/.secrets/owner` 的软链接
换成普通文件；重建隧道会连带重启依赖它的主服务。回滚点也全靠操作员记住。

## 决定

- 47owner 的全量发布只用 `scripts/shared-peer/deploy-full-release.sh <sha> <label> --apply`。
- 只发布 `origin/main` 上的提交。禁止重启的时间窗写成数据（`config/release-windows.json`，交易日 08:30–15:10，
  scheduler 另加 18:45–22:05），所有会重启服务的发布脚本都先用 `scripts/release_window.py` 检查。
- 发布前从主机上读出回滚点（上一个 release、wheelhouse、元数据、镜像），并给镜像
  打 `rollback-<label>` 标签。
- 发布期间持有 `release-lock.sh` 守护锁：heal 定时器见锁即退出，preopen 只记录问题、
  不重启。
- 启动顺序固定：不重建隧道，主服务健康之后再启动 scheduler。
- 两个端口都核验 `git_sha`、profile 和凭据软链接；任何一步失败都自动回滚，并释放锁。
- 只改 `app/` 等运行时代码的变更可以走 `deploy-code-only.sh` 覆盖层；迁移、依赖和
  镜像的变更必须走全量发布。
- 永远不运行 `deploy-intraday-edge-release.sh`，也不启用 `quant-intraday-edge`。

## 后果

一次发布约两分半钟，期间主服务约 25 秒不可用。旧 release、镜像和覆盖层都不删除。

## 怎样保证

`scripts/test_deploy_full_release.py` 覆盖时间窗、主线、守护锁、迁移闸门，以及远端
步骤的顺序；`scripts/peer-session-guard.sh` 遵守守护锁。
