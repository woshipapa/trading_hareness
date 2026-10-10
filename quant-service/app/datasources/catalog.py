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
    RETIRED, SCOPES, UNSUPPORTED, Binding, BindingSpec, CanonicalSchema, Capability, DataSource, FieldSpec, SourceLabel, Taxonomy,
)


CATALOG_VERSION: Final = "datasource-catalog-v2"

# Runtime health uses one stable physical capability name for each catalog
# binding. Keeping this translation beside source priority prevents circuit
# checks and resolver receipts from drifting independently.
HEALTH_CAPABILITY_ALIASES: Final[dict[tuple[str, str], str]] = {
    ("fuyao_ths", "quote.all_a_snapshot"): "a_share_prices_snapshot",
    ("longhuvip", "quote.watch_snapshot"): "stock_quote",
    ("tencent_free", "quote.watch_snapshot"): "order_book_quote",
    ("sina_free", "quote.watch_snapshot"): "realtime_quote",
    ("longhuvip", "quote.order_book"): "order_book_quote",
    ("tencent_free", "quote.order_book"): "order_book_quote",
    ("longhuvip", "bars.minute"): "intraday_minute",
    ("tencent_free", "bars.minute"): "intraday_minute",
}

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
    DataSource("tushare_primary", "Tushare 兼容主源（已下线）", "tushare-compatible REST", "licensed", "http_json", "paid",
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
    # The board-flow capture records its health under this key, but akshare's
    # board fund-flow functions read 同花顺 (data.10jqka.com.cn); see board_flow_units.
    DataSource("eastmoney_free", "东方财富公开行情（观察池资金流）；板块资金流沿用此键，上游实为同花顺公开页", "push2.eastmoney.com", "public_web", "http_json", "free",
               "app/free_market_providers.py", risks=_EASTMONEY_RISK),
    DataSource("xuangubao", "选股宝公开涨停/炸板/跌停池", "flash-api.xuangubao.com.cn", "public_web", "http_json", "free",
               "app/datasources/sources/xuangubao_pool.py",
               risks="公开无鉴权接口；沪市代码写作 .SS；换手率为其自身口径"),
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
    DataSource("tdx_mac", "通达信 MAC 行情服务", "MAC 0x12xx hosts :7709", "unofficial_protocol", "tcp_tdx_mac", "free",
               "app/datasources/sources/tdx_mac.py", risks="研究证据；MAC 字段与协议为非官方实现"),
    DataSource("tdx_ext", "通达信扩展行情", "tdx extended hq :7727", "unofficial_protocol", "tcp_tdx", "free",
               "app/datasources/sources/tdx_ex_market.py", risks="研究证据；A 股决策路径不得使用"),
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


def _cap(key: str, label: str, grain: str, scope: str, fields: str, time_semantics: str, description: str = "",
         category: str | None = None) -> Capability:
    names = tuple(fields.split())
    specs = tuple(
        FieldSpec(name=item.split(":", 1)[0], unit=item.split(":", 1)[1] if ":" in item else None)
        for item in names
    )
    return Capability(key, category or key.split(".", 1)[0], label, grain, scope, names, time_semantics, description,
                      CanonicalSchema(specs))


_OBSERVED = "effective=上游时间戳或采集时刻; available=采集时刻"
_SESSION_CLOSE = "effective=该交易日 15:00; available=采集时刻（盘后）"
_PUBLISHED = "effective=公开发布时间; available=首次采集时刻（重复采集不后移）"
_INGEST = "effective/available=供应商入库时钟 EITIME/EUTIME 与采集时刻取早者；从不用仅日期的公告日"

CAPABILITIES: Final[dict[str, Capability]] = {cap.key: cap for cap in (
    # quote
    _cap("quote.all_a_snapshot", "全 A L1 快照", "realtime", "all_a",
         "price:yuan pct_change:pct volume:shares turnover:yuan", _OBSERVED, "3 秒级 L1，无主力资金语义"),
    _cap("quote.index_overview", "指数概况与涨跌家数", "realtime", "market",
         "open:points high:points low:points close:points amount:yuan volume_raw up_count:count down_count:count",
         _OBSERVED, "0x051d 指数概况；涨跌家数口径不同于 0x054b 排序宽度"),
    _cap("quote.watch_snapshot", "观察池报价（交易所时间戳）", "realtime", "watchlist",
         "price:yuan volume:shares amount:yuan volume_ratio turnover_rate:pct exchange_time", _OBSERVED),
    _cap("quote.order_book", "盘口五档/十档", "realtime", "watchlist", "bid1..10:yuan ask1..10:yuan bid_vol:lots", _OBSERVED),
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
    _cap("microstructure.volume_profile", "分价成交量", "intraday", "per_symbol",
         "price:yuan volume_lots buy_lots sell_lots", _OBSERVED, category="derived"),
    _cap("microstructure.minute_series", "历史分时K线（指定日期）", "daily", "per_symbol",
         "time price:yuan volume_lots pre_close:yuan", _SESSION_CLOSE, category="derived"),
    _cap("microstructure.auction_curve", "集合竞价曲线", "intraday", "per_symbol",
         "time price:yuan matched_raw unmatched_raw unmatched_side", _OBSERVED,
         "TDX 流在 09:24:57 截止；数量单位未确认，保留 raw 命名", category="derived"),
    _cap("microstructure.unusual", "TDX 异动事件", "intraday", "all_a",
         "market code time event_type description value", _OBSERVED, category="derived"),
    _cap("microstructure.top_board", "TDX 排名榜", "intraday", "market",
         "category code price:yuan value", _OBSERVED, "研究来源；永不替代涨停池", category="derived"),
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
    _cap("sector.board_catalog", "板块目录", "reference", "board", "board_code name board_type",
         "effective=采集时刻; available=采集时刻"),
    _cap("sector.index_quote", "板块/概念指数行情", "daily", "board", "index_code last_price pct_change volume turnover", _OBSERVED),
    _cap("sector.flow_curve", "板块资金流曲线", "intraday", "board", "sector net_inflow:per-item unit (cny|100m_cny)", _OBSERVED),
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
    _cap("fundamentals.company_profile", "公司 F10 文本资料", "periodic", "per_symbol", "category filename content", "effective=采集时刻; available=采集时刻"),
    _cap("fundamentals.capital_changes", "除权除息与股本变迁", "event", "per_symbol",
         "date category cash_dividend float_shares_after_10k total_shares_after_10k", "effective=变动日; available=采集时刻"),
    _cap("fundamentals.margin", "融资融券", "daily", "all_a", "rzye rzmre rqye net_buy", "effective=T 日; available=T+1 采集"),
    _cap("reference.instruments", "证券基础信息", "reference", "all_a", "symbol name list_date is_st", "effective=入库"),
    _cap("reference.security_list", "TDX 证券列表", "reference", "all_a",
         "symbol market code name instrument_type decimal_point pre_close:yuan is_st list_source source_host",
         "effective=采集时刻; available=采集时刻（快照）"),
    _cap("context.instruments", "扩展市场品种列表", "reference", "market", "market_id code name category", "effective=服务器列表; available=采集时刻"),
    _cap("context.quote", "扩展市场快照", "realtime", "per_symbol", "market_id code price pre_close open high low volume amount open_interest", _OBSERVED),
    _cap("context.bars_daily", "扩展市场日K", "daily", "per_symbol", "market_id code datetime open high low close volume_raw amount open_interest settlement", "effective=交易日; available=采集时刻"),
    _cap("reference.trade_calendar", "交易日历", "reference", "market", "exchange calendar_date is_open", "effective=入库"),
    _cap("reference.suspensions", "停复牌（按交易日）", "daily", "all_a", "symbol suspend_date suspend_reason",
         "effective=交易日; available=入库"),
    _cap("fund.nav", "基金单位/累计净值", "daily", "fund", "fund_code nav_date unit_nav accumulated_nav daily_growth_pct",
         "effective=净值日; available=采集时刻"),
    _cap("fund.iopv", "ETF 盘中参考净值 IOPV", "realtime", "fund", "fund_code iopv:yuan pre_iopv:yuan exchange_time", _OBSERVED,
         "行情推送的盘中参考净值，不是披露的单位净值 fund.nav；fund_code 为带交易所后缀的代码（510300.SH）"),
    # derived
    _cap("derived.market_sentiment", "自算短线情绪（涨跌停/封板率/分层晋级率/昨涨停溢价/涨跌分布/量能/板块强度）",
         "intraday", "market", "limit_up_count seal_rate promotion prior_limit_up_today distribution turnover concept_strength",
         _OBSERVED, "公开口径，不等于任何供应商私有评分"),
    _cap("derived.sentiment_cycle", "日K 自算情绪周期", "daily", "market", "broken_rate max_board_height promotion_rate stage",
         "effective=交易日收盘; available=计算时刻"),
)}


def _bind(source: str, capability: str, priority: int, status: str, store: str | None = None, adapter: str | None = None,
          history: str = "", limits: str = "", notes: str = "", decision_eligible: bool = False,
          spec: BindingSpec | None = None) -> Binding:
    return Binding(source, capability, priority, status, store, adapter, history, limits, notes, decision_eligible, spec)


_EVT = "market_events:event_type="
_RAW = "raw_market_observations:capability="

BINDINGS: Final[tuple[Binding, ...]] = (
    # quote.all_a_snapshot
    _bind("fuyao_ths", "quote.all_a_snapshot", 12, LIVE_VERIFIED, _RAW + "a_share_prices_snapshot",
          "app/fuyao_provider.py:all_a_snapshot_rows", "实时", "2 页 × 5000",
          notes="owner 规则：Longhu 行业截面与观察池报价都不冒充全 A 快照"),
    _bind("akshare", "quote.all_a_snapshot", 60, DECLARED, _RAW + "realtime_quote",
          "app/akshare_provider.py:akshare_tencent_all_a_spot", "实时",
          notes="腾讯公开全 A 现货 fallback；无逐票交易所时间戳，仅补充研究覆盖"),
    _bind("eastmoney_free", "quote.all_a_snapshot", 45, UNSUPPORTED, notes="push2 clist 在 owner 出口被断连（2026-09-18）"),
    _bind("tdx_public", "quote.all_a_snapshot", 80, UNSUPPORTED, None,
          "app/datasources/sources/tdx_legacy_misc.py:fetch_all_a_snapshot",
          notes="0x054b 排序列表；主机与握手 profile 写入 CapabilityEvidence warnings；coverage=None 待 I1 证券列表能力接入（分母未知）；ST 需要证券列表而非 0x054b 名称（δ1 Q6）；price≤0 行已过滤；legacy volume 单位为手，canonical shares 乘 100；rows carry market+code, add symbol field at resolver",
          spec=BindingSpec(params={},
                           field_map={"price": "price", "pct_change": "pct_change", "volume_lots": "volume", "amount": "turnover"},
                           unit_factors={"volume": 100}, paging={"kind": "offset", "page_size": 80}, max_batch=80,
                           time_semantics="server_time_raw=TDX quote server time; available=collection time",
                           handshake_profile="login_one")),
    _bind("tdx_public", "quote.index_overview", 80, UNSUPPORTED, None,
          "app/datasources/sources/tdx_legacy_misc.py:fetch_index_overview",
          notes="0x051d；涨跌家数与 0x054b 排序宽度口径不同（δ2 D4）；OHLC 为指数点数非元；coverage=None 待 I1（分母未知）；R1 echo check 已实现（δ1 R1）；成交量单位未经实测，保留为 volume_raw",
          spec=BindingSpec(params={"symbol": "market_code"},
                           field_map={"open": "open", "high": "high", "low": "low", "close": "close",
                                      "amount": "amount", "volume_lots": "volume_raw", "up_count": "up_count",
                                      "down_count": "down_count"},
                           time_semantics="server_time_raw=TDX index server time; available=collection time",
                           handshake_profile="login_one")),
    # quote.watch_snapshot / order book / fast confirmation
    _bind("longhuvip", "quote.watch_snapshot", 10, LIVE_VERIFIED, "intraday_quote_observations",
          "app/longhu_vendor_source.py", limits="≤300 只/逻辑请求", decision_eligible=True),
    _bind("tencent_free", "quote.watch_snapshot", 50, LIVE_VERIFIED, "intraday_quote_observations",
          "app/free_market_providers.py", decision_eligible=True),
    _bind("sina_free", "quote.watch_snapshot", 55, UNSUPPORTED, "intraday_quote_observations",
          "app/free_market_providers.py",
          notes="无时间戳契约，仅兜底；owner 出口访问 hq.sinajs.cn 返回 403（2026-09-21 起，健康表从未成功）"),
    _bind("tdx_mac", "quote.watch_snapshot", 70, UNSUPPORTED, _RAW + "tdx_mac_watch_snapshot",
          "app/datasources/sources/tdx_mac.py:fetch_watch_snapshot",
          notes="0x122b；close(0x04)=price；vol(0x05) 为手，canonical shares 乘 100；换手 0x1b = 0x05 手 / 0x0b 万股；"
                "exchange_time 由适配器按 0x13 日期 + 0x14 时间组成 Asia/Shanghai 感知时间；回包按位置核对，代码不符的行丢弃并记 code_mismatch（δ1 R1）",
          spec=BindingSpec(
              params={"symbols": "symbols such as 000001.SZ"},
              field_map={"close": "price", "vol": "volume", "amount": "amount", "vol_ratio": "volume_ratio",
                         "turnover": "turnover_rate", "exchange_time": "exchange_time"},
              unit_factors={"volume": 100}, paging="batch", max_batch=80,
              time_semantics="effective=exchange_time (bits 0x13 date and 0x14 time, Asia/Shanghai); available=collection",
              handshake_profile="mac")),
    # Both providers persist depth observations in the shared quote table.  The
    # source discriminator is part of the storage contract; there is no
    # separate intraday_order_book_observations relation.
    _bind("longhuvip", "quote.order_book", 10, LIVE_VERIFIED,
          "intraday_quote_observations:source_name=longhu_order_book", "app/longhu_vendor_source.py",
          notes="十档；source_name=longhu_order_book"),
    _bind("tencent_free", "quote.order_book", 50, LIVE_VERIFIED,
          "intraday_quote_observations:source_name=tencent_order_book",
          "app/intraday_order_book_service.py", notes="五档；source_name=tencent_order_book"),
    _bind("tdx_public", "quote.order_book", 80, UNSUPPORTED, None,
          "app/datasources/sources/tdx_quotes.py:fetch_order_book",
          notes="0x053e 五档：bid1..5/ask1..5（元）、bid_vol1..5/ask_vol1..5（手）；收盘后与腾讯收盘盘口逐档一致（沪、深、北个股，"
                "价格与手数相同，scripts/data/tdx_quote_order_book_2026-10-10_mac.json）；只接受主板、创业板、科创板和北交所个股，"
                "价格按固定 /100（这些类型小数位为 2，scripts/data/tdx_quote_scale_and_bj_2026-10-10_mac.json）；"
                "指数、板块、ETF、基金、可转债等其他代码在联网前以 ValueError 拒绝（ETF、可转债和基金的价格另由 MAC 批量行情以 float 给出）；"
                "旧北交所代码按 920xxx 请求，行带 source_symbol；回包按位置核对，代码不符的行丢弃并记 code_mismatch（δ1 R1）；"
                "盘中延迟与新鲜度未测",
          spec=BindingSpec(
              params={"symbols": "stock symbols such as 600519.SH (main board, ChiNext, STAR, BJ)"},
              field_map={},
              paging="batch", max_batch=80,
              time_semantics="effective/available=collection time; the row carries no decoded exchange clock",
              handshake_profile="login_one")),
    _bind("fuyao_ths", "quote.valuation", 12, DECLARED, _RAW + "a_share_valuations_snapshot",
          "app/datasources/collectors/post_close.py:job_fuyao_valuation_index", "盘后逐日", "thscodes≤100",
          notes="可按交易日投影到 daily_fundamentals 缺失记录；pe=TTM、pb=MRQ；非完整每日指标，自动投影默认关闭"),
    _bind("tushare_primary", "quote.valuation", 20, RETIRED, "daily_fundamentals", "app/tushare_providers.py", notes="主源已下线；仅保留历史证据"),
    # bars
    _bind("tushare_super_get", "bars.daily", 15, DORMANT, "canonical_bars_daily", "app/tushare_providers.py", "多年",
          notes="2026-10-08 停用 Tushare（决策 0005）；保留 dormant 只为历史存量仍可按来源读取"),
    _bind("tushare_primary", "bars.daily", 10, RETIRED, "canonical_bars_daily", "app/tushare_providers.py", "历史",
          notes="主源已下线；仅保留历史证据"),
    _bind("tushare_backup", "bars.daily", 40, DORMANT, "canonical_bars_daily", "app/tushare_providers.py", limits="6 次/分钟",
          notes="2026-10-08 停用 Tushare（决策 0005）；保留 dormant 只为历史存量仍可按来源读取"),
    _bind("longhuvip_composite", "bars.daily", 25, LIVE_VERIFIED, "canonical_bars_daily", "app/longhu_shared_full_market.py",
          notes="收盘后授权截面"),
    _bind("baostock", "bars.daily", 30, DORMANT, "canonical_bars_daily", "app/baostock_daily_sync.py", "多年"),
    _bind("eastmoney_free", "bars.daily", 45, DORMANT, "canonical_bars_daily", "app/free_market_providers.py"),
    _bind("akshare", "bars.daily", 60, DORMANT, "raw_market_observations", "app/akshare_provider.py"),
    _bind("tdx_local", "bars.daily", 35, DECLARED, "offline CSV (research)", "app/datasources/sources/tdx_local_files.py:parse_day_bytes",
          "客户端下载的全部历史", notes="owner 工作站 CLI，不直接写 canonical"),
    _bind("fuyao_ths", "bars.daily", 20, DECLARED, None, "app/fuyao_bulk_dump_capture.py", "10 年日K + 复权因子全量导出"),
    _bind("tdx_mac", "bars.daily", 70, UNSUPPORTED, _RAW + "tdx_mac_daily_bars",
          "app/datasources/sources/tdx_mac.py:fetch_daily_bars", spec=BindingSpec(
              params={"symbol": "market+code", "count": "count (period 4 = daily)"}, field_map={},
              unit_factors={"volume": 0.01},
              time_semantics="effective=bar date; available=collection", handshake_profile="mac")),
    _bind("tencent_free", "bars.daily_adjusted", 50, LIVE_VERIFIED, "research_adjusted_bars_daily", "app/free_market_providers.py",
          notes="前复权，研究参考"),
    _bind("longhuvip", "bars.minute", 10, LIVE_VERIFIED, "intraday_minute_sessions", "app/intraday_minute_capture_actions.py",
          history="30/60 分钟周期K：GetKLineDay_W14 Type=30/60（longhu_vendor_source.stock_period_bars），"
                  "单页 ≤300 根；120 根≈30 分钟 15 个交易日 / 60 分钟 30 个交易日，含盘中形成中的K",
          notes="日期可验证才用"),
    _bind("tencent_free", "bars.minute", 50, LIVE_VERIFIED, "intraday_minute_sessions", "app/intraday_minute_capture_actions.py",
          history="5/15/30/60 分钟K：mkline（free_market_providers.tencent_period_bars），含当日K与真实开高低；"
                  "10 分钟 = 两根 5 分钟精确合成"),
    _bind("tushare_super_get", "bars.minute", 15, DORMANT, "tushare_raw_records", "app/tushare_providers.py",
          notes="rt_min；2026-10-08 停用 Tushare（决策 0005）；保留 dormant 只为历史存量仍可按来源读取"),
    _bind("tdx_local", "bars.minute", 35, DECLARED, "market_bars_minute (offline import)",
          "app/datasources/sources/tdx_local_files.py:parse_minute_bytes", "客户端保留的全部分钟线"),
    _bind("tdx_mac", "bars.minute", 70, UNSUPPORTED, _RAW + "tdx_mac_minute_bars",
          "app/datasources/sources/tdx_mac.py:fetch_minute_bars",
          notes="bars.minute 要求显式 source_available_at（本地入库时间不可替代）；MAC K 线只给 bar 时间（日期 + 当日秒数，"
                "适配器组成 Asia/Shanghai 感知的 bar_time），本绑定给不出 source_available_at，因此保持 UNSUPPORTED",
          spec=BindingSpec(
              params={"symbol": "market+code", "count": "count (period 8)"}, field_map={},
              time_semantics="effective=bar_time (wire date + seconds, Asia/Shanghai); available=none, the source gives no source_available_at",
              handshake_profile="mac")),
    _bind("longhuvip_index", "bars.index_daily", 45, DORMANT, "canonical_bars_daily", "app/longhu_market_service.py"),
    _bind("fuyao_ths", "bars.index_daily", 20, DECLARED, None, "app/fuyao_catalog.py:ths_index_prices_historical"),
    _bind("tushare_primary", "bars.adjustment_factor", 10, RETIRED, "daily_adjustment_factors:provider=tushare_primary",
          "app/tushare_providers.py", notes="主源已下线；仅保留历史证据"),
    _bind("tushare_super_sdk", "bars.adjustment_factor", 12, DORMANT, "daily_adjustment_factors:provider=tushare_super_sdk",
          "app/tushare_providers.py", notes="代理 407 时不可用"),
    _bind("tushare_super_get", "bars.adjustment_factor", 15, DORMANT, "daily_adjustment_factors:provider=tushare_super_get",
          "app/tushare_providers.py", notes="2026-10-08 停用 Tushare（决策 0005）；保留 dormant 只为历史存量仍可按来源读取"),
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
    _bind("tdx_public", "microstructure.volume_profile", 90, UNSUPPORTED,
          adapter="app/datasources/sources/tdx_microstructure.py:fetch_volume_profile",
          notes="0x051a；覆盖率核对前不进入决策；成交量单位为 lots；价格按 /100，路线记录（docs/archive/tdx-route-microstructure.md）"
                "只在个股上有证据，所以只接受个股代码，ETF、可转债等其他类型在联网前以 ValueError 拒绝",
          spec=BindingSpec(params={"symbol": "stock symbol such as 600519.SH"},
                           field_map={"price": "price", "volume_lots": "volume_lots", "buy_lots": "buy_lots", "sell_lots": "sell_lots"},
                           time_semantics="server_time_raw 是源字段；effective=采集时刻")),
    _bind("tdx_public", "microstructure.minute_series", 90, UNSUPPORTED,
          adapter="app/datasources/sources/tdx_microstructure.py:fetch_minute_series",
          notes="0x0fb4；one row per trading minute (09:31..11:30, 13:01..15:00)；第二个变长字段含义未知，保留为 unknown；覆盖率核对前不进入决策；"
                "价格按 /100，路线记录（docs/archive/tdx-route-microstructure.md）只在个股上有证据，所以只接受个股代码，ETF、可转债等其他类型"
                "在联网前以 ValueError 拒绝",
          spec=BindingSpec(params={"symbol": "stock symbol such as 600519.SH", "trade_date": "trade_date"},
                           field_map={"time": "time", "price": "price", "volume_lots": "volume_lots", "pre_close": "pre_close"},
                           time_semantics="effective=trade_date; available=采集时刻")),
    _bind("tdx_public", "microstructure.auction_curve", 90, UNSUPPORTED,
          adapter="app/datasources/sources/tdx_microstructure.py:fetch_auction_curve",
          notes="0x056a；09:24:57 截止，无字面 09:25 行；数量单位未确认",
          spec=BindingSpec(params={"symbol": "symbol such as 600519.SH"},
                           field_map={"time": "time", "price": "price", "matched_raw": "matched_raw", "unmatched_raw": "unmatched_raw", "unmatched_side": "unmatched_side"},
                           time_semantics="effective=auction time; available=采集时刻")),
    _bind("tdx_public", "microstructure.unusual", 90, UNSUPPORTED,
          adapter="app/datasources/sources/tdx_microstructure.py:fetch_unusual",
          notes="0x0563；覆盖率核对前不进入决策；limits.anomaly_tape 可在覆盖率核对后作为第二来源",
          spec=BindingSpec(params={"market": "market", "start": "start", "count": "count"},
                           field_map={"market": "market", "code": "code", "time": "time", "event_type": "event_type", "description": "description", "value": "value"},
                           time_semantics="effective=event time; available=采集时刻")),
    _bind("tdx_public", "microstructure.top_board", 90, UNSUPPORTED,
          adapter="app/datasources/sources/tdx_microstructure.py:fetch_top_board",
          notes="0x053f；覆盖率核对前不进入决策；top_board 永不替代涨停池",
          spec=BindingSpec(params={"category": "category", "size": "size"},
                           field_map={"code": "code", "price": "price", "value": "value"},
                           time_semantics="effective=采集时刻; available=采集时刻")),
    # limits
    _bind("tencent_free", "limits.prices", 12, DECLARED, "daily_trade_limits:provider=tencent_free",
          "app/datasources/sources/tencent_limits.py", limits="全市场约 68 批、每批 80 只；不足 95% 或仍是前一交易日行情即报错重试",
          notes="行情第 47/48 位为交易所公布的涨跌停价（北交所向内取整，规则推算会差 0.01）；盘前约 09:15 后可取；2026-10-09 本机探测"),
    _bind("tushare_super_get", "limits.prices", 15, RETIRED, "daily_trade_limits", "app/xiaojie_reference_repository.py",
          limits="stk_limit 全市场必须分页", notes="2026-10-08 停用 Tushare（决策 0005），盘中改由腾讯公布值替代"),
    _bind("longhuvip_composite", "limits.prices", 25, LIVE_VERIFIED, "daily_trade_limits", "app/longhu_shared_full_market.py",
          notes="16:00 左右才落库，盘中不可依赖；优先用同批腾讯行情里的公布值，缺失时才按板块比例推算"),
    _bind("tdx_mac", "limits.prices", 70, UNSUPPORTED, _RAW + "tdx_mac_limits",
          "app/datasources/sources/tdx_mac.py:fetch_limit_prices",
          notes="0x122b 位 0x20/0x21（δ3 Q5：按板块比例，ST 1.05）；trade_date 取位 0x13；回包按位置核对（δ1 R1）",
          spec=BindingSpec(
              params={"symbols": "symbols such as 000001.SZ"}, field_map={"limit_up": "up_limit", "limit_down": "down_limit"},
              paging="batch", max_batch=80, time_semantics="effective=trade_date (bit 0x13, Asia/Shanghai date); available=collection",
              handshake_profile="mac")),
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
    _bind("longhuvip", "limits.seal_detail", 10, DECLARED, _RAW + "longhu:longhu_market_wide:GetPlateInfo_w38",
          "app/datasources/sources/longhu_limit_review.py:decode_review",
          notes="涨停复盘逐股：首封时间、连板数、封单额、主力净额、成交额、流通市值、题材与原因；收盘后补充采集归档；"
                "2026-10-09 与选股宝逐只核对首封时间与连板数"),
    _bind("xuangubao", "limits.seal_detail", 20, DECLARED, "", "app/datasources/sources/xuangubao_pool.py:fetch_pool",
          notes="补末封时间、开板次数、N天M板、新股标记；公开接口按需取"),
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
    _bind("tushare_super_get", "sector.membership", 15, RETIRED, "sector_membership_history:taxonomy_key=ths_concept_flow",
          "app/sector_membership_repository.py:persist_ths_snapshot", limits="ths_member 逐板块 + 6 次/分，全量数小时",
          notes="2026-10-08 停用 Tushare（决策 0005）；ths_concept_flow 历史成分仍可读，不再刷新，新成分见 fuyao_ths_concept"),
    _bind("longhuvip", "sector.membership", 10, LIVE_VERIFIED, "sector_membership_history:taxonomy_key=longhu_ths_industry",
          "scripts/fill-longhu-sector-membership.py", notes="104 个行业，约 10 分钟"),
    _bind("fuyao_ths", "sector.membership", 12, DECLARED, "sector_membership_history:taxonomy_key=fuyao_ths_concept",
          "app/fuyao_ths_membership.py:run_batch", "每个交易日盘后刷新（成分只有当日快照，无历史区间）",
          "ths_index_list 按 tag + ths_index_constituents 每指数一次；请求间隔 1.5 秒，共用进程内 Fuyao 限频",
          notes="概念 390 + 行业 320 + 地域 33（fuyao_ths_concept/_industry/_region）；ths_member_backfill 循环 15:10-18:00 分批，"
                "只记变化（新成员 known_at=观测时刻，盘中刷新对盘中读者次日生效）"),
    _bind("akshare", "sector.membership", 60, DORMANT, "sector_membership_history", "app/akshare_provider.py",
          notes="东财成分函数在 owner 出口不可用"),
    _bind("tdx_mac", "sector.board_catalog", 70, UNSUPPORTED, _RAW + "tdx_mac_board_catalog",
          "app/datasources/sources/tdx_mac.py:fetch_board_catalog", spec=BindingSpec(
              params={}, field_map={},
              time_semantics="effective/available=collection", handshake_profile="mac")),
    _bind("tdx_mac", "sector.membership", 70, UNSUPPORTED, "sector_membership_history:taxonomy_key=tdx_mac_*",
          "app/datasources/sources/tdx_mac.py:fetch_membership",
          notes="board_type 取自 fetch_board_catalog 的同一行，调用时不扫描板块列表；类型 2（无板块）和 6（与其余类型重复）不读取；未知类型在联网前报错并点名板块键",
          spec=BindingSpec(
              params={"sector_key": "board_code of a fetch_board_catalog row",
                      "board_type": "board_type of the same row (selects the tdx_mac_type_N taxonomy)"},
              field_map={},
              time_semantics="known_at=collection UTC-aware", handshake_profile="mac")),
    _bind("fuyao_ths", "sector.index_quote", 12, DECLARED, _RAW + "ths_index_prices_snapshot",
          "app/datasources/collectors/post_close.py:job_fuyao_valuation_index", limits="thscodes≤100"),
    _bind("tdx_public", "sector.index_quote", 80, UNSUPPORTED, None,
          "app/datasources/sources/tdx_quotes.py:fetch_index_quote",
          notes="0x053e 读 880xxx/881xxx 板块指数：last_price、pre_close，pct_change 由这两者算出（pre_close 为 0 时为 None）；volume_raw、amount_raw 的单位无证据，"
                "保持 raw，不映射到 volume/turnover；板块码的买卖盘字段不是盘口（scripts/data/tdx_quote_order_book_2026-10-10_mac.json），"
                "不读取；只接受板块码，其他代码在联网前以 ValueError 拒绝；价格按固定 /100（板块码小数位 2，"
                "scripts/data/tdx_quote_scale_and_bj_2026-10-10_mac.json）；回包按位置核对，代码不符的行丢弃并记 code_mismatch（δ1 R1）；"
                "盘中延迟与新鲜度未测",
          spec=BindingSpec(
              params={"symbols": "board symbols such as 880005.SH"},
              field_map={"symbol": "index_code"},
              limits={"derived": {"pct_change": "(last_price / pre_close - 1) * 100, computed by the adapter; None when pre_close is 0"}},
              paging="batch", max_batch=80,
              time_semantics="effective/available=collection time; the row carries no decoded exchange clock",
              handshake_profile="login_one")),
    _bind("eastmoney_free", "sector.flow_curve", 45, LIVE_VERIFIED, "intraday_board_flow_snapshots", "app/board_flow_capture_actions.py",
          notes="上游实为同花顺公开资金流页 data.10jqka.com.cn（akshare stock_fund_flow_concept/industry，"
                "owner akshare 1.18.96 于 2026-10-09 核实）；键 eastmoney_free 与 eastmoney_* 板块口径是历史名；"
                "板块键为同花顺板块名，净额=流入-流出，单位亿元"),
    _bind("longhuvip", "sector.flow_curve", 10, DECLARED, "intraday_board_flow_snapshots", "app/longhu_board_flow.py",
          notes="净额列（位置 6）单位为元，条目带 unit=cny（快照级 unit 只对公开条目成立）；其余数值列待字段校验，不冒充净资金"),
    _bind("eastmoney_ztb", "sector.anomaly", 50, DECLARED, _RAW + "board_change_snapshot",
          "app/datasources/sources/eastmoney_ztb.py:fetch_board_changes"),
    # flow
    _bind("longhuvip_composite", "flow.stock_daily", 12, LIVE_VERIFIED, "stock_money_flow_daily:source=longhuvip_main_net",
          "app/longhu_market_sync.py", notes="全市场主力净额，每日约 5300 只"),
    _bind("tushare_super_get", "flow.stock_daily", 15, RETIRED, "stock_money_flow_daily:source=moneyflow_dc",
          "app/stock_money_flow_sync.py",
          notes="2026-10-08 停用 Tushare（决策 0005）；历史行仍在同表 source=moneyflow / moneyflow_dc / moneyflow_ths，口径与 Longhu 不同，不混用"),
    _bind("eastmoney_free", "flow.watch_intraday", 45, LIVE_VERIFIED, "intraday_quote_observations", "app/eastmoney_live_hydration.py",
          notes="研究用，不进决策"),
    _bind("derived_tick_flow", "flow.tick_derived", 90, DECLARED, _RAW + "tick_flow_daily",
          "app/datasources/collectors/post_close.py:job_tick_flow"),
    # lhb
    _bind("tushare_super_get", "lhb.daily", 15, RETIRED, "tushare_raw_records", "app/tushare_providers.py",
          notes="2026-10-08 停用 Tushare（决策 0005），由 Fuyao 龙虎榜替代"),
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
        ("repurchase", "repurchase", "RPTA_WEB_GETHGLIST_NEW"),
        ("ipo_calendar", "ipo_calendar", "RPTA_APP_IPOAPPLY"),
    )),
    # The reporting calendar is read a whole period at a time (earnings_calendar_sync); the
    # forecast and express reports are also archived to market_events by the close archive.
    _bind("eastmoney_datacenter", "events.earnings_forecast", 12, DECLARED, "earnings_forecasts",
          "app/earnings_calendar_sync.py",
          notes="RPT_PUBLIC_OP_NEWPREDICT 按报告期整期取，归母净利润行（万元）；2026-10-09 本机探测，owner 出口待验证"),
    _bind("eastmoney_datacenter", "events.earnings_express", 12, DECLARED, "earnings_express",
          "app/earnings_calendar_sync.py",
          notes="RPT_FCI_PERFORMANCEE 按报告期整期取（元）；2026-10-09 本机探测，owner 出口待验证"),
    _bind("eastmoney_datacenter", "reference.suspensions", 12, DECLARED, "security_suspensions",
          "app/full_market_daily_controls_sync.py",
          notes="RPT_CUSTOM_SUSPEND_DATA_INTERFACE 按交易日取，剔除开盘前已复牌的行；替代 Tushare suspend_d；2026-10-09 本机探测，owner 出口待验证"),
    _bind("eastmoney_datacenter", "events.disclosure_schedule", 12, DECLARED, "disclosure_schedule",
          "app/earnings_calendar_sync.py",
          notes="RPT_PUBLIC_BS_APPOIN 按报告期整期取：首次预约、最近改期、实际披露；2026-10-09 本机探测，owner 出口待验证"),
    *(_bind("tushare_super_get", capability, 15, RETIRED, store, "app/earnings_calendar_sync.py",
            notes="2026-10-08 停用 Tushare（决策 0005），由东财数据中心替代") for capability, store in (
        ("events.earnings_forecast", "earnings_forecasts"), ("events.earnings_express", "earnings_express"),
        ("events.disclosure_schedule", "disclosure_schedule"),
    )),
    _bind("akshare", "events.block_trade", 60, DORMANT, "raw_market_observations", "app/akshare_provider.py"),
    # fundamentals / reference / fund
    _bind("tushare_super_get", "fundamentals.daily_basic", 15, DORMANT, "daily_fundamentals:provider=tushare_super_get",
          "app/tushare_providers.py", notes="2026-10-08 停用 Tushare（决策 0005）；保留 dormant 只为历史存量仍可按来源读取"),
    _bind("longhuvip_composite", "fundamentals.daily_basic", 25, LIVE_VERIFIED, "daily_fundamentals:provider=longhuvip_composite",
          "app/longhu_shared_full_market.py", notes="2026-09-16/17 覆盖 5129/5153 只"),
    _bind("fuyao_ths", "fundamentals.financial_statements", 12, DECLARED, None, "app/fuyao_catalog.py:a_share_*_statements"),
    _bind("tushare_super_get", "fundamentals.financial_statements", 15, DORMANT, "tushare_raw_records", "app/tushare_providers.py",
          notes="2026-10-08 停用 Tushare（决策 0005）；保留 dormant 只为历史存量仍可按来源读取"),
    _bind("tdx_public", "fundamentals.financial_statements", 80, UNSUPPORTED, None,
          "app/datasources/sources/tdx_f10_finance.py:fetch_financial_summary", history="latest 0x0010 summary only",
          notes="金额和股本已在解析器中换算；0x0010 不带报告期，report_period 为 None，"
                "须与 tipinfo 第 2 列（报告期）和第 4 列（首次披露日）关联后才可用；绝不使用 updated_date",
          spec=BindingSpec(params={"symbol": "(market, code)"},
                           field_map={"statement_items": "statement_items", "report_period": "report_period"},
                           time_semantics="effective=报告期（来自 tipinfo 关联，本适配器不知道）; "
                                          "available=采集时刻；绝不用 updated_date")),
    _bind("tdx_public", "fundamentals.company_profile", 80, UNSUPPORTED, None,
          "app/datasources/sources/tdx_f10_finance.py:fetch_company_profile", notes="F10 GBK 文本；available_at 为采集时刻",
          spec=BindingSpec(params={"symbol": "(market, code)"},
                           field_map={"category": "category", "filename": "filename", "content": "content"},
                           time_semantics="effective=采集时刻; available=采集时刻")),
    _bind("tdx_public", "fundamentals.capital_changes", 20, DECLARED, _RAW + "capital_changes",
          "app/datasources/sources/ticks.py:fetch_tdx_capital_changes", "上市以来全部", notes="与 pytdx 一致"),
    _bind("eastmoney_datacenter", "fundamentals.capital_changes", 50, DECLARED, None,
          "app/datasources/sources/eastmoney_datacenter.py:RPT_F10_EH_EQUITY"),
    _bind("eastmoney_datacenter", "fundamentals.margin", 50, DECLARED, _RAW + "margin_market",
          "app/datasources/collectors/post_close.py:job_eastmoney_margin", notes="明细默认关闭（~4000 行/日）"),
    _bind("tdx_public", "reference.security_list", 20, UNSUPPORTED, None,
          "app/datasources/sources/tdx_instruments.py:fetch_security_list",
          notes="登录模式：login_one；一台确定的主机；深沪各分页，北交所仅计数+zhb.zip",
          decision_eligible=False,
          spec=BindingSpec(time_semantics="effective=采集时刻; available=采集时刻（快照）")),
    _bind("tdx_public", "reference.instruments", 19, UNSUPPORTED, None,
          "app/datasources/sources/tdx_instruments.py:fetch_instruments",
          notes="从 reference.security_list 派生：仅股票类；TDX 不提供上市日期，list_date 为 None，绝不编造",
          decision_eligible=False,
          spec=BindingSpec(field_map={"symbol": "symbol", "name": "name", "list_date": "list_date", "is_st": "is_st"},
                           time_semantics="effective=collection time; available=collection time (snapshot)")),
    _bind("fuyao_ths", "reference.instruments", 12, DECLARED, "instruments", "app/market_universe_sync.py",
          limits="ticker_list asset_type=a-share，每页 1000，翻到短页为止",
          notes="全 A 权威清单（可移出成员）：须达 minimum_rows 且沪深北齐全才落库；Longhu 收盘只增不删；2026-10-09 本机探测"),
    _bind("tushare_super_get", "reference.instruments", 15, RETIRED, "instruments", "app/market_universe_sync.py",
          notes="2026-10-08 停用 Tushare（决策 0005），stock_basic 由 Fuyao ticker_list 替代"),
    _bind("tushare_super_get", "reference.trade_calendar", 15, DORMANT, "market_trade_calendar", "app/tushare_providers.py",
          notes="2026-10-08 停用 Tushare（决策 0005）；保留 dormant 只为历史存量仍可按来源读取"),
    _bind("fuyao_ths", "reference.trade_calendar", 20, DECLARED, None, "app/fuyao_catalog.py:a_share_trading_days"),
    _bind("ttfund", "fund.nav", 50, DECLARED, _RAW + "fund_nav", "app/datasources/sources/ttfund.py:fetch_nav_history"),
    _bind("fuyao_ths", "fund.nav", 12, DECLARED, None, "app/fuyao_catalog.py:fund_performance_nav"),
    _bind("tdx_mac", "fund.iopv", 70, UNSUPPORTED, _RAW + "tdx_mac_iopv",
          "app/datasources/sources/tdx_mac.py:fetch_iopv",
          notes="0x122b 位 0x24 pre_iopv、0x27 iopv（float32，元），不是 fund.nav 的披露净值；只有 ETF 510300 对账为 MATCH"
                "（docs/archive/tdx-route-mac-fields.md）；非基金代码的这两位是别的数（scripts/data/tdx_mac_adapters_live_2026-10-10_mac.json），"
                "所以只接受 ETF、LOF 和基金类代码（etf、lof、fund），其他类型在联网前以 ValueError 拒绝；"
                "exchange_time 取同一行位 0x13/0x14 的行情更新时间，IOPV 自身的时刻不在行内；"
                "回包按位置核对，代码不符的行丢弃并记 code_mismatch（δ1 R1）",
          spec=BindingSpec(
              params={"symbols": "fund symbols such as 510300.SH (ETF, LOF, fund)"},
              field_map={"symbol": "fund_code", "iopv": "iopv", "pre_iopv": "pre_iopv", "exchange_time": "exchange_time"},
              paging="batch", max_batch=80,
              time_semantics="effective=exchange_time (bits 0x13 date and 0x14 time of the quote row, Asia/Shanghai; the "
                             "IOPV's own clock is not in the row); available=collection",
              handshake_profile="mac")),
    # derived
    _bind("derived_market_sentiment", "derived.market_sentiment", 90, DECLARED, _RAW + "market_sentiment_snapshot",
          "app/datasources/collectors/intraday.py:capture_sentiment"),
    _bind("derived_sentiment_cycle", "derived.sentiment_cycle", 90, LIVE_VERIFIED, "sentiment_cycle_daily",
          "app/sentiment_cycle_daily.py"),
    # deliberately retired
    _bind("longhuvip", "bars.daily", 26, RETIRED, notes="个股日K 接口（旧系统 id=7）恒空，下线；日K 走 longhuvip_composite"),
    _bind("tdx_ext", "context.instruments", 90, UNSUPPORTED, adapter="app/datasources/sources/tdx_ex_market.py:fetch_instruments", notes="A 股决策路径不得使用",
          spec=BindingSpec(field_map={"market_id": "market_id", "code": "code", "name": "name", "category": "category"}, time_semantics="effective=服务器列表; available=采集时刻")),
    _bind("tdx_ext", "context.quote", 90, UNSUPPORTED, adapter="app/datasources/sources/tdx_ex_market.py:fetch_quote", notes="A 股决策路径不得使用",
          spec=BindingSpec(params={"symbol": "(market_id, code)"}, field_map={"market_id": "market_id", "code": "code", "price": "price", "pre_close": "pre_close", "open": "open", "high": "high", "low": "low", "volume": "volume", "amount": "amount", "open_interest": "open_interest"}, time_semantics="effective=采集时刻（报价不带服务器时间）; available=采集时刻")),
    _bind("tdx_ext", "context.bars_daily", 90, UNSUPPORTED, adapter="app/datasources/sources/tdx_ex_market.py:fetch_bars_daily", notes="A 股决策路径不得使用",
          spec=BindingSpec(params={"symbol": "(market_id, code)"}, field_map={"market_id": "market_id", "code": "code", "datetime": "datetime", "open": "open", "high": "high", "low": "low", "close": "close", "volume_raw": "volume_raw", "amount": "amount", "open_interest": "open_interest", "settlement": "settlement"}, time_semantics="effective=K 线交易日（期货夜盘归下一交易日）; available=采集时刻")),
)

# The 120 bindings that existed without a structured BindingSpec at the P0 baseline (ca209a81), written
# out so the list cannot grow by itself: a binding without a spec must be in it, and an entry whose binding
# is gone must be deleted from it. It may only shrink, as bindings receive verified specs (P2-P6).
GRANDFATHER_BINDINGS: Final[frozenset[tuple[str, str]]] = frozenset({
    ("akshare", "bars.daily"), ("akshare", "events.block_trade"), ("akshare", "lhb.daily"),
    ("akshare", "lhb.seat_statistics"), ("akshare", "limits.limit_up_pool"),
    ("akshare", "limits.previous_limit_up"), ("akshare", "limits.strong_pool"),
    ("akshare", "quote.all_a_snapshot"), ("akshare", "sector.membership"), ("baostock", "bars.daily"),
    ("cls_telegraph", "news.flash"), ("cninfo_free", "news.announcements"), ("cninfo_irm", "events.investor_qa"),
    ("derived_market_sentiment", "derived.market_sentiment"),
    ("derived_sentiment_cycle", "derived.sentiment_cycle"), ("derived_tick_flow", "flow.tick_derived"),
    ("eastmoney_datacenter", "events.block_trade"), ("eastmoney_datacenter", "events.disclosure_schedule"),
    ("eastmoney_datacenter", "events.earnings_express"), ("eastmoney_datacenter", "events.earnings_forecast"),
    ("eastmoney_datacenter", "events.holder_count"), ("eastmoney_datacenter", "events.holder_trade"),
    ("eastmoney_datacenter", "events.ipo_calendar"), ("eastmoney_datacenter", "events.repurchase"),
    ("eastmoney_datacenter", "events.restricted_release"),
    ("eastmoney_datacenter", "fundamentals.capital_changes"), ("eastmoney_datacenter", "fundamentals.margin"),
    ("eastmoney_datacenter", "reference.suspensions"), ("eastmoney_flash", "news.flash"),
    ("eastmoney_free", "bars.daily"), ("eastmoney_free", "flow.watch_intraday"),
    ("eastmoney_free", "quote.all_a_snapshot"), ("eastmoney_free", "sector.flow_curve"),
    ("eastmoney_hot_rank", "attention.em_popularity"), ("eastmoney_hot_rank", "attention.em_rank_history"),
    ("eastmoney_hot_rank", "attention.em_surge"), ("eastmoney_ztb", "limits.anomaly_tape"),
    ("eastmoney_ztb", "limits.broken_pool"), ("eastmoney_ztb", "limits.limit_down_pool"),
    ("eastmoney_ztb", "limits.limit_up_pool"), ("eastmoney_ztb", "limits.previous_limit_up"),
    ("eastmoney_ztb", "limits.seal_detail"), ("eastmoney_ztb", "limits.strong_pool"),
    ("eastmoney_ztb", "limits.sub_new_pool"), ("eastmoney_ztb", "sector.anomaly"),
    ("fuyao_ths", "attention.ths_hot_history"), ("fuyao_ths", "attention.ths_hot_rank"),
    ("fuyao_ths", "attention.ths_skyrocket"), ("fuyao_ths", "auction.close_snapshot"),
    ("fuyao_ths", "auction.open_snapshot"), ("fuyao_ths", "auction.short_term_benchmark"),
    ("fuyao_ths", "bars.adjustment_factor"), ("fuyao_ths", "bars.daily"), ("fuyao_ths", "bars.index_daily"),
    ("fuyao_ths", "fund.nav"), ("fuyao_ths", "fundamentals.financial_statements"), ("fuyao_ths", "lhb.daily"),
    ("fuyao_ths", "lhb.seat_statistics"), ("fuyao_ths", "limits.broken_pool"), ("fuyao_ths", "limits.ladder"),
    ("fuyao_ths", "limits.limit_down_pool"), ("fuyao_ths", "limits.limit_up_pool"),
    ("fuyao_ths", "limits.stock_anomaly_reason"), ("fuyao_ths", "quote.all_a_snapshot"),
    ("fuyao_ths", "quote.valuation"), ("fuyao_ths", "reference.instruments"),
    ("fuyao_ths", "reference.trade_calendar"), ("fuyao_ths", "sector.index_quote"),
    ("fuyao_ths", "sector.membership"), ("jin10_flash", "news.flash"),
    ("longhu_qfq_derived", "bars.adjustment_factor"), ("longhuvip", "auction.open_snapshot"),
    ("longhuvip", "bars.daily"), ("longhuvip", "bars.minute"), ("longhuvip", "limits.seal_detail"),
    ("longhuvip", "quote.order_book"), ("longhuvip", "quote.watch_snapshot"), ("longhuvip", "sector.flow_curve"),
    ("longhuvip", "sector.membership"), ("longhuvip_composite", "bars.adjustment_factor"),
    ("longhuvip_composite", "bars.daily"), ("longhuvip_composite", "flow.stock_daily"),
    ("longhuvip_composite", "fundamentals.daily_basic"), ("longhuvip_composite", "limits.prices"),
    ("longhuvip_index", "bars.index_daily"), ("sina_free", "quote.watch_snapshot"),
    ("sse_einteract", "events.investor_qa"), ("tdx_local", "bars.daily"), ("tdx_local", "bars.minute"),
    ("tdx_public", "auction.history_0925"), ("tdx_public", "fundamentals.capital_changes"),
    ("tdx_public", "ticks.session"), ("tencent_free", "bars.daily_adjusted"), ("tencent_free", "bars.minute"),
    ("tencent_free", "limits.prices"), ("tencent_free", "quote.order_book"),
    ("tencent_free", "quote.watch_snapshot"), ("tencent_free", "ticks.session"), ("ths_flash", "news.flash"),
    ("ttfund", "fund.nav"), ("tushare_backup", "bars.daily"), ("tushare_primary", "bars.adjustment_factor"),
    ("tushare_primary", "bars.daily"), ("tushare_primary", "quote.valuation"),
    ("tushare_super_get", "bars.adjustment_factor"), ("tushare_super_get", "bars.daily"),
    ("tushare_super_get", "bars.minute"), ("tushare_super_get", "events.disclosure_schedule"),
    ("tushare_super_get", "events.earnings_express"), ("tushare_super_get", "events.earnings_forecast"),
    ("tushare_super_get", "flow.stock_daily"), ("tushare_super_get", "fundamentals.daily_basic"),
    ("tushare_super_get", "fundamentals.financial_statements"), ("tushare_super_get", "lhb.daily"),
    ("tushare_super_get", "limits.prices"), ("tushare_super_get", "reference.instruments"),
    ("tushare_super_get", "reference.trade_calendar"), ("tushare_super_get", "sector.membership"),
    ("tushare_super_sdk", "bars.adjustment_factor"), ("xuangubao", "limits.seal_detail"),
})
GRANDFATHER_BASELINE_SIZE: Final[int] = 120
LEGACY_SOURCE_KEYS: Final[frozenset[str]] = frozenset({
    "longhuvip", "longhuvip_composite", "longhuvip_index", "tushare_primary", "tushare_super_get",
    "tushare_super_sdk", "tushare_backup", "fuyao_ths", "eastmoney_free", "xuangubao", "eastmoney_ztb",
    "eastmoney_hot_rank", "eastmoney_datacenter", "eastmoney_flash", "tencent_free", "sina_free",
    "cninfo_free", "cninfo_irm", "sse_einteract", "cls_telegraph", "jin10_flash", "ths_flash", "ttfund",
    "tdx_public", "tdx_local", "akshare", "baostock", "derived_market_sentiment", "derived_tick_flow",
    "derived_sentiment_cycle", "longhu_qfq_derived",
})


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
    SourceLabel("akshare_tencent_all_a_snapshot", "akshare", "quote.all_a_snapshot", notes="腾讯公开横截面，日期推断且无逐票交易所时间戳"),
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
    # The four Tushare-filled THS taxonomies stopped growing on 2026-10-08
    # (decision 0005).  Their rows are verified history and stay readable for
    # replays; their replacements are fuyao_ths_* (membership),
    # longhu_ths_industry (industry flow) and eastmoney_concept (concept flow).
    Taxonomy("ths_concept_flow", "tushare_super_get", "ths_concept", LIVE_VERIFIED, 10,
             "ths_member 逐板块回填；9-18 曾只有 1 个板块（中断的回填）；2026-10-08 起不再刷新，仅历史"),
    Taxonomy("ths_index_n", "tushare_super_get", "ths_concept", LIVE_VERIFIED, 12, "ths_index 概念类（N）；2026-10-08 起不再刷新，仅历史"),
    Taxonomy("ths_industry", "tushare_super_get", "ths_industry", LIVE_VERIFIED, 15,
             "同花顺行业资金流（moneyflow_ind_ths）口径；2026-10-08 起不再刷新，仅历史"),
    Taxonomy("ths_index_i", "tushare_super_get", "ths_industry", LIVE_VERIFIED, 16, "ths_index 行业类（I）；2026-10-08 起不再刷新，仅历史"),
    Taxonomy("longhu_ths_industry", "longhuvip", "ths_industry", LIVE_VERIFIED, 20, "104 个行业，约 5300 只"),
    Taxonomy("fuyao_ths_concept", "fuyao_ths", "ths_concept", DECLARED, 30,
             "390 个概念全量成分，盘后自动刷新；作为 ths_concept_flow 的候选替代，待策略侧验证"),
    Taxonomy("fuyao_ths_industry", "fuyao_ths", "ths_industry", DECLARED, 40, "320 个行业"),
    Taxonomy("fuyao_ths_region", "fuyao_ths", "ths_region", DECLARED, 50, "33 个地域"),
    # MAC board types 0, 1, 3, 4 and 5 (tdx_mac.BOARD_TYPES), named by number: the MAC report
    # (docs/archive/tdx-route-mac.md) gives counts and sample codes but no meaning.  Type 2 returned no boards
    # and type 6 repeats boards of the others, so neither has a taxonomy.  Unverified, so never offered by
    # taxonomies_for().
    Taxonomy("tdx_mac_type_0", "tdx_mac", "tdx_mac_board", UNSUPPORTED, 70),
    Taxonomy("tdx_mac_type_1", "tdx_mac", "tdx_mac_board", UNSUPPORTED, 71),
    Taxonomy("tdx_mac_type_3", "tdx_mac", "tdx_mac_board", UNSUPPORTED, 73),
    Taxonomy("tdx_mac_type_4", "tdx_mac", "tdx_mac_board", UNSUPPORTED, 74),
    Taxonomy("tdx_mac_type_5", "tdx_mac", "tdx_mac_board", UNSUPPORTED, 75),
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


def health_capability(source: str, capability: str, *, fallback: str | None = None) -> str:
    """Return the physical provider-health capability for a catalog binding."""
    return HEALTH_CAPABILITY_ALIASES.get((source, capability), fallback or capability)


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


def _spec_problems(item: Binding) -> list[str]:
    """A spec may only name canonical fields of its capability, and scale them by plain numbers."""
    label = f"binding {item.source}->{item.capability}"
    capability = CAPABILITIES.get(item.capability)
    names = set(capability.schema.names) if capability is not None and capability.schema is not None else set()
    spec = item.spec
    problems = [f"{label}: field_map targets {target!r}, not a canonical field of the capability"
                for target in sorted(set(spec.field_map.values()) - names)]
    for field_name, factor in spec.unit_factors.items():
        if field_name not in names:
            problems.append(f"{label}: unit factor for {field_name!r}, not a canonical field (factors apply after field_map)")
        if isinstance(factor, bool) or not isinstance(factor, (int, float)) or not factor:
            problems.append(f"{label}: unit factor for {field_name!r} must be a non-zero int or float, got {factor!r}")
    return problems


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
        # A capability must stay routable; the only exception is one whose bindings all await evidence.
        if not bindings_for(key) and not any(item.capability == key and item.status == UNSUPPORTED for item in BINDINGS):
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
        if item.spec is None and (item.source, item.capability) not in GRANDFATHER_BINDINGS:
            problems.append(f"binding {item.source}->{item.capability}: missing BindingSpec outside grandfather list")
        if item.spec is None and item.source not in LEGACY_SOURCE_KEYS:
            problems.append(f"binding {item.source}->{item.capability}: new source requires BindingSpec")
    current = {(item.source, item.capability) for item in BINDINGS}
    for source, capability in sorted(GRANDFATHER_BINDINGS - current):
        problems.append(f"grandfather entry {source}->{capability} has no binding: delete it (the list only shrinks)")
    for item in BINDINGS:
        if item.spec is None:
            continue
        if (item.source, item.capability) in GRANDFATHER_BINDINGS:
            problems.append(f"grandfather entry {item.source}->{item.capability} has a BindingSpec now: delete it")
        problems.extend(_spec_problems(item))
    if len(GRANDFATHER_BINDINGS) > GRANDFATHER_BASELINE_SIZE:
        problems.append("grandfather binding list may only shrink")
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
            "schema": [{"name": field.name, "unit": field.unit, "dtype": field.dtype,
                        "nullable": field.nullable, "note": field.note}
                       for field in (capability.schema.fields if capability.schema else ())],
            "time_semantics": capability.time_semantics, "description": capability.description,
            "providers": [{"source": item.source, "priority": item.priority, "status": item.status, "store": item.store,
                           "history": item.history, "limits": item.limits, "notes": item.notes,
                           "decision_eligible": item.decision_eligible,
                           "spec": item.spec.__dict__ if item.spec else None}
                          for item in bindings_for(capability.key, states=(LIVE_VERIFIED, DECLARED, DORMANT, UNSUPPORTED))],
        } for capability in sorted(CAPABILITIES.values(), key=lambda item: item.key)],
        "retired": [{"source": item.source, "capability": item.capability, "notes": item.notes}
                    for item in BINDINGS if item.status == RETIRED],
        "non_sector_groups": {"keys": dict(sorted(NON_SECTOR_GROUPS.items())),
                              "label_pattern": NON_SECTOR_LABEL_PATTERN},
    }


__all__ = [
    "BINDINGS", "CAPABILITIES", "CATALOG_VERSION", "EXCHANGE_TIMESTAMPED_QUOTE_LABELS", "HEALTH_CAPABILITY_ALIASES", "NON_SECTOR_GROUPS",
    "NON_SECTOR_LABEL_PATTERN", "RULE_USABLE_FLOW_LABELS",
    "SOURCES", "SOURCE_LABELS", "TAXONOMIES", "bindings_for", "capabilities_of", "catalog_document",
    "evidence_locations", "health_capability", "primary_source", "primary_store_value", "store_values", "taxonomies_for",
    "validate_catalog",
]
