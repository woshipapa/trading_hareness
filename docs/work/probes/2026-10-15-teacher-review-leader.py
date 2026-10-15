"""Read-only owner probe for the 2026-10-15 teacher review names."""

from datetime import datetime, time
import json
from zoneinfo import ZoneInfo

from app.main import db


DAY = datetime(2026, 10, 15, tzinfo=ZoneInfo("Asia/Shanghai")).date()
NAMES = (
    "九阳", "时代万恒", "联亚", "澳弘电子", "果麦文化", "百洋股份",
    "满坤科技", "近岸蛋白", "善水科技", "新华文轩", "领湃科技", "慈文传媒",
    "中文传媒", "新华传媒",
)


def main() -> None:
    start = datetime.combine(DAY, time(9, 15), ZoneInfo("Asia/Shanghai"))
    end = datetime.combine(DAY, time(15, 0), ZoneInfo("Asia/Shanghai"))
    with db.transaction() as connection:
        connection.execute("SET LOCAL statement_timeout = '20s'")
        instruments = connection.execute(
            """SELECT symbol,name FROM quant.instruments
                WHERE name = ANY(%s) OR name ILIKE ANY(%s)
                ORDER BY name,symbol""",
            (list(NAMES), [f"%{name}%" for name in NAMES]),
        ).fetchall()
        symbols = sorted({str(row["symbol"]) for row in instruments})
        print({"instruments": [dict(row) for row in instruments]})
        if not symbols:
            return
        daily = connection.execute(
            """SELECT symbol,trading_date,open,high,low,close,pre_close,amount,limit_up
                 FROM quant.canonical_bars_daily
                WHERE symbol=ANY(%s) AND trading_date BETWEEN %s AND %s
                ORDER BY trading_date,symbol""",
            (symbols, DAY.replace(day=DAY.day - 1), DAY),
        ).fetchall()
        print({"daily": [dict(row) for row in daily]})
        quotes = connection.execute(
            """SELECT symbol,observed_at,source_name,price,pct_change,volume_ratio,
                      turnover_rate,main_net_inflow,raw
                 FROM quant.intraday_quote_observations
                WHERE symbol=ANY(%s) AND observed_at >= %s AND observed_at <= %s
                ORDER BY symbol,observed_at,source_name""",
            (symbols, start, end),
        ).fetchall()
        quote_summary = {}
        for row in quotes:
            item = dict(row)
            raw = item.get("raw") if isinstance(item.get("raw"), dict) else {}
            book = raw.get("watch_quote") if isinstance(raw.get("watch_quote"), dict) else {}
            summary = quote_summary.setdefault(item["symbol"], {
                "first": item["observed_at"], "last": item["observed_at"],
                "sources": set(), "prices": [], "pct": [], "turnover_rates": [],
                "book": [],
            })
            summary["first"] = min(summary["first"], item["observed_at"])
            summary["last"] = max(summary["last"], item["observed_at"])
            summary["sources"].add(item["source_name"])
            if item.get("price") is not None:
                summary["prices"].append(float(item["price"]))
            if item.get("pct_change") is not None:
                summary["pct"].append(float(item["pct_change"]))
            if item.get("turnover_rate") is not None:
                summary["turnover_rates"].append(float(item["turnover_rate"]))
            if book:
                summary["book"].append({
                    "at": item["observed_at"], "side": book.get("book_side"),
                    "seal_volume_lot": book.get("seal_volume_lot"),
                    "outer_volume_lot": book.get("outer_volume_lot"),
                    "inner_volume_lot": book.get("inner_volume_lot"),
                })
        for summary in quote_summary.values():
            summary["sources"] = sorted(summary["sources"])
            summary["min_price"] = min(summary["prices"]) if summary["prices"] else None
            summary["max_price"] = max(summary["prices"]) if summary["prices"] else None
            summary["last_pct"] = summary["pct"][-1] if summary["pct"] else None
            summary["last_turnover_rate"] = summary["turnover_rates"][-1] if summary["turnover_rates"] else None
            summary.pop("prices")
            summary.pop("pct")
            summary.pop("turnover_rates")
            summary["book_samples"] = summary.pop("book")[-5:]
        print({"quote_summary": quote_summary})
        events = connection.execute(
            """SELECT symbol,event_type,occurred_at,source,body
                 FROM quant.market_events
                WHERE symbol=ANY(%s) AND occurred_at >= %s AND occurred_at <= %s
                  AND event_type IN ('limit_up_pool','limit_chain','limit_open_pool')
                ORDER BY occurred_at,symbol,event_type""",
            (symbols, start, end),
        ).fetchall()
        latest_events = {}
        for row in events:
            body = row["body"]
            try:
                body = json.loads(body) if isinstance(body, str) else dict(body or {})
            except (TypeError, ValueError):
                body = {}
            key = (str(row["symbol"]), str(row["event_type"]))
            latest_events[key] = {
                "symbol": row["symbol"], "event_type": row["event_type"],
                "occurred_at": row["occurred_at"], "source": row["source"],
                "continue_day_text": body.get("continue_day_text"),
                "continue_day_cnt": body.get("continue_day_cnt"),
                "limit_up_time": body.get("limit_up_time"),
                "limit_up_reason": body.get("limit_up_reason"),
                "max_seal_money": body.get("max_seal_money"),
                "turnover_ratio_pct": body.get("turnover_ratio_pct"),
                "open_times": body.get("open_times"),
            }
        print({"latest_events": list(latest_events.values())})


if __name__ == "__main__":
    main()
