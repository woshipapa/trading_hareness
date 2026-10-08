# 0005 停用 Tushare，`datasources/` 是唯一的数据源层

- 状态：生效（代码删除中）
- 日期：2026-10-08

## 背景

Tushare 及其兼容网关（super、promax、datahub）曾是主要的行情来源，后来被 Longhu
（`longhuvip_composite`）、Fuyao、cninfo 和公开行情取代。但 Tushare 的代码一直留着：
`tushare_providers.py`、`tushare_official.py`、`capability_registry.py`、
`tushare_catalog*.py`，二十多个模块依赖它们，面板也还有 Tushare 面板。两套数据源层
并存，新代码不知道该用哪一套。

## 决定

- 运营者决定（2026-10-08）：不再使用 Tushare，删除相关凭据和代码，能力改用其他
  数据源的接口。
- `env-split.py` 不再生成 `TUSHARE_*` 凭据，没有凭据的 Tushare 代码路径处于休眠状态。
- `app/datasources/` 是唯一的数据源层；Tushare 的模块、路由、面板和测试分步删除。

## 后果

- 依赖 Tushare 网关、又没有替代来源的能力会丢失；逐项列出，交给运营者决定。
- Tushare 相关的文档在代码删除时一起归档。

## 怎样保证

删除完成后，`git grep -i tushare -- quant-service/app` 只剩历史迁移和数据血缘里的
提供方名称。
