"""The data-source catalog: every upstream, every capability, every binding.

This is the single place that answers "who can give me X, how good is it,
and where does it land".  Strategies and collectors resolve *capabilities*
through it; vendor names stay here.

Status facts are dated: ``live_verified`` means health rows or stored
evidence were observed on the owner runtime (2026-09-18 audit), ``declared``
means probed from the owner peer but not yet session-proven.  Update a
binding's status here -- and nowhere else -- when that changes.
"""

from __future__ import annotations

from typing import Any, Final, Iterable

from .contracts import (
    BINDING_STATES, CATEGORIES, DECLARED, DORMANT, GRAINS, LICENSES, LIVE_VERIFIED, RESOLVABLE_STATES,
    RETIRED, SCOPES, UNSUPPORTED, Binding, Capability, DataSource, SourceLabel, Taxonomy,
)


CATALOG_VERSION: Final = "datasource-catalog-v2"

_TUSHARE_RISK = "共享限频池；全市场截面需分页；超级 GET 网关偶发返回错数据集并按参数缓存"
_EASTMONEY_RISK = "按出口 IP 限流/反爬；push2 clist 全市场在 owner 出口被断连（2026-09-18）"

SOURCES: Final[dict[str, DataSource]] = {source.key: source for source in (
    # -- licensed -------------------------------------------------------------
    DataSource("longhuvip", "开盘啦授权行情（经 owner 网关）", "owner /licensed/longhu/*", "licensed", "gateway", "paid",
               "app/longhu_vendor_source.py", ("QUANT_SHARED_READ_API_BASE_URL", "QUANT_SHARED_READ_API_KEY"),
               "上游凭据只在 owner；逻辑篮子≤300只；GetStockPanKou 按票扇出"),
    DataSource("longhuvip_composite", "开盘啦行业截面 + 日K 收盘合成", "owner /licensed/longhu/*", "licensed", "gateway", "paid",
               "app/longhu_shared_full_market.py", ("QUANT_SHARED_READ_API_BASE_URL", "QUANT_SHARED_READ_API_KEY"),
               "收盘后约 16:00 才完整"),
    DataSource("longhuvip_index", "开盘啦指数日K", "owner /licensed/longhu/*", "licensed", "gateway", "paid",
               "app/longhu_market_service.py", ("QUANT_SHARED_READ_API_BASE_URL", "QUANT_SHARED_READ_API_KEY")),
    DataSource("tushare_primary", "Tushare 兼容主源", "tushare-compatible REST", "licensed", "http_json", "paid",
               "app/tushare_providers.py", ("TUSHARE_PRIMARY_TOKEN", "TUSHARE_PRIMARY_API_URL"), _TUSHARE_RISK),
    DataSource("tushare_super_get", "Tushare 超级 GET 网关", "ProMax GET gateway", "licensed", "http_json", "paid",
               "app/tushare_providers.py", ("TUSHARE_SUPER_GET_API_KEY", "TUSHARE_SUPER_GET_API_URL"), _TUSHARE_RISK),
    DataSource("tushare_super_sdk", "Tushare 超级 SDK 代理源", "tushare SDK proxy", "licensed", "http_json", "paid",
               "app/tushare_providers.py", ("TUSHARE_SUPER_PROXY_URL",), _TUSHARE_RISK + "；代理 407 时整源不可用"),
    DataSource("tushare_backup", "Tushare REST 备用源", "tushare REST", "licensed", "http_json", "paid",
               "app/tushare_providers.py", ("TUSHARE_BACKUP_API_KEY", "TUSHARE_BACKUP_API_URL"), "6 次/分钟；整包截面 413"),
    # -- official free API ----------------------------------------------------
    DataSource("fuyao_ths", "同花顺官方数据服务（hithink / fuyao）", "fuyao.aicubes.cn", "official_free_api", "x_api_key", "free_for_now",
               "app/fuyao_provider.py", ("HITHINK_FINANCE_API_KEY", "FUYAO_API_KEY", "FUYAO_TOKEN"),
               "官方未发计费条款，按运行动态限流；无分钟K/tick/新闻公告原文；thscodes≤100 且一个坏代码整批失败；池默认分页 50"),
    # -- public web -----------------------------------------------------------
    DataSource("eastmoney_free", "东方财富公开行情（板块资金流/观察池资金流）", "push2.eastmoney.com", "public_web", "http_json", "free",
               "app/free_market_providers.py", risks=_EASTMONEY_RISK),
    DataSource("eastmoney_ztb", "东方财富涨停板专题与盘口异动", "push2ex.eastmoney.com", "public_web", "http_json", "free",
               "app/datasources/sources/eastmoney_ztb.py", risks=_EASTMONEY_RISK + "；池子只保留近期，须每日归档"),
    DataSource("eastmoney_hot_rank", "东方财富股吧人气榜", "emappdata.eastmoney.com", "public_web", "http_json", "free",
               "app/datasources/sources/eastmoney_hot_rank.py", risks=_EASTMONEY_RISK),
    DataSource("eastmoney_datacenter", "东方财富数据中心", "datacenter-web.eastmoney.com", "public_web", "http_json", "free",
               "app/datasources/sources/eastmoney_datacenter.py", risks=_EASTMONEY_RISK + "；两融明细单页较慢"),
    DataSource("eastmoney_flash", "东方财富 7x24 快讯", "np-weblist.eastmoney.com", "public_web", "http_json", "free",
               "app/datasources/sources/news_flash.py"),
    DataSource("tencent_free", "腾讯财经（qt/分钟/分笔）", "qt.gtimg.cn / stock.gtimg.cn", "public_web", "http_text", "free",
               "app/free_market_providers.py", risks="日K 为前复权，只作研究参考"),
    DataSource("sina_free", "新浪财经公开报价", "hq.sinajs.cn", "public_web", "http_text", "free",
               "app/free_market_providers.py", risks="无决策时间戳契约，仅兜底"),
    DataSource("cninfo_free", "巨潮资讯公开公告", "www.cninfo.com.cn", "public_web", "http_json", "free",
               "app/free_market_providers.py"),
    DataSource("cninfo_irm", "巨潮互动易", "irm.cninfo.com.cn", "public_web", "http_json", "free",
               "app/datasources/sources/investor_qa.py"),
    DataSource("sse_einteract", "上证e互动", "sns.sseinfo.com", "public_web", "http_html", "free",
               "app/datasources/sources/investor_qa.py", risks="HTML 结构变化会使解析归零；回答时间为相对时间"),
    DataSource("cls_telegraph", "财联社电报", "www.cls.cn", "public_web", "http_json", "free",
               "app/datasources/sources/news_flash.py", risks="请求需 web 客户端校验和（非凭据）"),
    DataSource("jin10_flash", "金十快讯", "flash-api.jin10.com", "public_web", "http_json", "free",
               "app/datasources/sources/news_flash.py", risks="不含 VIP 内容"),
    DataSource("ths_flash", "同花顺快讯", "news.10jqka.com.cn", "public_web", "http_json", "free",
               "app/datasources/sources/news_flash.py"),
    DataSource("ttfund", "天天基金净值", "api.fund.eastmoney.com", "public_web", "http_json", "free",
               "app/datasources/sources/ttfund.py", risks="QDII 净值晚 1-2 天披露"),
    # -- unofficial protocol / local files -------------------------------------
    DataSource("tdx_public", "通达信公开行情主站", "tdx hq hosts :7709", "unofficial_protocol", "tcp_tdx", "free",
               "app/datasources/sources/tdx_protocol.py",
               risks="非官方社区主站；2026-09-18 起仅历史分笔与除权除息可用，实时行情与 K 线命令被拒"),
    DataSource("tdx_local", "通达信客户端盘后数据（vipdoc）", "owner workstation TDX client", "local_files", "files", "free",
               "app/datasources/sources/tdx_local_files.py", risks="依赖 owner 手工/定时盘后下载", deploy="owner_workstation_cli"),
    # -- aggregators ----------------------------------------------------------
    DataSource("akshare", "AKShare 公开聚合", "multiple (akshare)", "aggregator", "python_library", "free",
               "app/akshare_provider.py", risks="上游站点变化即失效；东财系函数在部分出口被断连"),
    DataSource("baostock", "BaoStock", "baostock.com", "aggregator", "python_library", "free", "app/baostock_daily_sync.py"),
    # -- derived --------------------------------------------------------------
    DataSource("derived_market_sentiment", "自算短线情绪指标", "local computation", "derived", "derived", "free",
               "app/datasources/derived/market_sentiment.py"),
    DataSource("derived_tick_flow", "自算分笔资金流", "local computation", "derived", "derived", "free",
               "app/datasources/derived/tick_flow.py"),
    DataSource("derived_sentiment_cycle", "日K 自算情绪周期", "local computation", "derived", "derived", "free",
               "app/sentiment_cycle_daily.py"),
    DataSource("longhu_qfq_derived", "开盘啦前复权 K 线推算累计因子", "owner close-maintenance job", "derived", "derived", "free",
               "app/longhu_shared_full_market.py", risks="owner 04:30 task；仅接受已落库并带 available_at 的累计因子，不在 peer 侧重新推算"),
)}


def _cap(key: str, label: str, grain: str, scope: str, fields: str, time_semantics: str, description: str = "") -> Capability:
    return Capability(key, key.split(".", 1)[0], label, grain, scope, tuple(fields.split()), time_semantics, description)


_OBSERVED = "effective=上游时间戳或采集时刻; available=采集时刻"
_SESSION_CLOSE = "effective=该交易日 15:00; available=采集时刻（盘后）"
_PUBLISHED = "effective=公开发布时间; available=首次采集时刻（重复采集不后移）"
_INGEST = "effective/available=供应商入库时钟 EITIME/EUTIME 与采集时刻取早者；从不用仅日期的公告日"

CAPABILITIES: Final[dict[str, Capability]] = {cap.key: cap for cap in (
    # quote
    _cap("quote.all_a_snapshot", "全 A L1 快照", "realtime", "all_a",
         "price:yuan pct_change:pct volume:shares turnover:yuan", _OBSERVED, "3 秒级 L1，无主力资金语义"),
    _cap("quote.watch_snapshot", "观察池报价（交易所时间戳）", "realtime", "watchlist",
         "price:yuan volume:shares amount:yuan volume_ratio turnover_rate:pct exchange_time", _OBSERVED),
    _cap("quote.order_book", "盘口五档/十档", "realtime", "watchlist", "bid1..10:yuan ask1..10:yuan bid_vol:lots", _OBSERVED),
    _cap("quote.fast_confirmation", "秒级二次确认报价", "realtime", "watchlist", "price:yuan", _OBSERVED),
    _cap("quote.valuation", "估值 PE/PB/PS/PCF", "daily", "all_a", "pe_ttm pe_mrq pb_mrq ps_ttm pcf_ttm", _OBSERVED),
    # bars
    _cap("bars.daily", "日K（不复权，canonical 仲裁）", "daily", "all_a",
         "open:yuan high low close pre_close volume:lots amount", "effective=交易日; available=入库时刻"),
    _cap("bars.daily_adjusted", "复权日K（研究）", "daily", "per_symbol", "open:yuan close adj_basis", "effective=交易日"),
    _cap("bars.minute", "分钟K", "intraday", "watchlist", "bar_time open:yuan close volume:shares amount:yuan",
         "effective=bar_time; available=explicit source_available_at only（本地入库时间不可替代）"),
    _cap("bars.index_daily", "指数日K", "daily", "market", "open close volume amount", "effective=交易日"),
    _cap("bars.adjustment_factor", "复权因子", "daily", "all_a", "adj_factor", "effective=交易日"),
    # ticks
    _cap("ticks.session", "分笔（带主动买卖方向）", "intraday", "per_symbol",
         "time price:yuan volume:shares side:B/S/N/A/P", "effective=成交时刻; available=采集时刻",
         "L1 分笔，不是 L2 逐笔委托；A=09:15-09:25 竞价虚拟撮合"),
    # auction
    _cap("auction.open_snapshot", "早盘集合竞价", "realtime", "all_a",
         "auction_price:yuan auction_volume auction_amount auction_unmatched auction_pct", _OBSERVED),
    _cap("auction.close_snapshot", "尾盘集合竞价终值", "daily", "all_a", "auction_price auction_volume auction_amount", _OBSERVED),
    _cap("auction.short_term_benchmark", "竞价短线基准", "daily", "market", "symbol auction_pct tags", _OBSERVED),
    _cap("auction.history_0925", "历史每日 09:25 竞价成交", "daily", "per_symbol",
         "price:yuan volume:shares amount:yuan auction_curve", _SESSION_CLOSE),
    # limits
    _cap("limits.prices", "涨跌停价", "daily", "all_a", "up_limit:yuan down_limit:yuan", "effective=交易日; available=入库时刻"),
    _cap("limits.limit_up_pool", "涨停池", "intraday", "all_a",
         "symbol limit_up_time reason board_count seal_money", _OBSERVED),
    _cap("limits.broken_pool", "炸板池", "intraday", "all_a", "symbol open_times", _OBSERVED),
    _cap("limits.limit_down_pool", "跌停池", "intraday", "all_a", "symbol seal_money", _OBSERVED),
    _cap("limits.ladder", "连板天梯", "intraday", "all_a", "symbol board_num", _OBSERVED),
    _cap("limits.seal_detail", "封板明细（首/末封时间、封板资金、炸板次数、几天几板）", "daily", "all_a",
         "first_seal_time last_seal_time seal_fund open_times limit_stat_text", _SESSION_CLOSE),
    _cap("limits.previous_limit_up", "昨日涨停今日表现", "daily", "all_a", "symbol pct_change previous_board_count", _SESSION_CLOSE),
    _cap("limits.strong_pool", "强势股池", "daily", "all_a", "symbol reason volume_ratio", _SESSION_CLOSE),
    _cap("limits.sub_new_pool", "次新股池", "daily", "all_a", "symbol opened_days ipo_date", _SESSION_CLOSE),
    _cap("limits.anomaly_tape", "盘口异动（火箭发射/大笔买卖/封板开板）", "intraday", "all_a",
         "symbol time change_type info", "effective=异动时刻; available=采集时刻"),
    _cap("limits.stock_anomaly_reason", "个股异动原因", "intraday", "all_a", "symbol tag keywords analysis",
         _OBSERVED, "AI 生成摘要，仅作解释"),
    # sector
    _cap("sector.membership", "板块/概念成分（PIT）", "reference", "board", "taxonomy_key sector_key symbol known_at",
         "known_at 之后才可用；盘中刷新只对下一场生效"),
    _cap("sector.index_quote", "板块/概念指数行情", "daily", "board", "index_code last_price pct_change volume turnover", _OBSERVED),
    _cap("sector.flow_curve", "板块资金流曲线", "intraday", "board", "sector net_inflow:yuan", _OBSERVED),
    _cap("sector.anomaly", "板块异动", "intraday", "board", "board_code pct_change main_net_inflow change_counts", _OBSERVED),
    # flow
    _cap("flow.stock_daily", "个股资金流（日）", "daily", "all_a", "main_net:yuan super_large large medium small", "effective=交易日"),
    _cap("flow.watch_intraday", "观察池盘中资金流", "intraday", "watchlist", "main_net_inflow:yuan", _OBSERVED,
         "公开资金流代理，非交易所订单流"),
    _cap("flow.tick_derived", "分笔主动买卖/大单估算", "daily", "watchlist",
         "net_active_amount:yuan large_net_active_amount by_bucket by_window", _SESSION_CLOSE, "按成交额阈值估算，非 L2"),
    # lhb
    _cap("lhb.daily", "龙虎榜", "daily", "all_a", "symbol net_buy:yuan buy sell reason", _SESSION_CLOSE),
    _cap("lhb.seat_statistics", "龙虎榜席位/游资统计", "daily", "market", "seat net_buy", _SESSION_CLOSE),
    # attention
    _cap("attention.ths_hot_rank", "同花顺热榜", "intraday", "market", "symbol rank heat rank_change", _OBSERVED),
    _cap("attention.ths_skyrocket", "同花顺飙升榜", "intraday", "market", "symbol rank heat", _OBSERVED),
    _cap("attention.ths_hot_history", "同花顺热榜历史（逐日归档）", "daily", "market", "symbol rank trade_date", _SESSION_CLOSE),
    _cap("attention.em_popularity", "东财人气榜", "intraday", "market", "symbol rank rank_change", _OBSERVED),
    _cap("attention.em_surge", "东财飙升榜", "intraday", "market", "symbol rank rank_change surge_rank", _OBSERVED),
    _cap("attention.em_rank_history", "东财个股人气排名历史（约一年）", "daily", "per_symbol", "symbol trade_date rank",
         "effective=该日收盘; available=回填时刻"),
    # news / events
    _cap("news.flash", "7x24 快讯（带重要标记）", "event", "market", "flash_id published_at title importance symbols", _PUBLISHED),
    _cap("news.announcements", "公告列表", "event", "per_symbol", "title url announcement_type", _PUBLISHED),
    _cap("events.investor_qa", "互动易/e互动问答", "event", "all_a", "symbol question answer answered_at", _PUBLISHED),
    _cap("events.restricted_release", "限售解禁（历史+未来）", "event", "all_a", "symbol free_date shares type", _INGEST),
    _cap("events.holder_trade", "股东增减持", "event", "all_a", "symbol holder direction change_num", _INGEST),
    _cap("events.holder_count", "股东户数", "event", "all_a", "symbol end_date holder_num change_ratio", _INGEST),
    _cap("events.block_trade", "大宗交易", "event", "all_a", "symbol deal_price premium_ratio buyer seller", _INGEST),
    _cap("events.earnings_forecast", "业绩预告", "event", "all_a", "symbol report_date predict_type amount_range", _INGEST),
    _cap("events.earnings_express", "业绩快报", "event", "all_a", "symbol report_date eps revenue net_profit", _INGEST),
    _cap("events.repurchase", "股份回购", "event", "all_a", "symbol progress amount_range price_cap", _INGEST),
    _cap("events.ipo_calendar", "新股申购日历", "event", "market", "symbol apply_date issue_price", _INGEST),
    _cap("events.disclosure_schedule", "定期报告预约披露", "event", "all_a", "symbol end_date pre_date actual_date", "effective=公告; available=入库"),
    # fundamentals / reference / fund
    _cap("fundamentals.daily_basic", "每日指标（换手/量比/市值/股本）", "daily", "all_a",
         "turnover_rate volume_ratio total_mv float_mv float_share", "effective=交易日"),
    _cap("fundamentals.financial_statements", "三大报表与财务指标", "periodic", "per_symbol", "report_period statement_items", "按公告日可得"),
    _cap("fundamentals.capital_changes", "除权除息与股本变迁", "event", "per_symbol",
         "date category cash_dividend float_shares_after_10k total_shares_after_10k", "effective=变动日; available=采集时刻"),
    _cap("fundamentals.margin", "融资融券", "daily", "all_a", "rzye rzmre rqye net_buy", "effective=T 日; available=T+1 采集"),
    _cap("reference.instruments", "证券基础信息", "reference", "all_a", "symbol name list_date is_st", "effective=入库"),
    _cap("reference.trade_calendar", "交易日历", "reference", "market", "exchange calendar_date is_open", "effective=入库"),
    _cap("fund.nav", "基金单位/累计净值", "daily", "fund", "fund_code nav_date unit_nav accumulated_nav daily_growth_pct",
         "effective=净值日; available=采集时刻"),
    # derived
    _cap("derived.market_sentiment", "自算短线情绪（涨跌停/封板率/分层晋级率/昨涨停溢价/涨跌分布/量能/板块强度）",
         "intraday", "market", "limit_up_count seal_rate promotion prior_limit_up_today distribution turnover concept_strength",
         _OBSERVED, "公开口径，不等于任何供应商私有评分"),
    _cap("derived.sentiment_cycle", "日K 自算情绪周期", "daily", "market", "broken_rate max_board_height promotion_rate stage",
         "effective=交易日收盘; available=计算时刻"),
)}


def _bind(source: str, capability: str, priority: int, status: str, store: str | None = None, adapter: str | None = None,
          history: str = "", limits: str = "", notes: str = "", decision_eligible: bool = False) -> Binding:
    return Binding(source, capability, priority, status, store, adapter, history, limits, notes, decision_eligible)


_EVT = "market_events:event_type="
_RAW = "raw_market_observations:capability="

BINDINGS: Final[tuple[Binding, ...]] = (
    # quote.all_a_snapshot
    _bind("fuyao_ths", "quote.all_a_snapshot", 12, LIVE_VERIFIED, _RAW + "a_share_prices_snapshot",
          "app/fuyao_provider.py:all_a_snapshot_rows", "实时", "2 页 × 5000",
          notes="owner 规则：Longhu 行业截面与观察池报价都不冒充全 A 快照"),
    _bind("eastmoney_free", "quote.all_a_snapshot", 45, UNSUPPORTED, notes="push2 clist 在 owner 出口被断连（2026-09-18）"),
    # quote.watch_snapshot / order book / fast confirmation
    _bind("longhuvip", "quote.watch_snapshot", 10, LIVE_VERIFIED, "intraday_quote_observations",
          "app/longhu_vendor_source.py", limits="≤300 只/逻辑请求", decision_eligible=True),
    _bind("tencent_free", "quote.watch_snapshot", 50, LIVE_VERIFIED, "intraday_quote_observations",
          "app/free_market_providers.py", decision_eligible=True),
    _bind("sina_free", "quote.watch_snapshot", 55, LIVE_VERIFIED, "intraday_quote_observations",
          "app/free_market_providers.py", notes="无时间戳契约，仅兜底"),
    # Both providers persist depth observations in the shared quote table.  The
    # source discriminator is part of the storage contract; there is no
    # separate intraday_order_book_observations relation.
    _bind("longhuvip", "quote.order_book", 10, LIVE_VERIFIED,
          "intraday_quote_observations:source_name=longhu_order_book", "app/longhu_vendor_source.py",
          notes="十档；source_name=longhu_order_book"),
    _bind("tencent_free", "quote.order_book", 50, LIVE_VERIFIED,
          "intraday_quote_observations:source_name=tencent_order_book",
          "app/intraday_order_book_service.py", notes="五档；source_name=tencent_order_book"),
    _bind("tushare_super_get", "quote.fast_confirmation", 15, LIVE_VERIFIED, "intraday_fast_quotes",
          "app/intraday_fast_quote_service.py", notes="rt_k"),
    _bind("fuyao_ths", "quote.valuation", 12, DECLARED, _RAW + "a_share_valuations_snapshot",
          "app/datasources/collectors/post_close.py:job_fuyao_valuation_index", "盘后逐日", "thscodes≤100"),
    _bind("tushare_primary", "quote.valuation", 20, LIVE_VERIFIED, "daily_fundamentals", "app/tushare_providers.py", notes="daily_basic"),
    # bars
    _bind("tushare_super_get", "bars.daily", 15, LIVE_VERIFIED, "canonical_bars_daily", "app/tushare_providers.py", "多年"),
    _bind("tushare_primary", "bars.daily", 10, LIVE_VERIFIED, "canonical_bars_daily", "app/tushare_providers.py", "多年",
          notes="2026-09-18 ConnectError 熔断"),
    _bind("tushare_backup", "bars.daily", 40, LIVE_VERIFIED, "canonical_bars_daily", "app/tushare_providers.py", limits="6 次/分钟"),
    _bind("longhuvip_composite", "bars.daily", 25, LIVE_VERIFIED, "canonical_bars_daily", "app/longhu_shared_full_market.py",
          notes="收盘后授权截面"),
    _bind("baostock", "bars.daily", 30, DORMANT, "canonical_bars_daily", "app/baostock_daily_sync.py", "多年"),
    _bind("eastmoney_free", "bars.daily", 45, DORMANT, "canonical_bars_daily", "app/free_market_providers.py"),
    _bind("akshare", "bars.daily", 60, DORMANT, "raw_market_observations", "app/akshare_provider.py"),
    _bind("tdx_local", "bars.daily", 35, DECLARED, "offline CSV (research)", "app/datasources/sources/tdx_local_files.py:parse_day_bytes",
          "客户端下载的全部历史", notes="owner 工作站 CLI，不直接写 canonical"),
    _bind("fuyao_ths", "bars.daily", 20, DECLARED, None, "app/fuyao_bulk_dump_capture.py", "10 年日K + 复权因子全量导出"),
    _bind("tencent_free", "bars.daily_adjusted", 50, LIVE_VERIFIED, "research_adjusted_bars_daily", "app/free_market_providers.py",
          notes="前复权，研究参考"),
    _bind("longhuvip", "bars.minute", 10, LIVE_VERIFIED, "intraday_minute_sessions", "app/intraday_minute_capture_actions.py",
          history="30/60 分钟周期K：GetKLineDay_W14 Type=30/60（longhu_vendor_source.stock_period_bars），"
                  "单页 ≤300 根；120 根≈30 分钟 15 个交易日 / 60 分钟 30 个交易日，含盘中形成中的K",
          notes="日期可验证才用"),
    _bind("tencent_free", "bars.minute", 50, LIVE_VERIFIED, "intraday_minute_sessions", "app/intraday_minute_provider_service.py"),
    _bind("tushare_super_get", "bars.minute", 15, LIVE_VERIFIED, "tushare_raw_records", "app/tushare_providers.py", notes="rt_min"),
    _bind("tdx_local", "bars.minute", 35, DECLARED, "market_bars_minute (offline import)",
          "app/datasources/sources/tdx_local_files.py:parse_minute_bytes", "客户端保留的全部分钟线"),
    _bind("longhuvip_index", "bars.index_daily", 45, DORMANT, "canonical_bars_daily", "app/longhu_market_service.py"),
    _bind("fuyao_ths", "bars.index_daily", 20, DECLARED, None, "app/fuyao_catalog.py:ths_index_prices_historical"),
    _bind("tushare_primary", "bars.adjustment_factor", 10, LIVE_VERIFIED, "daily_adjustment_factors:provider=tushare_primary",
          "app/tushare_providers.py"),
    _bind("tushare_super_sdk", "bars.adjustment_factor", 12, DORMANT, "daily_adjustment_factors:provider=tushare_super_sdk",
          "app/tushare_providers.py", notes="代理 407 时不可用"),
    _bind("tushare_super_get", "bars.adjustment_factor", 15, LIVE_VERIFIED, "daily_adjustment_factors:provider=tushare_super_get",
          "app/tushare_providers.py"),
    _bind("longhu_qfq_derived", "bars.adjustment_factor", 8, LIVE_VERIFIED,
          "daily_adjustment_factors:provider=longhu_qfq_derived", "app/longhu_shared_full_market.py:owner_factor_task",
          notes="owner 由 Longhu 前复权 K 线推算；provider 不是原始 Longhu 行情标签，peer 不得直接调用"),
    _bind("longhuvip_composite", "bars.adjustment_factor", 90, DORMANT, "daily_adjustment_factors:provider=longhuvip_composite",
          "app/longhu_shared_full_market.py", notes="不再写入累计 adj_factor；同日恒等值不能进入研究价格"),
    _bind("fuyao_ths", "bars.adjustment_factor", 20, DECLARED, None, "app/fuyao_catalog.py:a_share_adjustment_factors"),
    # ticks
    _bind("tdx_public", "ticks.session", 20, DECLARED, _RAW + "tick_flow_daily (summaries)",
          "app/datasources/sources/ticks.py:fetch_tdx_ticks", "近期任意交易日（含当日收盘后）", "2000 笔/请求",
          "与 pytdx 逐笔一致；方向经腾讯逐分钟对账 100% 一致"),
    _bind("tencent_free", "ticks.session", 30, DECLARED, _RAW + "tick_flow_daily (summaries)",
          "app/datasources/sources/ticks.py:fetch_tencent_ticks", "仅当日", notes="秒级时间戳"),
    # auction
    _bind("fuyao_ths", "auction.open_snapshot", 12, DECLARED, _EVT + "auction_*", "app/fuyao_catalog.py:a_share_auction_snapshot",
          limits="thscodes≤100"),
    _bind("longhuvip", "auction.open_snapshot", 10, LIVE_VERIFIED, _EVT + "auction_*", "app/longhu_auction_capture.py"),
    _bind("fuyao_ths", "auction.close_snapshot", 12, LIVE_VERIFIED, _EVT + "auction_final", "app/market_event_capture.py",
          limits="thscodes≤100（此前按 500 分批，只有尾批成功，已修）"),
    _bind("fuyao_ths", "auction.short_term_benchmark", 12, DECLARED, _EVT + "auction_short_term_benchmark",
          "app/market_event_capture.py"),
    _bind("tdx_public", "auction.history_0925", 20, DECLARED, _RAW + "tick_flow_daily",
          "app/datasources/bindings.py:opening_auction", "近期任意交易日", notes="含 09:15-09:25 竞价虚拟撮合曲线"),
    # limits
    _bind("tushare_super_get", "limits.prices", 15, LIVE_VERIFIED, "daily_trade_limits", "app/xiaojie_reference_repository.py",
          limits="stk_limit 全市场必须分页"),
    _bind("longhuvip_composite", "limits.prices", 25, LIVE_VERIFIED, "daily_trade_limits", "app/longhu_shared_full_market.py",
          notes="16:00 左右才落库，盘中不可依赖"),
    _bind("fuyao_ths", "limits.limit_up_pool", 12, LIVE_VERIFIED, _EVT + "limit_up_pool", "app/market_event_capture.py",
          "近期", "默认分页 50（此前只存了第一页，已修为翻页）"),
    _bind("eastmoney_ztb", "limits.limit_up_pool", 50, DECLARED, _RAW + "limit_pool_limit_up",
          "app/datasources/sources/eastmoney_ztb.py:fetch_pool", "近期窗口", notes="仅收盘归档，不混入 Fuyao 事件类型"),
    _bind("akshare", "limits.limit_up_pool", 60, DORMANT, _EVT + "limit_up_pool", "app/akshare_provider.py"),
    _bind("fuyao_ths", "limits.broken_pool", 12, LIVE_VERIFIED, _EVT + "limit_open_pool", "app/market_event_capture.py"),
    _bind("eastmoney_ztb", "limits.broken_pool", 50, DECLARED, _RAW + "limit_pool_broken", "app/datasources/sources/eastmoney_ztb.py"),
    _bind("fuyao_ths", "limits.limit_down_pool", 12, DECLARED, _EVT + "limit_down_pool", "app/market_event_capture.py"),
    _bind("eastmoney_ztb", "limits.limit_down_pool", 50, DECLARED, _RAW + "limit_pool_limit_down", "app/datasources/sources/eastmoney_ztb.py"),
    _bind("fuyao_ths", "limits.ladder", 12, LIVE_VERIFIED, _EVT + "limit_chain", "app/market_event_capture.py"),
    _bind("eastmoney_ztb", "limits.seal_detail", 50, DECLARED, _RAW + "limit_pool_limit_up",
          "app/datasources/sources/eastmoney_ztb.py:normalize_pool_item", "近期窗口，每日归档"),
    _bind("eastmoney_ztb", "limits.previous_limit_up", 50, DECLARED, _EVT + "previous_limit_pool",
          "app/datasources/sources/eastmoney_ztb.py", "近期窗口，每日归档"),
    _bind("akshare", "limits.previous_limit_up", 60, DORMANT, _EVT + "previous_limit_pool", "app/akshare_provider.py"),
    _bind("eastmoney_ztb", "limits.strong_pool", 50, DECLARED, _EVT + "strong_pool", "app/datasources/sources/eastmoney_ztb.py"),
    _bind("akshare", "limits.strong_pool", 60, DORMANT, _EVT + "strong_pool", "app/akshare_provider.py"),
    _bind("eastmoney_ztb", "limits.sub_new_pool", 50, DECLARED, _EVT + "sub_new_limit_pool", "app/datasources/sources/eastmoney_ztb.py"),
    _bind("eastmoney_ztb", "limits.anomaly_tape", 50, DECLARED, _EVT + "stock_change",
          "app/datasources/sources/eastmoney_ztb.py:fetch_stock_changes", "当日累计"),
    _bind("fuyao_ths", "limits.stock_anomaly_reason", 12, DECLARED, _EVT + "stock_anomaly", "app/market_event_capture.py"),
    # sector
    _bind("tushare_super_get", "sector.membership", 15, LIVE_VERIFIED, "sector_membership_history:taxonomy_key=ths_concept_flow",
          "app/ths_concept_members_sync.py", limits="ths_member 逐板块 + 6 次/分，全量数小时"),
    _bind("longhuvip", "sector.membership", 10, LIVE_VERIFIED, "sector_membership_history:taxonomy_key=longhu_ths_industry",
          "scripts/fill-longhu-sector-membership.py", notes="104 个行业，约 10 分钟"),
    _bind("fuyao_ths", "sector.membership", 12, DECLARED, "sector_membership_history:taxonomy_key=fuyao_ths_concept",
          "scripts/fill-fuyao-ths-membership.py", notes="概念 390 + 行业 320 + 地域 33，数分钟"),
    _bind("akshare", "sector.membership", 60, DORMANT, "sector_membership_history", "app/akshare_provider.py",
          notes="东财成分函数在 owner 出口不可用"),
    _bind("fuyao_ths", "sector.index_quote", 12, DECLARED, _RAW + "ths_index_prices_snapshot",
          "app/datasources/collectors/post_close.py:job_fuyao_valuation_index", limits="thscodes≤100"),
    _bind("eastmoney_free", "sector.flow_curve", 45, LIVE_VERIFIED, "intraday_board_flow_snapshots", "app/board_flow_capture_actions.py"),
    _bind("longhuvip", "sector.flow_curve", 10, DECLARED, "intraday_board_flow_snapshots", "app/longhu_board_flow.py",
          notes="部分数值列待字段/单位校验，不冒充净资金"),
    _bind("eastmoney_ztb", "sector.anomaly", 50, DECLARED, _RAW + "board_change_snapshot",
          "app/datasources/sources/eastmoney_ztb.py:fetch_board_changes"),
    # flow
    _bind("longhuvip_composite", "flow.stock_daily", 12, LIVE_VERIFIED, "stock_money_flow_daily:source=longhuvip_main_net",
          "app/longhu_market_sync.py", notes="全市场主力净额，每日约 5300 只"),
    _bind("tushare_super_get", "flow.stock_daily", 15, LIVE_VERIFIED, "stock_money_flow_daily:source=moneyflow_dc",
          "app/stock_money_flow_sync.py", notes="同表另有 source=moneyflow / moneyflow_ths；覆盖稀疏，口径与 Longhu 不同，不混用"),
    _bind("eastmoney_free", "flow.watch_intraday", 45, LIVE_VERIFIED, "intraday_quote_observations", "app/eastmoney_live_hydration.py",
          notes="研究用，不进决策"),
    _bind("derived_tick_flow", "flow.tick_derived", 90, DECLARED, _RAW + "tick_flow_daily",
          "app/datasources/collectors/post_close.py:job_tick_flow"),
    # lhb
    _bind("tushare_super_get", "lhb.daily", 15, LIVE_VERIFIED, "tushare_raw_records", "app/tushare_providers.py", notes="top_list/top_inst"),
    _bind("fuyao_ths", "lhb.daily", 12, DECLARED, _EVT + "lhb_ths", "app/datasources/collectors/post_close.py:job_fuyao_dragon_tiger"),
    _bind("akshare", "lhb.daily", 60, DORMANT, _EVT + "lhb_event", "app/akshare_provider.py"),
    _bind("akshare", "lhb.seat_statistics", 60, DORMANT, "raw_market_observations", "app/akshare_provider.py"),
    _bind("fuyao_ths", "lhb.seat_statistics", 12, DECLARED, _RAW + "lhb_hot_money", "app/datasources/sources/fuyao_evidence.py",
          notes="席位对应游资无官方来源，需自建映射"),
    # attention
    _bind("fuyao_ths", "attention.ths_hot_rank", 12, DECLARED, _RAW + "a_share_hot_stock_list", "app/market_event_capture.py",
          "只能从现在开始快照"),
    _bind("fuyao_ths", "attention.ths_skyrocket", 12, DECLARED, _RAW + "a_share_skyrocket_list", "app/market_event_capture.py"),
    _bind("fuyao_ths", "attention.ths_hot_history", 12, DECLARED, _RAW + "a_share_hot_stock_list_history",
          "app/datasources/collectors/post_close.py:job_fuyao_attention_close", "按日归档"),
    _bind("eastmoney_hot_rank", "attention.em_popularity", 50, DECLARED, _RAW + "hot_rank_popularity",
          "app/datasources/sources/eastmoney_hot_rank.py:fetch_rank_list"),
    _bind("eastmoney_hot_rank", "attention.em_surge", 50, DECLARED, _RAW + "hot_rank_surge",
          "app/datasources/sources/eastmoney_hot_rank.py:fetch_rank_list"),
    _bind("eastmoney_hot_rank", "attention.em_rank_history", 50, DECLARED, _RAW + "hot_rank_popularity_history",
          "scripts/backfill-eastmoney-hot-rank-history.py", "约一年"),
    # news / events
    _bind("cls_telegraph", "news.flash", 40, DECLARED, _RAW + "news_flash", "app/datasources/sources/news_flash.py:fetch_cls"),
    _bind("jin10_flash", "news.flash", 45, DECLARED, _RAW + "news_flash", "app/datasources/sources/news_flash.py:fetch_jin10"),
    _bind("eastmoney_flash", "news.flash", 45, DECLARED, _RAW + "news_flash", "app/datasources/sources/news_flash.py:fetch_eastmoney"),
    _bind("ths_flash", "news.flash", 45, DECLARED, _RAW + "news_flash", "app/datasources/sources/news_flash.py:fetch_ths"),
    _bind("cninfo_free", "news.announcements", 35, LIVE_VERIFIED, _EVT + "announcement", "app/free_market_providers.py:cninfo_announcements",
          notes="按票按需，90 天窗口"),
    _bind("cninfo_irm", "events.investor_qa", 40, DECLARED, _EVT + "investor_qa", "app/datasources/sources/investor_qa.py:fetch_cninfo"),
    _bind("sse_einteract", "events.investor_qa", 40, DECLARED, _EVT + "investor_qa", "app/datasources/sources/investor_qa.py:fetch_sse"),
    *(_bind("eastmoney_datacenter", f"events.{key}", 50, DECLARED, _EVT + event_type,
            f"app/datasources/sources/eastmoney_datacenter.py:{report}") for key, event_type, report in (
        ("restricted_release", "restricted_release_schedule", "RPT_LIFT_STAGE"),
        ("holder_trade", "holder_trade", "RPT_SHARE_HOLDER_INCREASE"),
        ("holder_count", "holder_count_change", "RPT_HOLDERNUMLATEST"),
        ("block_trade", "block_trade", "RPT_DATA_BLOCKTRADE"),
        ("earnings_forecast", "earnings_forecast", "RPT_PUBLIC_OP_NEWPREDICT"),
        ("earnings_express", "earnings_express", "RPT_FCI_PERFORMANCEE"),
        ("repurchase", "repurchase", "RPTA_WEB_GETHGLIST_NEW"),
        ("ipo_calendar", "ipo_calendar", "RPTA_APP_IPOAPPLY"),
    )),
    _bind("tushare_super_get", "events.earnings_forecast", 15, LIVE_VERIFIED, "earnings_forecasts", "app/earnings_calendar_sync.py"),
    _bind("tushare_super_get", "events.earnings_express", 15, LIVE_VERIFIED, "earnings_express", "app/earnings_calendar_sync.py"),
    _bind("tushare_super_get", "events.disclosure_schedule", 15, LIVE_VERIFIED, "disclosure_schedule", "app/earnings_calendar_sync.py"),
    _bind("akshare", "events.block_trade", 60, DORMANT, "raw_market_observations", "app/akshare_provider.py"),
    # fundamentals / reference / fund
    _bind("tushare_super_get", "fundamentals.daily_basic", 15, LIVE_VERIFIED, "daily_fundamentals:provider=tushare_super_get",
          "app/tushare_providers.py"),
    _bind("longhuvip_composite", "fundamentals.daily_basic", 25, LIVE_VERIFIED, "daily_fundamentals:provider=longhuvip_composite",
          "app/longhu_shared_full_market.py", notes="2026-09-16/17 覆盖 5129/5153 只"),
    _bind("fuyao_ths", "fundamentals.financial_statements", 12, DECLARED, None, "app/fuyao_catalog.py:a_share_*_statements"),
    _bind("tushare_super_get", "fundamentals.financial_statements", 15, DECLARED, "tushare_raw_records", "app/tushare_providers.py"),
    _bind("tdx_public", "fundamentals.capital_changes", 20, DECLARED, _RAW + "capital_changes",
          "app/datasources/sources/ticks.py:fetch_tdx_capital_changes", "上市以来全部", notes="与 pytdx 一致"),
    _bind("eastmoney_datacenter", "fundamentals.capital_changes", 50, DECLARED, None,
          "app/datasources/sources/eastmoney_datacenter.py:RPT_F10_EH_EQUITY"),
    _bind("eastmoney_datacenter", "fundamentals.margin", 50, DECLARED, _RAW + "margin_market",
          "app/datasources/collectors/post_close.py:job_eastmoney_margin", notes="明细默认关闭（~4000 行/日）"),
    _bind("tushare_super_get", "reference.instruments", 15, LIVE_VERIFIED, "instruments", "app/market_universe_sync.py"),
    _bind("tushare_super_get", "reference.trade_calendar", 15, LIVE_VERIFIED, "market_trade_calendar", "app/tushare_providers.py"),
    _bind("fuyao_ths", "reference.trade_calendar", 20, DECLARED, None, "app/fuyao_catalog.py:a_share_trading_days"),
    _bind("ttfund", "fund.nav", 50, DECLARED, _RAW + "fund_nav", "app/datasources/sources/ttfund.py:fetch_nav_history"),
    _bind("fuyao_ths", "fund.nav", 12, DECLARED, None, "app/fuyao_catalog.py:fund_performance_nav"),
    # derived
    _bind("derived_market_sentiment", "derived.market_sentiment", 90, DECLARED, _RAW + "market_sentiment_snapshot",
          "app/datasources/collectors/intraday.py:capture_sentiment"),
    _bind("derived_sentiment_cycle", "derived.sentiment_cycle", 90, LIVE_VERIFIED, "sentiment_cycle_daily",
          "app/sentiment_cycle_daily.py"),
    # deliberately retired
    _bind("longhuvip", "bars.daily", 26, RETIRED, notes="个股日K 接口（旧系统 id=7）恒空，下线；日K 走 longhuvip_composite"),
)


#: Provenance labels stored on quotes and flow fields.  Rules test these
#: properties instead of comparing vendor strings.
SOURCE_LABELS: Final[dict[str, SourceLabel]] = {label.label: label for label in (
    SourceLabel("tencent_batched_watch_quote", "tencent_free", "quote.watch_snapshot", exchange_timestamped=True),
    SourceLabel("longhuvip_watch_quote", "longhuvip", "quote.watch_snapshot", exchange_timestamped=True,
                notes="作为量能标签暂不进规则：9-15 原生量比与腾讯中位比 0.994，但 9-16/17 有腾讯盘口派生值"
                      "混入 longhuvip 行（量比≈0.03），报价合并路径查清前保持 rule_usable_flow=False"),
    SourceLabel("longhuvip_volume_derived", "longhuvip", "quote.watch_snapshot",
                notes="持牌成交量 + 本地流通股推算；同上待查"),
    SourceLabel("sina_batched_watch_quote", "sina_free", "quote.watch_snapshot", notes="无交易所时间戳契约"),
    SourceLabel("fuyao_ths_all_a_snapshot", "fuyao_ths", "quote.all_a_snapshot", notes="横截面快照，非逐票交易所时间戳"),
    SourceLabel("fuyao_ths_derived", "fuyao_ths", "quote.all_a_snapshot", rule_usable_flow=True,
                notes="全 A 快照成交量 + 本地流通股推算的量比/换手，横截面口径"),
    SourceLabel("eastmoney_watch_flow", "eastmoney_free", "flow.watch_intraday", notes="观察池篮子，非横截面，仅研究"),
    SourceLabel("longhuvip_main_net", "longhuvip_composite", "flow.stock_daily"),
    SourceLabel("moneyflow_dc", "tushare_super_get", "flow.stock_daily"),
)}
EXCHANGE_TIMESTAMPED_QUOTE_LABELS: Final[frozenset[str]] = frozenset(
    label for label, item in SOURCE_LABELS.items() if item.exchange_timestamped)
RULE_USABLE_FLOW_LABELS: Final[frozenset[str]] = frozenset(
    label for label, item in SOURCE_LABELS.items() if item.rule_usable_flow)

#: Stored sector-membership taxonomies.  Strategies select by ``kind`` and
#: only accept ``live_verified`` ones unless they opt in to weaker evidence.
TAXONOMIES: Final[dict[str, Taxonomy]] = {item.key: item for item in (
    Taxonomy("ths_concept_flow", "tushare_super_get", "ths_concept", LIVE_VERIFIED, 10,
             "ths_member 逐板块回填；9-18 曾只有 1 个板块（中断的回填）"),
    Taxonomy("ths_index_n", "tushare_super_get", "ths_concept", LIVE_VERIFIED, 12, "ths_index 概念类（N）"),
    Taxonomy("ths_industry", "tushare_super_get", "ths_industry", LIVE_VERIFIED, 15, "同花顺行业资金流（moneyflow_ind_ths）口径"),
    Taxonomy("ths_index_i", "tushare_super_get", "ths_industry", LIVE_VERIFIED, 16, "ths_index 行业类（I）"),
    Taxonomy("longhu_ths_industry", "longhuvip", "ths_industry", LIVE_VERIFIED, 20, "104 个行业，约 5300 只"),
    Taxonomy("fuyao_ths_concept", "fuyao_ths", "ths_concept", DECLARED, 30, "390 个概念全量成分，待策略侧验证"),
    Taxonomy("fuyao_ths_industry", "fuyao_ths", "ths_industry", DECLARED, 40, "320 个行业"),
    Taxonomy("fuyao_ths_region", "fuyao_ths", "ths_region", DECLARED, 50, "33 个地域"),
)}

#: Groups in the THS concept tables whose membership is a qualification, not a
#: shared business or policy driver: trading access, index inclusion, THS's own
#: curated index products, a yield screen, a certification (专精特新, every
#: industry), a rescue-fund holding and reporting-period lists.  Two names in 融资融券 (3,915 members on 2026-09-18)
#: have no reason to move together, yet a rule that takes every group for a
#: sector pairs them -- it made each name on a five-name watchlist the
#: "same-sector peer" of the other four.  Clusters that do trade together stay
#: sectors: ST板块, 次新股, 摘帽, 国企改革, 中字头股票, 参股券商, 国家大基金持股.
#: Keys are the verified THS codes; the pattern (valid as a PostgreSQL ARE and
#: as a Python ``re``) catches the lists THS mints each period or product.
NON_SECTOR_GROUPS: Final[dict[str, str]] = {
    "885338.TI": "融资融券", "885694.TI": "深股通", "885520.TI": "沪股通",
    "883300.TI": "沪深300样本股", "883301.TI": "上证50样本股", "883302.TI": "上证180成份股",
    "883303.TI": "上证380成份股", "883304.TI": "中证500成份股",
    "885916.TI": "同花顺漂亮100", "886045.TI": "同花顺中特估100", "886075.TI": "同花顺出海50",
    "886096.TI": "同花顺新质50", "886082.TI": "同花顺果指数",
    "886072.TI": "高股息精选", "885929.TI": "专精特新", "885663.TI": "证金持股",
    "886109.TI": "2026一季报预增", "886110.TI": "2026中报预增",
}
NON_SECTOR_LABEL_PATTERN: Final[str] = (
    r"^(融资融券|转融券|深股通|沪股通|港股通)|(成份股|样本股)$|^同花顺.+(\d+|指数)$"
    r"|^\d{4}.*报(预增|预减|预亏|预盈|扭亏)$"
)

_STATE_RANK: Final = {LIVE_VERIFIED: 0, DECLARED: 1, DORMANT: 2}


def _at_least(status: str, minimum: str) -> bool:
    return status in _STATE_RANK and _STATE_RANK[status] <= _STATE_RANK[minimum]


def taxonomies_for(kinds: Iterable[str], *, min_status: str = LIVE_VERIFIED) -> tuple[str, ...]:
    """Taxonomy keys of the given kinds, in preference order."""
    wanted = set(kinds)
    return tuple(item.key for item in sorted(TAXONOMIES.values(), key=lambda value: value.preference)
                 if item.kind in wanted and _at_least(item.status, min_status))


def primary_source(capability: str, *, min_status: str = LIVE_VERIFIED) -> str:
    """The highest-priority source of ``capability`` at ``min_status`` or better."""
    for binding in bindings_for(capability):
        if _at_least(binding.status, min_status):
            return binding.source
    raise LookupError(f"no {min_status} source for {capability}")


def store_values(capability: str, table: str, column: str, *, min_status: str = DORMANT) -> tuple[str, ...]:
    """Values of ``table.column`` that identify each source's stored evidence,
    in resolution order.  Replaces hard-coded ``source='...'`` filters."""
    values = []
    for binding in bindings_for(capability):
        if not binding.store or not _at_least(binding.status, min_status):
            continue
        stored_table, _, selector = binding.store.partition(":")
        name, _, value = selector.partition("=")
        if stored_table.split(" ")[0] == table and name == column and value:
            values.append(value.split(" ")[0])
    return tuple(values)


def primary_store_value(capability: str, table: str, column: str, *, min_status: str = LIVE_VERIFIED) -> str:
    """The single source to read when a strategy must not mix vendors' definitions."""
    values = store_values(capability, table, column, min_status=min_status)
    if not values:
        raise LookupError(f"no {min_status} store for {capability} in {table}.{column}")
    return values[0]


def bindings_for(capability: str, *, states: Iterable[str] = RESOLVABLE_STATES) -> list[Binding]:
    """Bindings serving ``capability`` in resolution order."""
    wanted = set(states)
    return sorted((item for item in BINDINGS if item.capability == capability and item.status in wanted),
                  key=lambda item: (item.priority, item.source))


def capabilities_of(source: str) -> list[Binding]:
    return sorted((item for item in BINDINGS if item.source == source), key=lambda item: item.capability)


def evidence_locations(capability: str) -> list[dict[str, Any]]:
    """Where stored evidence of ``capability`` lives, per source.

    Readers use this instead of hard-coding ``source='fuyao_ths'``: the
    result names the table and the filter column/value to query.
    """
    result = []
    for item in bindings_for(capability):
        if not item.store:
            continue
        table, _, selector = item.store.partition(":")
        column, _, value = selector.partition("=")
        result.append({"source": item.source, "table": table.split(" ")[0],
                       "filter": {column: value.split(" ")[0]} if column and value else {}})
    return result


def validate_catalog() -> list[str]:
    """Referential and vocabulary checks; returns problems (empty is valid)."""
    problems = []
    for key, capability in CAPABILITIES.items():
        if capability.category not in CATEGORIES:
            problems.append(f"{key}: unknown category {capability.category}")
        if capability.grain not in GRAINS:
            problems.append(f"{key}: unknown grain {capability.grain}")
        if capability.scope not in SCOPES:
            problems.append(f"{key}: unknown scope {capability.scope}")
        if not bindings_for(key):
            problems.append(f"{key}: no resolvable binding")
    for source in SOURCES.values():
        if source.license not in LICENSES:
            problems.append(f"{source.key}: unknown license {source.license}")
    seen = set()
    for item in BINDINGS:
        if item.source not in SOURCES:
            problems.append(f"binding {item.source}->{item.capability}: unknown source")
        if item.capability not in CAPABILITIES:
            problems.append(f"binding {item.source}->{item.capability}: unknown capability")
        if item.status not in BINDING_STATES:
            problems.append(f"binding {item.source}->{item.capability}: unknown status {item.status}")
        if (item.source, item.capability) in seen:
            problems.append(f"binding {item.source}->{item.capability}: duplicated")
        seen.add((item.source, item.capability))
        if item.decision_eligible and item.status not in {LIVE_VERIFIED}:
            problems.append(f"binding {item.source}->{item.capability}: decision eligible without live verification")
    for label in SOURCE_LABELS.values():
        if label.source not in SOURCES or label.capability not in CAPABILITIES:
            problems.append(f"label {label.label}: unknown source or capability")
    for taxonomy in TAXONOMIES.values():
        if taxonomy.source not in SOURCES or taxonomy.status not in BINDING_STATES:
            problems.append(f"taxonomy {taxonomy.key}: unknown source or status")
    return problems


def catalog_document() -> dict[str, Any]:
    """A deterministic, secret-free description for agents, the API and docs."""
    return {
        "version": CATALOG_VERSION,
        "sources": [{
            "key": source.key, "label": source.label, "upstream": source.upstream, "license": source.license,
            "protocol": source.protocol, "cost": source.cost, "module": source.module,
            "credential_env": list(source.credential_env), "risks": source.risks, "deploy": source.deploy,
            "capabilities": [item.capability for item in capabilities_of(source.key) if item.status != RETIRED],
        } for source in sorted(SOURCES.values(), key=lambda item: item.key)],
        "capabilities": [{
            "key": capability.key, "category": capability.category, "label": capability.label,
            "grain": capability.grain, "scope": capability.scope, "fields": list(capability.fields),
            "time_semantics": capability.time_semantics, "description": capability.description,
            "providers": [{"source": item.source, "priority": item.priority, "status": item.status, "store": item.store,
                           "history": item.history, "limits": item.limits, "notes": item.notes,
                           "decision_eligible": item.decision_eligible}
                          for item in bindings_for(capability.key, states=(LIVE_VERIFIED, DECLARED, DORMANT, UNSUPPORTED))],
        } for capability in sorted(CAPABILITIES.values(), key=lambda item: item.key)],
        "retired": [{"source": item.source, "capability": item.capability, "notes": item.notes}
                    for item in BINDINGS if item.status == RETIRED],
        "non_sector_groups": {"keys": dict(sorted(NON_SECTOR_GROUPS.items())),
                              "label_pattern": NON_SECTOR_LABEL_PATTERN},
    }


__all__ = [
    "BINDINGS", "CAPABILITIES", "CATALOG_VERSION", "EXCHANGE_TIMESTAMPED_QUOTE_LABELS", "NON_SECTOR_GROUPS",
    "NON_SECTOR_LABEL_PATTERN", "RULE_USABLE_FLOW_LABELS",
    "SOURCES", "SOURCE_LABELS", "TAXONOMIES", "bindings_for", "capabilities_of", "catalog_document",
    "evidence_locations", "primary_source", "primary_store_value", "store_values", "taxonomies_for",
    "validate_catalog",
]
