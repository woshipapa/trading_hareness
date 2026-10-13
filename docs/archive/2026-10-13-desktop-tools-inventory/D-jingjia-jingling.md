# 竞价精灵 v1.2.2：功能与数据来源清单

来源：安装包 `竞价精灵_1.2.2_(1).exe`（SHA-256 `387669ab48ccbe205deb605b48071564fd0370203e2d1a678c9ab4569e346fc5`，2026-10-13 静态读取，没有运行，没有向任何服务器发请求）。
提取物在本机 `state/research/jingjia_jingling_1.2.2/`：`frontend/` 是从 `app.exe` 里取出的 105 个前端文件，`app-exe-strings.txt` 是 `app.exe` 的字符串表。

## 0. 读到的层次（先看这段）

- 这是 Tauri 应用：原生层是 Rust 编译的 `app.exe`，界面是内嵌的 Vue 前端（打包压缩过，变量名被缩短，没有注释）。
- **读得到**：页面清单、原生层提供给前端的命令名、公开数据源的域名和路径、软件自己服务器的接口路径。
- **读不到**：选股和打分规则。量化选股、阴阳谱、竞价榜单、涨停表现这些页面只展示软件服务器（`api2.jingjia188.top`）返回的结果，规则不在安装包里。
- **没读**：原生层的实现（各命令怎样请求和清洗数据），需要反汇编器，本机没装。登录、激活和请求签名没有碰。
- 因此本清单是“这类工具用哪些数据、做哪些看板”的线索，不是可以照抄的规则；每个接口的参数和返回字段都要自己实测后才算数。

## 1. 页面（25 个路由）

| 路由 | 页面 | 前端里看到的数据来源 |
|---|---|---|
| `/workbench` | 工作台（可拖放卡片：量化精选、概念题材、竞价、题材报告等，分竞价和盘中两个阶段） | 软件服务器 `/api/quant/gnkxxg`、`/api/stockinfo`、`/api/theme/*`、`/api/theme-dynamics/list`、`/api/ai-report/detail`、`/api/ticai-eastmoney-list`；同花顺 `dq.10jqka.com.cn` |
| `/auction` | 竞价（竞价榜单、竞价爆发板块、新题材/延续题材、一字板方向、赛马概念、历史模式） | 软件服务器 `/api/stockinfo`、`/api/stockinfo-history?date=` |
| `/market-live` | 盘中 | 原生命令（行情类） |
| `/yesterday-limit-up` | 昨日涨停 | 软件服务器 `/api/bidding-data`、`/api/zhangting-performance`；开盘啦 `apphis.longhuvip.com` |
| `/limit-up-perf` | 涨停表现 | 选股宝 `flash-api.xuangubao.com.cn/api/pool/detail` |
| `/limit-up` | 涨停 | 原生命令 `get_limit_up_pool` 等 |
| `/anomaly-center` | 异动中心 | 东方财富 `push2.eastmoney.com/api/qt/ulist.np/get`、`datacenter.eastmoney.com/securities/api/data/v1/get`、`mobappconfig.securities.eastmoney.com/emcfg/stock_monitor.json`、公告 `data.eastmoney.com/notices/detail/`；开盘啦 `apphwshhq.longhuvip.com` |
| `/concept-theme` | 概念题材 | 软件服务器 `/api/ticai-eastmoney-list`、`/api/ticai-eastmoney-snapshots?date=`、`/api/ticai-eastmoney-theme-flow(s)`；东方财富 `push2delay.eastmoney.com/api/qt/stock/trends2/get` |
| `/theme-library` | 题材库 | 软件服务器 `/api/theme/first-level`、`/api/theme/detail?code=`、`/api/theme/quotes?codes=`、`/api/theme/feedback` |
| `/mainline-cycle` | 主线周期 | 选股宝 `flash-api.xuangubao.com.cn/api/surge_stock/plates`、`/api/surge_stock/stocks` |
| `/hotspot` | 热点解读 | 同上（选股宝异动板块和异动股票） |
| `/sector-rotation` | 板块轮动 | 未在前端看到直接地址（走原生命令） |
| `/hot-list` | 热榜 | 同花顺 `eq.10jqka.com.cn/open/api/hot_list/v1/hot_stock/a/hour/data.txt`；东方财富人气榜 `emappdata.eastmoney.com/stockrank/getAllCurrentList` |
| `/new-high` | 新高 | 原生命令 `get_new_high_data` |
| `/yyp` | 阴阳谱 | 软件服务器 `/api/yyp-daily`、`/api/yyp-daily-all`、`/api/yyp-windindicator`；原生命令 `get_yyp_tuoying` |
| `/quant` | 量化选股（量化精选、问财、优选三类） | 软件服务器 `/api/quant/gnkxxg`、`/api/stockinfo` |
| `/compare` | 多股对比 | 原生命令 `get_batch_kline`、`get_market_minutes` |
| `/watchlist` | 自选股 | 本地存储 |
| `/training` | 交易训练 | 本地存储（`tt_pool_v1`、`tt_records_v1`、`tt_setup_v1`） |
| `/ipo` | 新股申购 | 软件服务器 `/api/ipo/list` |
| `/tools-buchang` | 补仓计算器 | 纯前端计算 |
| `/settings`、`/login`、`/activate`、`/download` | 设置、登录、激活、下载 | 未读 |

## 2. 原生层提供给前端的命令

- 行情：`get_market_quotes`、`get_market_minutes`、`get_market_kline`、`get_batch_kline`、`get_market_overview`、`get_market_distribution`、`get_market_trend`
- 涨停：`get_limit_up_pool`、`get_limit_stats`、`get_limit_heights`、`get_burst_history`、`get_stock_pool`、`get_stock_reason`
- 竞价：`get_bid_history`、`get_auction_blocks`
- 题材和新高：`get_theme_graph`、`get_theme_stocks`、`get_new_high_data`、`get_yyp_tuoying`
- 通达信联动：`detect_local_software`、`get_tdx_directory`、`get_tdx_sectors`、`import_to_tdx_sector`、`send_stock_code`

## 3. 公开数据源（`app.exe` 字符串表和前端里出现的地址）

| 提供方 | 地址 | 从名字看是什么（未实测） |
|---|---|---|
| 同花顺 | `eq.10jqka.com.cn/call_auction_v2/variation/v1/get_block_list/1?date=` | 竞价异动板块列表，可按日期取 |
| 同花顺 | `data.10jqka.com.cn/dataapi/limit_up` | 涨停数据 |
| 同花顺 | `eq.10jqka.com.cn/open/api/quote_review/rise_fall/v1/chart?date=` | 涨跌分布，可按日期取 |
| 同花顺 | `dq.10jqka.com.cn/fuyao/market_analysis_api/chart/v1/get_chart_data?chart_key=turnover_minute` | 全市场成交额分钟曲线 |
| 同花顺 | `dq.10jqka.com.cn/fuyao/pc_reviewpoints/reviewpoints/v1/publicopinionmonitoring` | 舆情监控 |
| 同花顺 | `basic.10jqka.com.cn/fuyao/f10_stock_index/concept/v1/stock_concept_list?market_id=` | 个股所属概念 |
| 同花顺 | `gpc.10jqka.com.cn/dptrend/window/textinfo/capitalflows` | 大盘资金流文字 |
| 同花顺 | `eq.10jqka.com.cn/open/api/hot_list/v1/hot_stock/a/hour/data.txt` | 热股榜（小时） |
| 开盘啦 | `apphq`、`apphis`、`apphwhq`、`apphwshhq`、`apparticle` 的 `.longhuvip.com/w1/api/index.php`，以及 `.kaipanhong.com` 的备用域名 | 实时、历史、文章接口；字符串表里出现的方法名有 `HisDaBanList`、`ZhiShuRanking`、`GetIndexStock`、`GetZsTrend` |
| 选股宝 | `flash-api.xuangubao.com.cn/api/pool/detail`（含 `pool_name=yesterday_limit_up&date=`） | 涨停池和昨日涨停池，可按日期取 |
| 选股宝 | `flash-api.xuangubao.com.cn/api/surge_stock/plates`、`/api/surge_stock/stocks` | 异动板块和异动股票 |
| 选股宝 | `flash-api.xuangubao.com.cn/api/market_indicator/line?fields=yesterday_limit_up_avg_pcp`、`/api/market_indicator/pcp_distribution` | 昨日涨停今日平均涨幅曲线、涨跌幅分布 |
| 东方财富 | `push2.eastmoney.com/api/qt/stock/get`、`/api/qt/ulist.np/get`、`/api/qt/stock/trends2/get`；`push2his.eastmoney.com/api/qt/stock/kline/get`、`/api/qt/stock/trends2/get`；`push2delay.eastmoney.com` | 报价、批量报价、分时、K 线 |
| 东方财富 | `emcfgdata.eastmoney.com/api/themeInvest/getThemeGraph`、`/getStockList` | 题材图谱和题材成分股 |
| 东方财富 | `emappdata.eastmoney.com/stockrank/getAllCurrentList` | 人气榜 |
| 东方财富 | `datacenter.eastmoney.com/securities/api/data/v1/get`、`mobappconfig.securities.eastmoney.com/emcfg/stock_monitor.json` | 数据中心通用查询、盘口异动类型配置 |
| 腾讯 | `web.ifzq.gtimg.cn/appstock/app/minute/query`、`/appstock/app/day/query`、`proxy.finance.qq.com/cgi/cgi-bin/stockinfoquery/kline/app/get`、`qt.gtimg.cn/q` | 分时、日 K、K 线、实时报价 |
| 新浪 | `quotes.sina.cn/cn/api/jsonp_v2.php/.../CN_MarketDataService.getKLineData?symbol=` | K 线 |

## 4. 没读出来的

- 每个接口的请求参数、返回字段、限流和可回溯的历史深度：都要实测。
- 软件服务器各接口的口径（量化精选的规则、阴阳谱的算法、竞价榜单的筛选）：不在安装包里。
- “阴阳谱”“赛马概念”“黄底优先（重合策略个股置顶）”这些名词的定义：前端只有标签文字。
