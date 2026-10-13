# 事实登记

线上系统和数据的事实，工作依赖它们。`measured` 是探针运行的日期（上海时间），`how` 是存好的脚本和输出，或读者能重跑的命令。
超过 README 里的新鲜期限（14 天）就先重测再用。owner 的查询一律只读。

| id | fact | measured | how | affects |
|---|---|---|---|---|
| F001 | owner 库全市场日线 `quant.canonical_bars_daily`：2023-08-15 至 2026-10-12，共 768 个交易日（2023 年 93、2024 年 242、2025 年 243、2026 年 190），每天约 5,300–5,410 只 | 2026-10-13 | docs/work/probes/2026-10-13-owner-history.py → .out.txt（SSH 进主服务容器只读查询） | B001, W006 |
| F002 | 日线行里有复权因子的比例：2023 年 48.5%，2024 年 99.9%，2025 年 99.9%，2026 年 99.6% | 2026-10-13 | 同 F001 | B002, W006 |
| F003 | 日线行里存了涨停价的比例：2023 年 91.4%，2024–2025 年 99.9%，2026 年 98.5%；涨跌停价表 `quant.daily_trade_limits` 只有 2026-09-01 至 2026-10-13 的 25 天 | 2026-10-13 | 同 F001 | B003, B009 |
| F004 | 涨停榜（每天 5 张）：历史 `longhuvip_history` / `limit_performance_history` 2023-09-01 至 2026-10-09 共 748 天，实时 `longhuvip` / `limit_performance_live` 2026-10-12 起；2023-09-01 至 2026-10-12 开市 749 天（探针打印的 750 含 10 月 13 日当天），一天不缺。风险分读数 `limit_up_risk_daily` 2023-09-11 至 2026-10-12 共 743 天 | 2026-10-13 | 同 F001 | W006 |
| F005 | 历史竞价榜 `morning_bidding_history`：2023-09-01 至 2026-09-18 共 739 天，只含竞价金额靠前的约 200 只。逐只竞价快照、竞价曲线、委托明细、处置表：2026-10-13 起才有（当天 62 个快照时点、78 条曲线、78 行明细） | 2026-10-13 | 同 F001 | B007, W006 |
| F006 | 原始观测表 `quant.raw_market_observations` 约 1,588 万行、39 GB；库里没有多年的个股分钟线表 | 2026-10-13 | 同 F001 | B004 |
| F007 | 授权接口的历史分钟价（`longhu_history`、`StockL2History`、`GetStockTrend`）按股票和日期返回 241 个分钟点（价格、均价），抽查 2022-09-05 至 2026-09-18 的 6 个股票日全部返回；指数和 ETF 也能取（`SH000001`、510300、512100、159915、563300），每点另有成交量 | 2026-10-13 | docs/work/probes/2026-10-13-minute-depth.py → .out.txt；docs/work/probes/2026-10-13-index-minute-probe.py → .out.txt（经 owner 读路径 127.0.0.1:15682） | B004, B008, W007 |
| F008 | 同一接口的逐分钟大单净额（`GetStockDaDanTrend`）回溯到 2025-01-02 | 2026-10-12 | 本机 `state/research/divergence_study_20261012/intraday/fetch_series.py` 取数时得到；没有单独的探针输出，用之前重测 | B005 |
| F009 | 两台主机实际运行的版本：owner `owner-code-20261013-03`（源 29f3ffdf），edge 中继 `hotfix-20261012T162327Z-857e48320e2b-18399`，edge 控制台 `console-20261013T031958Z-ac15bcdebbc8`（源 ac15bcde）；状态脚本 ALL CHECKS PASSED | 2026-10-13 | docs/work/probes/2026-10-13-release-status.out.txt（owner 与中继）；控制台版本号来自 11:20 发布脚本的输出，可用 `scripts/release status` 重测 | W001, W003, W004 |
| F010 | 分钟雷达序列（`/api/research/market/radar`）有数据的交易日：2026-10-09 只有午后，2026-10-12 和 2026-10-13 全天，2026-10-08 很少，2026-09-30 没有 | 2026-10-13 | `curl 'http://127.0.0.1:18300/api/research/market/radar?trade_date=<日期>'` 看返回大小和点数；当时的输出在 docs/work/probes/2026-10-13-vshape-today.out.txt | W007 |
