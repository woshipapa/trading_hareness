# 架构决定记录（ADR）

每份记录一个已经做出的架构决定：当时的背景、决定本身、带来的后果，以及由什么
机制保证它不被悄悄违反。决定改变时不改旧记录，而是新写一份并在旧记录的状态里
写"被 NNNN 取代"。

| 编号 | 决定 | 状态 |
| --- | --- | --- |
| [0001](0001-data-ownership.md) | 每张表只有一个所有者组件，跨组件读写必须登记 | 生效 |
| [0002](0002-forward-only-owner-migrations.md) | 迁移只向前，由 owner 在 Windows 上执行，发布前核对库版本 | 生效 |
| [0003](0003-owner-full-release.md) | owner 全量发布：一条命令、守护锁、按序启动、失败即回滚 | 生效 |
| [0004](0004-scoped-write-keys.md) | 写密钥按调用方分开并限定路径 | 生效（调用方迁移中） |
| [0005](0005-retire-tushare.md) | 停用 Tushare，`datasources/` 是唯一的数据源层 | 生效（代码删除中） |
| [0006](0006-research-fail-closed.md) | 研究与老师策略默认不产生实盘效果 | 生效 |
| [0007](0007-host-registry.md) | 每个后台任务都登记在某台主机上，退役任务默认不启动 | 生效 |
| [0008](0008-main-composition-root.md) | 拆分 main.py：运行时上下文与按领域的装配模块 | 生效（进行中） |
| [0009](0009-minute-cross-section-storage.md) | 全 A 分钟截面一分钟存一行，最新截面走内存 | 提议 |

格式：

```markdown
# NNNN 标题

- 状态：生效 | 被 NNNN 取代 | 已撤销
- 日期：YYYY-MM-DD

## 背景
## 决定
## 后果
## 怎样保证
```
