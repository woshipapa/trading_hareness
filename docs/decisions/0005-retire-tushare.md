# 0005 停用 Tushare，`datasources/` 是唯一的数据源层

- 状态：生效（所有能力已替代；无调用方的代码删除进行中）
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

## 替代完成（2026-10-09）

运营者的决定是“替代，不下线”，数据源按“开盘啦（Longhu）→ Fuyao → 东财 → 同花顺 → 新浪 → 腾讯”
的顺序优先。每一项都换了来源，没有一项被下线：

| 功能 | 现在的来源 | 提交 | 口径变化与注意 |
| --- | --- | --- | --- |
| 当日涨跌停价（`daily_trade_limits`） | 腾讯行情里交易所公布的涨跌停价（字段 47/48），按会话全市场写入 | `98165f6` | 北交所向内取整、沪深四舍五入，与交易所一致；覆盖不足 95% 或行情日期不对时整批拒收 |
| 停牌 | 东财数据中心停牌表（`RPT_CUSTOM_SUSPEND_DATA_INTERFACE`） | `8c8ed20` | 09:30 前已复牌的不计；B 股不计 |
| ST 证据 | 收盘名称（`record_st_evidence`） | `8c8ed20` | 旧的 `st_evidence` 源标为 `retired_source` |
| 财报披露日历、业绩预告、业绩快报 | 东财数据中心报表 | `9030658` | 预告单位万元、快报单位元，写入时标明 |
| 全 A 股票池 | Fuyao `ticker_list` | `987c3ac` | 收盘日线不再经过 Tushare 链路 |
| 个股资金流（日） | 开盘啦收盘全市场主力净额（`longhuvip_main_net`） | `f29a283`、`43e673f`、`24b006b` | 口径不同（按单笔大小分类）；特征版本为 `multi-source-feature-v5` |
| 实时 MACD/KDJ 的种子 | 本地前复权日线（≥120 个交易日） | `774f31b`、`39623a0` | `774f31b` 的提交说明曾称“三周读数错误”，实际该模块当时没有调用方，已在 `39623a0` 中更正 |
| 龙虎榜 | Fuyao `a_share_dragon_tiger_list`（`lhb_ths`） | `757e9e0`、`fb8b5e3` | 每股一行，无席位明细和上榜原因，相关字段报告为不可得而非空 |
| 涨停池（形态挖掘） | Fuyao 收盘涨停池与连板梯队 | `818fbeb` | — |
| 同花顺板块目录与成分 | Fuyao `ths_index_list` / `ths_index_constituents`（`fuyao_ths_*`），收盘后自动刷新、只写变化 | `947fc29` | 代码与旧 THS 相同（`NNNNNN.TI`） |
| 行业资金流 | 开盘啦收盘行业报告（`longhu_ths_industry`） | `c4cf5ff` | 单位为元（旧 THS 为亿元） |
| 概念资金流 | 同花顺公开资金流页（键沿用 `eastmoney_concept`） | `c4cf5ff`、`b38aaaf` | **上游是同花顺 data.10jqka.com.cn，不是东财**（akshare 1.18.96 核实）；板块键是名称，按名称对到 Fuyao 成分（`ths_concept_name_bridge`） |
| 概念涨停强度、涨停候选 | Fuyao 收盘涨停池 × 时点成分（`fuyao_ths_concept_limit_strength`） | `c4cf5ff` | 自算，剔除融资融券等资格类分组 |
| 冻结 THS 数据的读取方（涨停联动、板块报告、资金流读取、分析师评估、行业归属、默认路由） | 上面的新口径；旧行只用于历史 | `f0558b2`、`edf19f2` | 名称对不上的板块报告为未匹配，不静默丢弃 |
| 涨停逐股明细（新增，原无 Tushare 对应） | 开盘啦涨停复盘为主，选股宝对照 | `e0da686`、`55c2aca` | 首封时间 8/10 精确一致，连板数 10/10 一致 |

同时发现并修正的两个单位问题：开盘啦行业条目以元存放、快照却声明亿元（`b38aaaf`）；
盘后板块上下文把亿元和元混在一起排百分位（`edf19f2`）。

剩下的只有已无调用方的 Tushare 代码本身，删除见下一节。

## 怎样保证

删除完成后，`git grep -i tushare -- quant-service/app` 只剩历史迁移和数据血缘里的
提供方名称。
