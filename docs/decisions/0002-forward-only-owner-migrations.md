# 0002 迁移只向前，由 owner 在 Windows 上执行，发布前核对库版本

- 状态：生效
- 日期：2026-10-09

## 背景

系统的数据库在 owner 的 Windows 工作站上（PostgreSQL 55432），47owner 上的服务
经反向 SSH 隧道访问它。DDL 只能由 owner 在 Windows 上执行（RELEASE_SYNC_47 阶段 D）。
以前发布脚本不知道库停在哪个版本，代码有可能先于它依赖的表结构上线。

## 决定

- Alembic 迁移只向前，永不 `downgrade`；失败的迁移不手工"补半截"。
- 找不到源码的历史迁移不凭记忆重建，必须找回原文件。
- 迁移在 owner 的 Windows 工作站上执行，且先于任何代码切换。
- 发布命令在动手之前比较归档里的迁移 head 和运行中服务 `/health` 报告的
  `owner_storage.database_lineage.alembic_version`：库落后就拒绝，且不可覆盖；
  库版本未知或读不到时，只有明确写出所见版本号才放行。

## 后果

- 发布前必须先完成阶段 D，否则发布在改动任何东西之前就失败，并列出缺哪些迁移。
- 仓库里的迁移链必须只有一个 head；并行分支各自加迁移时要补 merge 迁移。

## 怎样保证

- `scripts/shared-peer/deploy-full-release.sh` 的迁移闸门（退出码 6）。
- `quant-service/scripts/migration_lineage.py`（只用标准库，可离线读迁移链）。
- `scripts/test_migration_lineage.py` 断言仓库只有一个迁移 head。
- `deploy-code-only.sh` 遇到迁移文件的实质改动直接拒绝。
