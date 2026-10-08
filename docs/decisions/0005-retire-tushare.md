# 0005 停用 Tushare，`datasources/` 是唯一的数据源层

- 状态：生效（代码删除进行中，剩余部分待定数据源）
- 日期：2026-10-08，2026-10-09 更新

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

## 已完成（2026-10-09）

- 两条在没有凭据时会在盘中抛异常的路径已修复（`b41d39f`）：观察池扫描的 ProMax 量能兜底，
  以及板块报告的涨停锚点。
- 已删除：
  - super_get 秒级报价交叉确认和 rt_min 分钟验证（`8fd32e4`）；
  - 只服务 Tushare 的路由（`/providers/tushare/{catalog,fetch,audit}`、`/providers/realtime/probe`、
    `/market/sync/tushare{,/core}`），以及面板、relay 和工作流里对应的调用方（`461c290`、`ce1eb12`）；
  - 历史回填 CLI（`9ae0a90`）；
  - 启动时的 Tushare 能力目录投影（`86e795e`）。
- 前向交易日历改由 Fuyao `a_share_trading_days` 补充，并与已有日历逐日对账（`f9fefae`）。

## 仍依赖 Tushare 的功能（凭据已移除，这些路径目前失败或空转）

| 功能 | 现状 | 可选替代 | 需要的决定 |
| --- | --- | --- | --- |
| 当日涨跌停价（`daily_trade_limits`，盘中小杰和涨停锚点、盘后对账） | 没有写入方，小杰标为 degraded | 腾讯或东财行情里交易所公布的涨跌停价；或按板块规则推算（2026-09-01 曾决定不把推算值写进规范表） | 选来源，或接受这项能力缺失 |
| 停牌（`suspend_d`） | 盘后控制同步会请求 Tushare | 改为可选；或用 Fuyao/Longhu 的停牌信号 | 改为可选，还是要求有来源 |
| 同花顺板块目录、概念资金流、概念成分、涨停强度 | 相关路由和回填循环返回 503 | Fuyao 的同花顺指数目录和成分（`/api/a-share-index/...`） | 按 Fuyao 重写，还是下线这些功能 |
| 龙虎榜席位（`top_list`/`top_inst`） | 读的是历史原始行 | Fuyao `a_share_dragon_tiger_list` | 是否接入 |
| 个股资金流（`moneyflow*`）与特征快照里的资金流特征 | 每日管线失败一次 | Longhu 主力净流（口径不同） | 是否换口径 |
| 财报披露日历 | 每日管线失败一次 | 东财事件（另一张表） | 是否迁移 |
| 个股研究、策略决策的 rt_k 校验 | 已完成（`2843ede`）：技术面改用本地收盘日线 | — | — |
| 自选股历史补齐、指数行情主源 | 已完成（`a953352`、`ce63d66`） | 本地收盘日线；东财→腾讯 | — |

以上部分处理完之后，再删除 `tushare_providers.py` 等五个模块，以及
`provider_rate_limits` 和 super_get 执行器。

## 怎样保证

删除完成后，`git grep -i tushare -- quant-service/app` 只剩历史迁移和数据血缘里的
提供方名称。
