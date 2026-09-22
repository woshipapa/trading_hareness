#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""爱投顾内参 个股操作与收益 → 飞书 盘后日报 / 周报。

与 itougu_neican_relay.py 的分工：
  * relay 负责"有新内容就实时转发一条"，只看 appendContent/list 的第一页；
  * 本模块负责"收盘后算一次账"，用游标翻完整个可见追加流，把买入/卖出配对成
    闭环交易，用行情收盘价给持仓打浮动，然后发一条日报（每交易日）或周报（周五）。

设计约束：
  * 交易日期与时段一律 Asia/Shanghai；
  * 行情缺失、K线未更新、当日非交易日 → 一律 fail closed，宁可写"行情缺失/跳过"，
    绝不用昨收或成本价顶替；
  * 上游 appendContent 只保留最近一段窗口，因此每次跑完把交易流水并进本地
    ledger，窗口滚掉也不会丢掉已配对的建仓价；
  * 凭据与飞书发送复用 itougu_neican_relay，不在本模块另起一套 token 逻辑。
"""
import argparse
import json
import os
import re
import sys
import urllib.request
from collections import namedtuple
from datetime import date, datetime, timedelta, timezone
from pathlib import Path

CST = timezone(timedelta(hours=8))
API_PREFIX = "/teach-product/internalReference"
# 上证指数只用于判断"今天到底有没有开市"，不参与任何收益计算。
INDEX_SYMBOL = "sh000001"
DEFAULT_PRODUCTS = {
    "1661993558510538753": "猎场擒龙内参",
    "1806593447818383361": "尾盘掘金内参",
}
STATE_FILE = Path(os.environ.get("ITOUGU_PERF_STATE_FILE",
                                 "/Users/papa/codebase/n8n/state/itougu-perf-report.json"))
TARGETS_SPEC = os.environ.get("ITOUGU_PERF_TARGETS", "")
MAX_LEDGER_TRADES = 2000

Book = namedtuple("Book", "trades closed open_positions orphan_sells")


def _relay():
    """Lazily import the relay so pure-function tests need no credentials."""
    sys.path.insert(0, str(Path(__file__).resolve().parent))
    import itougu_neican_relay as relay
    return relay


# ---------------- 配置解析 ----------------
def parse_targets(spec):
    """Parse `productId=chat,chat;productId=chat` into an ordered mapping."""
    targets = {}
    for entry in (spec or "").split(";"):
        entry = entry.strip()
        if not entry:
            continue
        product, _, chats = entry.partition("=")
        product = product.strip()
        if not product:
            continue
        destinations = [c.strip() for c in chats.split(",") if c.strip()]
        targets[product] = list(dict.fromkeys(destinations))
    return targets


# ---------------- 交易流水 ----------------
def _detail(item):
    detail = item.get("stockTransactionDetail") or item.get("simulateOperationJson")
    if isinstance(detail, str):
        try:
            detail = json.loads(detail)
        except (TypeError, ValueError):
            return None
    return detail if isinstance(detail, dict) else None


def _market_prefix(code, mkt):
    mkt = (mkt or "").strip().lower()
    if mkt in ("sh", "sz", "bj"):
        return mkt
    if code.startswith("6"):
        return "sh"
    if code.startswith(("0", "3")):
        return "sz"
    return "bj"


def _parse_time(item):
    raw = (item.get("publishTime") or item.get("createTime") or "").strip()
    for fmt in ("%Y-%m-%d %H:%M:%S", "%Y-%m-%d %H:%M"):
        try:
            return datetime.strptime(raw, fmt).replace(tzinfo=CST)
        except ValueError:
            continue
    return None


def extract_trades(items):
    """Project appendContent records into normalized trades, oldest first."""
    trades = []
    for item in items or []:
        detail = _detail(item)
        if not detail:
            continue
        code = str(detail.get("stockCode") or "").strip()
        deal = detail.get("dealType")
        at = _parse_time(item)
        if not code or at is None or deal not in (0, 1):
            continue
        try:
            price = float(detail.get("price"))
        except (TypeError, ValueError):
            continue
        if price <= 0:
            continue
        trades.append({
            "append_id": str(item.get("appendContentId") or ""),
            "at": at,
            "name": str(detail.get("stockName") or "").strip(),
            "code": code,
            "symbol": _market_prefix(code, detail.get("mkt")) + code,
            "price": price,
            "position": detail.get("position"),
            "deal": int(deal),
        })
    trades.sort(key=lambda t: (t["at"], t["append_id"]))
    return trades


def pair_trades(trades):
    """FIFO-match buys with later sells; never invent a missing entry price."""
    lots = {}
    closed, orphan_sells = [], []
    for trade in trades:
        if trade["deal"] == 0:
            lots.setdefault(trade["symbol"], []).append(trade)
            continue
        queue = lots.get(trade["symbol"]) or []
        if not queue:
            orphan_sells.append(trade)
            continue
        buy = queue.pop(0)
        closed.append({
            "symbol": trade["symbol"],
            "name": trade["name"] or buy["name"],
            "code": trade["code"],
            "buy": buy,
            "sell": trade,
            "return_pct": (trade["price"] - buy["price"]) / buy["price"] * 100.0,
            "holding_days": (trade["at"].date() - buy["at"].date()).days,
        })
    open_positions = [lot for queue in lots.values() for lot in queue]
    open_positions.sort(key=lambda t: t["at"])
    return Book(trades=list(trades), closed=closed,
                open_positions=open_positions, orphan_sells=orphan_sells)


def trades_through(trades, end_date):
    """Cut the ledger at the end of a report window so a back-dated run never
    reports a position or a return that was not knowable yet."""
    if end_date is None:
        return list(trades or [])
    return [t for t in (trades or []) if t["at"].date() <= end_date]


def merge_trades(existing, new):
    """Union two trade lists by appendContentId, keeping chronological order."""
    merged = {t["append_id"]: t for t in (existing or [])}
    for trade in new or []:
        merged[trade["append_id"]] = trade
    ordered = sorted(merged.values(), key=lambda t: (t["at"], t["append_id"]))
    return ordered[-MAX_LEDGER_TRADES:]


# ---------------- 行情 ----------------
def _http_get(url, encoding, timeout=20):
    request = urllib.request.Request(url, headers={"User-Agent": "Mozilla/5.0",
                                                   "Referer": "https://finance.sina.com.cn"})
    with urllib.request.urlopen(request, timeout=timeout) as response:
        return response.read().decode(encoding, "ignore")


def parse_tencent_quotes(payload):
    """Parse qt.gtimg.cn rows; drop anything short of a full quote."""
    quotes = {}
    for line in (payload or "").splitlines():
        match = re.match(r'^v_(\w+)="(.*)";?\s*$', line.strip())
        if not match:
            continue
        fields = match.group(2).split("~")
        if len(fields) < 35:
            continue
        try:
            quote = {
                "name": fields[1],
                "last": float(fields[3]),
                "prev_close": float(fields[4]),
                "open": float(fields[5]),
                "high": float(fields[33]),
                "low": float(fields[34]),
                "session_date": datetime.strptime(fields[30][:8], "%Y%m%d").date(),
            }
        except (ValueError, IndexError):
            continue
        if quote["last"] <= 0:
            continue
        quotes[match.group(1)] = quote
    return quotes


def parse_sina_quotes(payload):
    """Parse hq.sinajs.cn rows as the fallback provider."""
    quotes = {}
    for line in (payload or "").splitlines():
        match = re.match(r'^var hq_str_(\w+)="(.*)";?\s*$', line.strip())
        if not match:
            continue
        fields = match.group(2).split(",")
        if len(fields) < 32:
            continue
        try:
            quote = {
                "name": fields[0],
                "last": float(fields[3]),
                "prev_close": float(fields[2]),
                "open": float(fields[1]),
                "high": float(fields[4]),
                "low": float(fields[5]),
                "session_date": datetime.strptime(fields[30], "%Y-%m-%d").date(),
            }
        except (ValueError, IndexError):
            continue
        if quote["last"] <= 0:
            continue
        quotes[match.group(1)] = quote
    return quotes


def fetch_quotes(symbols, verbose=False):
    """Fetch quotes from Tencent, falling back to Sina for whatever is missing."""
    wanted = [s for s in dict.fromkeys(symbols) if s]
    if not wanted:
        return {}
    quotes = {}
    try:
        quotes.update(parse_tencent_quotes(_http_get("http://qt.gtimg.cn/q=" + ",".join(wanted), "gbk")))
    except Exception as exc:                                   # noqa: BLE001 - provider outage
        if verbose:
            print("腾讯行情失败: %s" % exc, flush=True)
    missing = [s for s in wanted if s not in quotes]
    if missing:
        try:
            quotes.update(parse_sina_quotes(
                _http_get("https://hq.sinajs.cn/list=" + ",".join(missing), "gbk")))
        except Exception as exc:                               # noqa: BLE001 - provider outage
            if verbose:
                print("新浪行情失败: %s" % exc, flush=True)
    return quotes


def usable_quotes(quotes, report_date):
    """Keep only quotes that actually belong to the reported session."""
    return {symbol: quote for symbol, quote in (quotes or {}).items()
            if quote.get("session_date") == report_date}


def is_session_date(index_quote, report_date):
    """Whether the exchange traded on `report_date` (fails closed when unknown)."""
    if not index_quote:
        return False
    return index_quote.get("session_date") == report_date


# ---------------- 取数 ----------------
def fetch_append_all(business_id, headers, call=None, max_pages=60):
    """Walk the whole visible append stream with the H5 cursor protocol."""
    call = call or (lambda path, body: _relay().itougu_call(path, body, headers))
    collected = {}
    cursor, order = "", ""
    for _ in range(max_pages):
        payload = call(API_PREFIX + "/appendContent/list",
                       {"businessProductId": business_id, "orderId": order, "appendContentId": cursor})
        data = (payload or {}).get("data") or {}
        rows = data.get("listResult") or []
        for row in rows:
            collected[str(row.get("appendContentId"))] = row
        order = str(data.get("orderId") or order)
        page_size = int(data.get("pageSize") or 10)
        if not rows or len(rows) < page_size or int(rows[-1].get("type") or -1) == 0:
            break
        cursor = str(rows[-1].get("appendContentId"))
    return list(collected.values())


# ---------------- 状态 ----------------
def load_state(path=None):
    path = Path(path or STATE_FILE)
    try:
        state = json.loads(path.read_text(encoding="utf-8"))
    except (FileNotFoundError, json.JSONDecodeError, OSError):
        return {"ledger": {}, "sent": {}}
    if not isinstance(state, dict):
        return {"ledger": {}, "sent": {}}
    state.setdefault("ledger", {})
    state.setdefault("sent", {})
    return state


def save_state(path, state):
    path = Path(path or STATE_FILE)
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(state, ensure_ascii=False, indent=1), encoding="utf-8")


def store_ledger(state, product_id, trades):
    state.setdefault("ledger", {})[product_id] = [
        {**t, "at": t["at"].strftime("%Y-%m-%d %H:%M")} for t in trades]


def read_ledger(state, product_id):
    rows = (state.get("ledger") or {}).get(product_id) or []
    restored = []
    for row in rows:
        at = row.get("at")
        if isinstance(at, str):
            try:
                at = datetime.strptime(at, "%Y-%m-%d %H:%M").replace(tzinfo=CST)
            except ValueError:
                continue
        restored.append({**row, "at": at})
    restored.sort(key=lambda t: (t["at"], t["append_id"]))
    return restored


def delivery_key(kind, product_id, report_date):
    return "%s:%s:%s" % (kind, product_id, report_date.isoformat())


def already_sent(state, key):
    return key in (state.get("sent") or {})


def mark_sent(state, key):
    sent = state.setdefault("sent", {})
    sent[key] = datetime.now(timezone.utc).isoformat()
    for stale in sorted(sent)[:-400]:
        sent.pop(stale, None)


# ---------------- formatting ----------------
def fmt_price(value):
    return ("%.3f" % float(value)).rstrip("0").rstrip(".")


def fmt_pct(value):
    return "%+.2f%%" % value


def fmt_position(value):
    if value is None:
        return ""
    return "仓位%s成" % ("%.4f" % float(value)).rstrip("0").rstrip(".")


def trade_line(trade):
    side = "🟢买入" if trade["deal"] == 0 else "🔴卖出"
    bits = ["%s %s(%s) @%s" % (side, trade["name"], trade["code"], fmt_price(trade["price"])),
            trade["at"].strftime("%H:%M")]
    position = fmt_position(trade.get("position"))
    if position:
        bits.append(position)
    return "　".join(bits)


def closed_line(closed):
    return "%s(%s) %s(%s) → %s(%s)　%s　持有%d天" % (
        closed["name"], closed["code"],
        fmt_price(closed["buy"]["price"]), closed["buy"]["at"].strftime("%m-%d %H:%M"),
        fmt_price(closed["sell"]["price"]), closed["sell"]["at"].strftime("%m-%d %H:%M"),
        fmt_pct(closed["return_pct"]), closed["holding_days"])


def position_line(position, quotes):
    code = position.get("code") or str(position.get("symbol", ""))[2:]
    head = "%s(%s) 成本 %s" % (position.get("name", ""), code, fmt_price(position["price"]))
    slot = fmt_position(position.get("position"))
    if slot:
        head += " " + slot
    quote = (quotes or {}).get(position.get("symbol"))
    if not quote:
        return head + "　行情缺失"
    change = (quote["last"] - position["price"]) / position["price"] * 100.0
    return "%s　收盘 %s　浮动 %s" % (head, fmt_price(quote["last"]), fmt_pct(change))


def deliver_to_chats(chat_ids, title, text, dedup_seed, sender=None):
    """Send one report to every destination, reporting all failures together.

    A report has one or two destinations, so this stays sequential on purpose.
    It depends only on the relay's long-stable single-chat sender, which keeps
    the report deployable against whichever relay revision the edge runs.
    """
    destinations = list(dict.fromkeys(str(c).strip() for c in (chat_ids or []) if str(c).strip()))
    if not destinations:
        return []
    sender = sender or (lambda chat_id, *rest: _relay().send_feishu(chat_id, *rest))
    delivered, failures = [], []
    for chat_id in destinations:
        try:
            sender(chat_id, title, text, dedup_seed)
            delivered.append(chat_id)
        except Exception as exc:                               # noqa: BLE001 - one group must not block the rest
            failures.append("%s: %s" % (chat_id, exc))
    if failures:
        raise RuntimeError("；".join(failures))
    return delivered


def summarize(closed):
    returns = [c["return_pct"] for c in closed or []]
    if not returns:
        return {"count": 0, "wins": 0, "losses": 0, "win_rate_pct": 0.0,
                "avg_pct": 0.0, "sum_pct": 0.0, "best": None, "worst": None}
    wins = sum(1 for r in returns if r > 0)
    return {
        "count": len(returns),
        "wins": wins,
        "losses": len(returns) - wins,
        "win_rate_pct": wins / len(returns) * 100.0,
        "avg_pct": sum(returns) / len(returns),
        "sum_pct": sum(returns),
        "best": max(closed, key=lambda c: c["return_pct"]),
        "worst": min(closed, key=lambda c: c["return_pct"]),
    }


def summary_line(stats, label):
    if not stats["count"]:
        return "%s：无平仓记录" % label
    return "%s：平仓 %d 笔 · 胜 %d / 负 %d · 胜率 %.0f%% · 平均 %s · 等权简单加总 %s" % (
        label, stats["count"], stats["wins"], stats["losses"], stats["win_rate_pct"],
        fmt_pct(stats["avg_pct"]), fmt_pct(stats["sum_pct"]))


def _exit_quality(closed, quotes):
    quote = (quotes or {}).get(closed["symbol"])
    if not quote:
        return ""
    after = (quote["last"] - closed["sell"]["price"]) / closed["sell"]["price"] * 100.0
    return "　卖后至今 %s" % fmt_pct(after)


def week_window(report_date):
    monday = report_date - timedelta(days=report_date.weekday())
    return monday, monday + timedelta(days=4)


def format_daily(product_name, report_date, book, quotes):
    title = "[盘后] %s · 操作与收益日报 %s" % (product_name, report_date.isoformat())
    today = [t for t in book.trades if t["at"].date() == report_date]
    closed_today = [c for c in book.closed if c["sell"]["at"].date() == report_date]
    lines = ["🕐 %s 收盘后统计（Asia/Shanghai）" % report_date.isoformat(), ""]
    if today:
        lines.append("今日操作 %d 笔" % len(today))
        lines.extend(trade_line(t) for t in today)
    else:
        lines.append("今日无操作")
    lines.append("")
    if closed_today:
        lines.append("今日平仓 %d 笔" % len(closed_today))
        lines.extend(closed_line(c) + _exit_quality(c, quotes) for c in closed_today)
        lines.append("")
    if book.open_positions:
        lines.append("当前持仓 %d 只（按收盘价）" % len(book.open_positions))
        lines.extend(position_line(p, quotes) for p in book.open_positions)
    else:
        lines.append("当前无持仓")
    lines.append("")
    lines.append(summary_line(summarize(book.closed), "累计（可见记录内）"))
    if book.orphan_sells:
        lines.append("另有 %d 笔卖出的建仓早于可见记录，未计入收益统计：%s" % (
            len(book.orphan_sells), "、".join(t["name"] for t in book.orphan_sells)))
    lines.append("〔口径〕价位取内参公布的模拟操作价，持仓浮动按当日收盘价；不含手续费与冲击成本。")
    return title, "\n".join(lines)


def format_weekly(product_name, report_date, book, quotes):
    start, end = week_window(report_date)
    title = "[周报] %s · 操作与收益周报 %s~%s" % (product_name, start.isoformat(), end.isoformat())
    in_week = [t for t in book.trades if start <= t["at"].date() <= end]
    closed_week = [c for c in book.closed if start <= c["sell"]["at"].date() <= end]
    lines = ["🕐 %s ~ %s（Asia/Shanghai）" % (start.isoformat(), end.isoformat()), ""]
    lines.append("本周操作 %d 笔（买入 %d / 卖出 %d）" % (
        len(in_week), sum(1 for t in in_week if t["deal"] == 0), sum(1 for t in in_week if t["deal"] == 1)))
    lines.append("")
    if closed_week:
        lines.append("本周平仓 %d 笔" % len(closed_week))
        lines.extend(closed_line(c) + _exit_quality(c, quotes) for c in closed_week)
    else:
        lines.append("本周无平仓")
    lines.append("")
    stats = summarize(closed_week)
    lines.append(summary_line(stats, "本周"))
    if stats["count"]:
        lines.append("最好 %s %s · 最差 %s %s" % (
            stats["best"]["name"], fmt_pct(stats["best"]["return_pct"]),
            stats["worst"]["name"], fmt_pct(stats["worst"]["return_pct"])))
    lines.append("")
    if book.open_positions:
        lines.append("周末持仓 %d 只（按收盘价）" % len(book.open_positions))
        lines.extend(position_line(p, quotes) for p in book.open_positions)
    else:
        lines.append("周末无持仓")
    lines.append("")
    lines.append(summary_line(summarize(book.closed), "累计（可见记录内）"))
    lines.append("〔口径〕价位取内参公布的模拟操作价，持仓浮动按当周最后交易日收盘价；不含手续费与冲击成本。")
    return title, "\n".join(lines)


# ---------------- 主流程 ----------------
def build_book(product_id, state, as_of=None, verbose=False):
    """Refresh the durable ledger from upstream, then pair it as of a date."""
    headers = _relay().load_headers()
    fetched = extract_trades(fetch_append_all(product_id, headers))
    ledger = merge_trades(read_ledger(state, product_id), fetched)
    store_ledger(state, product_id, ledger)
    windowed = trades_through(ledger, as_of)
    if verbose:
        print("[%s] 上游可见 %d 笔操作，本地流水 %d 笔，截至 %s 计入 %d 笔" % (
            product_id, len(fetched), len(ledger), as_of or "最新", len(windowed)), flush=True)
    return pair_trades(windowed)


def run_report(kind, product_id, product_name, chat_ids, report_date, state,
               dry_run=False, force=False, verbose=True):
    key = delivery_key(kind, product_id, report_date)
    if already_sent(state, key) and not force:
        if verbose:
            print("已发送过 %s，跳过（--force 可重发）" % key, flush=True)
        return 0
    window_end = report_date if kind == "daily" else week_window(report_date)[1]
    book = build_book(product_id, state, as_of=window_end, verbose=verbose)
    symbols = sorted({t["symbol"] for t in book.trades} | {INDEX_SYMBOL})
    quotes = fetch_quotes(symbols, verbose=verbose)
    today = datetime.now(CST).date()
    if report_date == today and not is_session_date(quotes.get(INDEX_SYMBOL), report_date):
        if verbose:
            print("%s 非交易日或行情未更新，跳过（fail closed）" % report_date, flush=True)
        return 0
    marks = usable_quotes(quotes, report_date)
    if verbose and len(marks) < len(symbols) - 1:
        print("行情可用 %d/%d，缺失项按'行情缺失'输出" % (len(marks), len(symbols) - 1), flush=True)
    formatter = format_daily if kind == "daily" else format_weekly
    title, text = formatter(product_name, report_date, book, marks)
    if dry_run or not chat_ids:
        print("── DRY [%s]\n%s\n%s\n" % (kind, title, text), flush=True)
        if not chat_ids and not dry_run and verbose:
            print("未配置目标群（ITOUGU_PERF_TARGETS），只打印不发送", flush=True)
        return 0
    deliver_to_chats(chat_ids, title, text, "perf:%s" % key)
    mark_sent(state, key)
    if verbose:
        print("✅ 已发飞书 %s → %s" % (key, ",".join(chat_ids)), flush=True)
    return 1


def main():
    parser = argparse.ArgumentParser(description="爱投顾内参 操作与收益 盘后日报/周报")
    parser.add_argument("--daily", action="store_true", help="发当日盘后日报")
    parser.add_argument("--weekly", action="store_true", help="发本周周报（周一~周五）")
    parser.add_argument("--date", help="报告日期 YYYY-MM-DD（默认今天，Asia/Shanghai）")
    parser.add_argument("--product", action="append", help="只处理指定 businessProductId")
    parser.add_argument("--chat-id", action="append", help="覆盖目标飞书群（测试用）")
    parser.add_argument("--dry-run", action="store_true", help="只打印不发送")
    parser.add_argument("--force", action="store_true", help="忽略已发送记录，重发")
    parser.add_argument("--state-file", help="覆盖状态文件路径")
    args = parser.parse_args()
    if not args.daily and not args.weekly:
        parser.error("需要 --daily 或 --weekly")

    report_date = date.fromisoformat(args.date) if args.date else datetime.now(CST).date()
    state_path = Path(args.state_file or STATE_FILE)
    state = load_state(state_path)
    targets = parse_targets(TARGETS_SPEC)
    if args.product:
        targets = {p: targets.get(p, []) for p in args.product}
    if not targets:
        targets = {next(iter(DEFAULT_PRODUCTS)): []}

    sent = 0
    for product_id, chat_ids in targets.items():
        name = DEFAULT_PRODUCTS.get(product_id, "内参 %s" % product_id)
        destinations = args.chat_id or chat_ids
        for kind in [k for k in ("daily", "weekly") if getattr(args, k)]:
            try:
                sent += run_report(kind, product_id, name, destinations, report_date, state,
                                   dry_run=args.dry_run, force=args.force)
            except Exception as exc:                           # noqa: BLE001 - one product must not block others
                print("[%s] %s 报告失败: %s" % (name, kind, exc), flush=True)
    if not args.dry_run:
        save_state(state_path, state)
    return 0


if __name__ == "__main__":
    sys.exit(main())
