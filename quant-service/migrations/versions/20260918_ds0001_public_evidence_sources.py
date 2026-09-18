"""Register the token-free public evidence sources and their capabilities.

Revision ID: 20260918_ds0001
Revises: 20260906_0094

The revision id is deliberately outside the numeric sequence: the owner
database already records 20260918_0105 from a lineage not in this
repository, so a numbered id here could collide with it.  The operations are
idempotent (ON CONFLICT), so applying them ahead of the merge is harmless.

Every source below was probed from the owner peer on 2026-09-18 after the
close (reachability and payload shape).  They stay ``declared`` research
evidence until market-session health rows accumulate; none is decision
eligible.  The TDX quote and K-line commands are recorded as ``unsupported``:
the public hosts answered them with no rows / a 2-byte error, as they did
for the reference pytdx client.
"""

from alembic import op


revision = "20260918_ds0001"
down_revision = "20260906_0094"
branch_labels = None
depends_on = None

PROVIDERS = (
    ("eastmoney_ztb", "东方财富涨停板专题与盘口异动"),
    ("eastmoney_hot_rank", "东方财富股吧人气榜"),
    ("eastmoney_datacenter", "东方财富数据中心（解禁/股东/大宗/业绩/回购/两融）"),
    ("cls_telegraph", "财联社电报"),
    ("jin10_flash", "金十快讯"),
    ("eastmoney_flash", "东方财富 7x24 快讯"),
    ("ths_flash", "同花顺快讯"),
    ("cninfo_irm", "巨潮互动易"),
    ("sse_einteract", "上证e互动"),
    ("tdx_public", "通达信公开行情主站（历史分笔/除权除息）"),
    ("ttfund", "天天基金净值"),
    ("derived_market_sentiment", "自算短线情绪指标"),
    ("derived_tick_flow", "自算分笔资金流"),
)

CAPABILITIES = (
    ("eastmoney_ztb", "limit_pool", 50, 30), ("eastmoney_ztb", "stock_change", 50, 30),
    ("eastmoney_ztb", "board_change_snapshot", 50, 10),
    ("eastmoney_hot_rank", "hot_rank_popularity", 50, 10), ("eastmoney_hot_rank", "hot_rank_surge", 50, 10),
    ("eastmoney_datacenter", "corporate_event", 50, 20), ("eastmoney_datacenter", "margin", 50, 10),
    ("cls_telegraph", "news_flash", 40, 20), ("jin10_flash", "news_flash", 45, 20),
    ("eastmoney_flash", "news_flash", 45, 20), ("ths_flash", "news_flash", 45, 20),
    ("cninfo_irm", "investor_qa", 40, 10), ("sse_einteract", "investor_qa", 40, 10),
    ("tdx_public", "tick_history", 40, 60), ("tdx_public", "capital_changes", 40, 60),
    ("ttfund", "fund_nav", 50, 20),
    ("fuyao_ths", "limit_pool", 12, 120), ("fuyao_ths", "attention_rank", 12, 60),
    ("fuyao_ths", "stock_anomaly", 12, 60), ("fuyao_ths", "dragon_tiger", 12, 30),
    ("fuyao_ths", "valuation", 12, 120), ("fuyao_ths", "index_quote", 12, 120),
    ("fuyao_ths", "auction_snapshot", 12, 120),
    ("derived_market_sentiment", "market_sentiment_snapshot", 90, None),
    ("derived_tick_flow", "tick_flow_daily", 90, None),
)

API_ROUTES = (
    ("eastmoney_ztb", "getTopicZTPool", "declared", "daily", "涨停池：首/末封时间、封板资金、炸板次数、几天几板"),
    ("eastmoney_ztb", "getYesterdayZTPool", "declared", "daily", "昨日涨停今日表现"),
    ("eastmoney_ztb", "getTopicQSPool", "declared", "daily", "强势股池"),
    ("eastmoney_ztb", "getTopicCXPooll", "declared", "daily", "次新股池"),
    ("eastmoney_ztb", "getTopicZBPool", "declared", "daily", "炸板股池"),
    ("eastmoney_ztb", "getTopicDTPool", "declared", "daily", "跌停股池"),
    ("eastmoney_ztb", "getAllStockChanges", "declared", "realtime", "盘口异动"),
    ("eastmoney_ztb", "getAllBKChanges", "declared", "realtime", "板块异动"),
    ("eastmoney_hot_rank", "getAllCurrentList", "declared", "realtime", "人气榜 Top100"),
    ("eastmoney_hot_rank", "getAllHisRcList", "declared", "realtime", "飙升榜 Top100"),
    ("eastmoney_hot_rank", "getHisList", "declared", "daily", "个股人气排名历史（约一年）"),
    ("eastmoney_datacenter", "RPT_LIFT_STAGE", "declared", "event", "限售解禁（历史与未来）"),
    ("eastmoney_datacenter", "RPT_HOLDERNUMLATEST", "declared", "event", "股东户数"),
    ("eastmoney_datacenter", "RPT_SHARE_HOLDER_INCREASE", "declared", "event", "股东增减持"),
    ("eastmoney_datacenter", "RPT_DATA_BLOCKTRADE", "declared", "daily", "大宗交易"),
    ("eastmoney_datacenter", "RPT_PUBLIC_OP_NEWPREDICT", "declared", "event", "业绩预告"),
    ("eastmoney_datacenter", "RPT_FCI_PERFORMANCEE", "declared", "event", "业绩快报"),
    ("eastmoney_datacenter", "RPTA_WEB_GETHGLIST_NEW", "declared", "event", "股份回购"),
    ("eastmoney_datacenter", "RPTA_APP_IPOAPPLY", "declared", "event", "新股申购日历"),
    ("eastmoney_datacenter", "RPTA_RZRQ_LSHJ", "declared", "daily", "两融市场汇总"),
    ("eastmoney_datacenter", "RPTA_WEB_RZRQ_GGMX", "declared", "daily", "两融个股明细"),
    ("eastmoney_datacenter", "RPT_F10_EH_EQUITY", "declared", "event", "股本变动"),
    ("cls_telegraph", "roll_list", "declared", "realtime", "电报，等级 A/B/C 与关联个股"),
    ("jin10_flash", "get_flash_list", "declared", "realtime", "快讯，important 标记，不含 VIP"),
    ("eastmoney_flash", "getFastNewsList", "declared", "realtime", "7x24 快讯"),
    ("ths_flash", "news_push_stock", "declared", "realtime", "快讯，关联个股"),
    ("cninfo_irm", "index_search", "declared", "realtime", "互动易已回复问答"),
    ("sse_einteract", "feeds", "declared", "realtime", "e互动已回复问答（HTML）"),
    ("tdx_public", "history_transaction", "declared", "daily", "历史分笔，含竞价虚拟撮合与买卖方向"),
    ("tdx_public", "xdxr_info", "declared", "event", "除权除息与股本变迁"),
    ("tdx_public", "security_quotes", "unsupported", "realtime", "2026-09-18：公开主站返回 0 行"),
    ("tdx_public", "security_bars", "unsupported", "daily", "2026-09-18：公开主站对全部 K 线周期返回 2 字节错误"),
    ("ttfund", "lsjz", "declared", "daily", "基金单位/累计净值与日增长率"),
    ("fuyao_ths", "a_share_limit_down_pool", "declared", "event", "跌停池"),
    ("fuyao_ths", "a_share_hot_stock_list", "declared", "realtime", "同花顺热榜"),
    ("fuyao_ths", "a_share_skyrocket_list", "declared", "realtime", "同花顺飙升榜"),
    ("fuyao_ths", "a_share_hot_stock_list_history", "declared", "daily", "热榜历史（按日归档）"),
    ("fuyao_ths", "a_share_anomaly_analysis_list", "declared", "event", "个股异动与原因（AI 生成摘要）"),
    ("fuyao_ths", "a_share_dragon_tiger_list", "declared", "daily", "龙虎榜"),
    ("fuyao_ths", "a_share_auction_short_term_benchmark", "declared", "auction", "竞价短线基准"),
    ("fuyao_ths", "a_share_valuations_snapshot", "declared", "daily", "PE/PB/PS/PCF"),
    ("fuyao_ths", "ths_index_prices_snapshot", "declared", "daily", "同花顺概念/行业/地域/特色指数行情"),
    ("fuyao_ths", "ths_index_constituents", "declared", "reference", "同花顺指数成分（概念 390 + 行业 320 + 地域 33 + 特色 105）"),
)


def _literal(value: object) -> str:
    if value is None:
        return "null"
    if isinstance(value, (int, float)):
        return str(value)
    return "'" + str(value).replace("'", "''") + "'"


def upgrade() -> None:
    providers = ",".join(f"({_literal(key)},{_literal(label)},true)" for key, label in PROVIDERS)
    capabilities = ",".join(
        f"({_literal(key)},{_literal(capability)},'cn',{priority},true,{_literal(rate)})"
        for key, capability, priority, rate in CAPABILITIES
    )
    routes = ",".join(
        f"({_literal(key)},{_literal(api)},{_literal(availability)},{_literal(frequency)},false,{_literal(note)},null,"
        "'{\"probe\":\"2026-09-18 post-close from owner peer\"}'::jsonb)"
        for key, api, availability, frequency, note in API_ROUTES
    )
    op.execute(f"""
        INSERT INTO quant.providers(provider_key,label,enabled) VALUES {providers}
        ON CONFLICT(provider_key) DO UPDATE SET label=EXCLUDED.label,updated_at=now();
        INSERT INTO quant.provider_capabilities(provider_key,capability,market,priority,enabled,rate_limit_per_minute)
        VALUES {capabilities}
        ON CONFLICT(provider_key,capability,market) DO UPDATE
          SET priority=EXCLUDED.priority,rate_limit_per_minute=EXCLUDED.rate_limit_per_minute;
        INSERT INTO quant.provider_api_capabilities(provider_key,api_name,availability,frequency,decision_eligible,note,verified_at,metadata)
        VALUES {routes}
        ON CONFLICT(provider_key,api_name) DO NOTHING;
    """)


def downgrade() -> None:
    keys = ",".join(_literal(key) for key, _label in PROVIDERS)
    op.execute(f"DELETE FROM quant.providers WHERE provider_key IN ({keys})")
    op.execute("""DELETE FROM quant.provider_capabilities WHERE provider_key='fuyao_ths' AND capability IN
                  ('limit_pool','attention_rank','stock_anomaly','dragon_tiger','valuation','index_quote','auction_snapshot')""")
