"""The session indicators a strategy can choose from, and what each one is.

Every indicator here has one definition, its sources in priority order
(Longhu first where Longhu serves it), its unit, where it is stored, the
route that reads it, and when in the day it should exist. indicator_health
checks each one for a session, covering presence, freshness, continuity,
coverage, plausibility and cross-source agreement.

A strategy that picks, combines or derives indicators should treat one as a
decision input only while its health says ``decision_eligible``. This is
research evidence: being registered grants no execution, and every
indicator here has ``live_effect`` none.
"""

from __future__ import annotations

from dataclasses import asdict, dataclass
from typing import Any, Final

#: When in the day an indicator should exist.
INTRADAY_MINUTE: Final = "intraday_minute"        # from the 09:25 match, every minute
SESSION_OPEN: Final = "session_open"              # once per session, shortly after the open
POST_CLOSE: Final = "post_close"                  # after the close pipeline (about 16:00-17:00)
PREVIOUS_SESSION: Final = "previous_session"      # the session before trade_date, available all day


@dataclass(frozen=True)
class Indicator:
    key: str
    label: str
    family: str
    grain: str
    unit: str
    sources: tuple[str, ...]
    storage: str
    read_api: str
    availability: str
    derivation: str


INDICATORS: Final[tuple[Indicator, ...]] = (
    Indicator("market.radar", "盘面雷达（±2%/±5%/涨跌停带，分板块）", "market", "minute", "cny / count",
              ("local_derived <- fuyao_ths a_share_prices_snapshot", "daily_trade_limits (腾讯公布涨跌停价)"),
              "raw_market_observations: local_derived/market_radar", "/api/v1/market/radar", INTRADAY_MINUTE,
              "首次触及 ±x% 的股票按方向只进不出、累计当日成交额；中间带=全池-两侧；now_* 跟随此刻价格；breadth=涨/跌/平家数"),
    Indicator("market.temperature", "情绪温度（日线 0–100，≤20 冰点、≥80 沸点）", "market", "session", "0-100",
              ("local_derived <- canonical_bars_daily 全 A 日线", "daily_trade_limits（日线缺涨跌停价时回退）"),
              "raw_market_observations: local_derived/market_temperature_daily", "/api/v1/market/temperature/daily",
              POST_CLOSE,
              "决策 0013：涨停、跌停（反向）、封板率、上涨比、昨日涨停溢价、连板晋级率、最高连板、量能比，"
              "各按前 250 日分位换成 0–100 后取平均，至少 5 项"),
    Indicator("market.temperature_intraday", "分时情绪温度（5 分钟，午休断开）", "market", "minute", "0-100",
              ("market.minute_documents", "stock.limit_prices", "market.temperature 的日线历史"),
              "raw_market_observations: local_derived/market_temperature_intraday（盘后存整日）",
              "/api/v1/market/temperature/intraday", INTRADAY_MINUTE,
              "同 8 个分项按“此刻收盘”计算，与前 250 日日线收盘值比分位；成交额按同时刻历史占比换算成全天等效"),
    Indicator("market.broad_etf_flow", "宽基 ETF 放量比（国家队足迹）", "market", "session", "ratio",
              ("fuyao_ths fund_market_historical", "tencent_free 日 K（成交额按均价估算）"),
              "raw_market_observations: local_derived/broad_etf_flow_daily", "/api/v1/market/temperature/daily",
              POST_CLOSE,
              "12 只宽基 ETF 成交额合计 ÷ 前 20 日均值，≥1.5 为放量；与温度 ≤40 同日为“冰点资金共振”，指数同日下跌为“逆势放量”"),
    Indicator("market.timing", "金 / 银指（2560 大盘时机，上证）", "market", "session", "state",
              ("fuyao_ths ths_index_prices_historical 000001.SH", "tencent_free 日 K（整段备用）"),
              "raw_market_observations: local_derived/market_timing_daily", "/api/v1/market/temperature/daily",
              POST_CLOSE,
              "MA5 上穿 MA25 且 VOL5>VOL60 确认为金指，MA5 下穿 MA25 为银指；趋势背景，不单独作信号"),
    Indicator("market.minute_documents", "全 A 分钟截面文档（一分钟一行）", "market", "minute", "rows / bytes",
              ("fuyao_ths a_share_prices_snapshot",), "raw_market_observations: fuyao_ths/a_share_minute_cross_section",
              "/api/v1/market/minute-panel", INTRADAY_MINUTE,
              "决策 0009：每分钟一份列式文档；检查存在、连续性与单份体积"),
    Indicator("market.main_net", "全市场主力净额（行业板块合计）", "market", "minute", "cny",
              ("longhuvip 授权行业排行", "eastmoney_free 键（实为同花顺公开资金流页）"),
              "intraday_board_flow_snapshots", "/api/v1/market/radar", INTRADAY_MINUTE,
              "同一快照内板块最多的一家供应商的行业净额之和，逐条按其单位换算为元"),
    Indicator("market.direction_gate", "总方向闸门", "market", "minute", "label",
              ("market.radar", "market.main_net"), "derived on read", "/api/v1/strategies/cards", INTRADAY_MINUTE,
              "±2% 带此刻在上方/下方成交额之比与主力净额符号（定义随结果返回）"),
    Indicator("limits.detail", "涨停明细（首封/末封/开板/连板/封单/原因）", "limits", "session", "cny / time / count",
              ("longhuvip 涨停复盘 GetPlateInfo_w38", "xuangubao 公开涨停池"),
              "raw_market_observations: longhuvip/longhu:*", "/api/v1/market/limit-detail", POST_CLOSE,
              "开盘啦为主、选股宝补末封/开板/N天M板，逐只给出两源首封时间差与连板数是否一致"),
    Indicator("board.concept_strength", "同花顺概念涨停强度", "board", "session", "count",
              ("fuyao_ths 涨停池 x fuyao_ths_concept 成分",), "sector_market_observations: fuyao_ths_concept_limit_strength",
              "/api/v1/market/sectors/concepts", POST_CLOSE, "收盘涨停池按时点成分计每个概念的涨停家数"),
    Indicator("board.concept_flow", "同花顺概念资金流", "board", "session", "100m_cny",
              ("同花顺公开资金流页（键 eastmoney_concept）",), "sector_market_observations: eastmoney_concept",
              "/api/v1/market/sectors/concepts", POST_CLOSE, "14:55-15:00 收盘窗口快照；净额=流入-流出，按板块名对应同花顺成分"),
    Indicator("board.industry_flow", "行业资金流", "board", "session", "cny",
              ("longhuvip_composite 收盘行业报告",), "sector_market_observations: longhu_ths_industry",
              "/api/v1/market/sectors/flows", POST_CLOSE, "开盘啦口径行业净额，按单笔大小分类，不代表机构身份"),
    Indicator("stock.money_flow", "个股主力净额（日）", "stock", "session", "cny",
              ("longhuvip 全市场收盘",), "stock_money_flow_daily: source=longhuvip_main_net",
              "/api/v1/data-readiness/features", POST_CLOSE, "开盘啦收盘全市场个股主力净额"),
    Indicator("stock.limit_prices", "涨跌停价", "stock", "session", "cny",
              ("腾讯行情公布价（字段 47/48）",), "daily_trade_limits", "/api/v1/data-readiness/features", SESSION_OPEN,
              "交易所公布涨跌停价；北交所向内取整，沪深四舍五入。盘中由小杰首轮扫描取当日价写入，收盘控制同步再对账"),
    Indicator("strategy.ledger", "策略线日候选台账", "strategy", "session", "rows",
              ("strategy_daily_candidate_ledger",), "strategy_daily_candidates", "/api/v1/strategies/cards",
              PREVIOUS_SESSION, "13 条策略线前一交易日收盘后的候选，次日开盘入场比较"),
    Indicator("events.lhb", "龙虎榜", "event", "session", "rows",
              ("fuyao_ths lhb_ths",), "market_events: lhb_ths", "/api/v1/events/lhb", PREVIOUS_SESSION,
              "每股一行：买卖额、净额、游资净额；无席位明细"),
)

BY_KEY: Final[dict[str, Indicator]] = {item.key: item for item in INDICATORS}


def registry() -> list[dict[str, Any]]:
    return [{**asdict(item), "sources": list(item.sources), "live_effect": "none"} for item in INDICATORS]


__all__ = ["BY_KEY", "INDICATORS", "INTRADAY_MINUTE", "Indicator", "POST_CLOSE", "PREVIOUS_SESSION", "SESSION_OPEN",
           "registry"]
