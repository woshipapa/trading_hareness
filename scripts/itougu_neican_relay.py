#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""爱投顾内参 → 飞书 中继。

两种触发方式共用同一套"取增量 + 发飞书 + 去重"逻辑：
  1) 事件驱动：wechat-biz-relay.py 检测到爱投顾(gh_6569bb074cc9)内参推送这条"聊天消息"时，
     调用 trigger_from_push()，立刻拉正文发飞书。（近实时，零轮询）
  2) 低频兵底：launchd 每 1-2 分钟(交易时段)跑一次 `--once`，防止个别推送漏检。

去重两层：本地 state 记已发的 appendContentId + 飞书 uuid(基于 appendContentId) 幂等。
凭据：itougu 用 wechat-export-macos/itougu_auth.json 里的长效 token；飞书用 n8n/.env 的 APP_ID/SECRET。
"""
import argparse
import html
import json
import os
import re
import sys
import time
import uuid
import urllib.request
import urllib.error
from datetime import datetime, timezone, timedelta
from pathlib import Path

CST = timezone(timedelta(hours=8))

# ---- 配置 ----
GH = "gh_6569bb074cc9"                       # 爱投顾公众号
AUTH_FILE = Path(os.environ.get("ITOUGU_AUTH_FILE", "/Users/papa/codebase/wechat-export-macos/itougu_auth.json"))
ENV_FILE = Path(os.environ.get("ITOUGU_ENV_FILE", "/Users/papa/codebase/n8n/.env"))
STATE_FILE = Path(os.environ.get("ITOUGU_STATE_FILE", "/Users/papa/codebase/n8n/state/itougu-neican.json"))
CHAT_IDS = [c for c in os.environ.get("ITOUGU_CHAT_IDS", "oc_570aeb3bbfb11fa2be66b25ca4568aad").split(",") if c.strip()]
# Optional product-specific fan-out.  It lets the dedicated 擒龙 group stay
# focused while the general public-account group continues to receive both.
QINLONG_CHAT_IDS = [c for c in os.environ.get("ITOUGU_QINLONG_CHAT_IDS", "").split(",") if c.strip()]
API_BASE = "https://group-api.itougu.com"
PFX = "/teach-product/internalReference"
SUCCESS = 20000

# WeChat 推送里的 productId(=businessProductId) -> 名称
WATCH = {
    "1806593447818383361": "尾盘掘金内参",
    "1661993558510538753": "猎场擒龙内参",
}


def chat_ids_for_product(business_id, override=None):
    """Return the configured destinations for one internal-reference product."""
    if override is not None:
        return override
    if business_id == "1661993558510538753" and QINLONG_CHAT_IDS:
        return QINLONG_CHAT_IDS
    return CHAT_IDS

_feishu_token = {"value": "", "expires_at": 0.0}
_tag_re = re.compile(r"<[^>]+>")


# ---------------- itougu ----------------
def load_headers():
    if not AUTH_FILE.exists():
        raise RuntimeError("缺少 %s（先用 itougu_capture.py 抓一次凭据）" % AUTH_FILE)
    h = dict(json.loads(AUTH_FILE.read_text(encoding="utf-8")).get("headers", {}))
    if not h.get("Authorization"):
        raise RuntimeError("itougu_auth.json 里没有 Authorization")
    h["Content-Type"] = "application/json"
    return h


def itougu_call(path, body, headers):
    data = json.dumps(body, separators=(",", ":")).encode("utf-8")
    req = urllib.request.Request(API_BASE + path, data=data, headers=headers, method="POST")
    with urllib.request.urlopen(req, timeout=25) as r:
        j = json.loads(r.read().decode("utf-8", "ignore"))
    code = j.get("code")
    msg = str(j.get("msg", ""))
    if code != SUCCESS and any(k in msg for k in ("登录", "token", "Token", "失效", "过期", "鉴权")):
        raise RuntimeError("itougu token 失效：%s（请重跑 itougu_capture.py）" % (msg or code))
    return j


def fetch_meta(business_id, headers):
    try:
        d = (itougu_call(PFX + "/user/getInfoById", {"businessProductId": business_id}, headers).get("data") or {})
    except Exception:
        return {}
    cons = d.get("consultants") or []
    return {"productName": d.get("productName"), "consultants": "/".join(c.get("consultantName", "") for c in cons)}


def fetch_append(business_id, headers, page_size=30):
    j = itougu_call(PFX + "/appendContent/list", {"businessProductId": business_id, "pageNo": 1, "pageSize": page_size}, headers)
    return (j.get("data") or {}).get("listResult") or []


def html2text(s):
    if not s:
        return ""
    s = s.replace("</p>", "\n").replace("<br>", "\n").replace("<br/>", "\n").replace("<br />", "\n")
    s = html.unescape(_tag_re.sub("", s))
    return "\n".join(ln.strip() for ln in s.splitlines() if ln.strip())


def item_consultant(it):
    c = it.get("consultantName")
    if isinstance(c, str) and c.strip().startswith("["):
        try:
            return "/".join(x.get("consultantName", "") for x in json.loads(c))
        except Exception:
            pass
    return c or ""


DEAL = {0: "🟢买入/加仓", 1: "🔴卖出/离场"}


def _trade_detail(it):
    d = it.get("stockTransactionDetail") or it.get("simulateOperationJson")
    if isinstance(d, str):
        try:
            d = json.loads(d)
        except Exception:
            return None
    return d if isinstance(d, dict) and d.get("stockName") else None


def trade_line(it):
    d = _trade_detail(it)
    if not d:
        return ""
    code, mkt = d.get("stockCode", ""), (d.get("mkt") or "").upper()
    who = "%s（%s.%s）" % (d.get("stockName", ""), code, mkt) if code else d.get("stockName", "")
    parts = ["📊 %s %s" % (DEAL.get(d.get("dealType"), "操作"), who)]
    if d.get("price") is not None:
        parts.append("价位 %s" % d["price"])
    if d.get("position") is not None:
        parts.append("仓位 %s" % d["position"])
    return "　".join(parts)


def report_line(it):
    rl = it.get("reportList") or []
    if not rl or not isinstance(rl, list):
        return ""
    r = rl[0]
    bits = [x for x in ["📄 研报", r.get("orgName", ""), r.get("emRatingName", ""),
                        "《%s》" % r.get("title", "") if r.get("title") else "", r.get("filePath", "")] if x]
    return " ".join(bits)


def format_item(name, it, delivery_label=""):
    when = it.get("publishTime") or it.get("createTime") or ""
    who = item_consultant(it)
    title = "%s · %s" % (name, who) if who else name
    if delivery_label:
        # Keep the delivery-path marker terse.  It is an operational label,
        # not a disclosure of credentials, endpoints, or listener details.
        title = "[%s] %s" % (delivery_label, title)
    lines = ["🕐 %s" % when]
    trade = trade_line(it)
    if trade:
        lines.append(trade)
    body = html2text(it.get("content"))
    if body:
        lines.append(body)
    report = report_line(it)
    if report:
        lines.append(report)
    tip = html2text(it.get("tipContent"))
    if tip:
        lines.append("〔提示〕" + tip.replace("\n", " "))
    if it.get("stockOfPool"):
        lines.append("〔股票池〕" + str(it["stockOfPool"]))
    return title, "\n".join(lines)


# ---------------- 飞书 ----------------
def load_feishu_env():
    if os.environ.get("FEISHU_APP_ID") and os.environ.get("FEISHU_APP_SECRET"):
        return
    try:
        for line in ENV_FILE.read_text(encoding="utf-8").splitlines():
            line = line.strip()
            if not line or line.startswith("#") or "=" not in line:
                continue
            k, v = line.split("=", 1)
            k, v = k.strip(), v.strip()
            if k in {"FEISHU_APP_ID", "FEISHU_APP_SECRET"} and not os.environ.get(k):
                if len(v) >= 2 and v[0] == v[-1] and v[0] in "'\"":
                    v = v[1:-1]
                os.environ[k] = v
    except OSError:
        pass


def feishu_token():
    now = time.time()
    if _feishu_token["value"] and _feishu_token["expires_at"] > now + 60:
        return _feishu_token["value"]
    load_feishu_env()
    app_id, secret = os.environ.get("FEISHU_APP_ID", ""), os.environ.get("FEISHU_APP_SECRET", "")
    if not app_id or not secret:
        raise RuntimeError("缺少 FEISHU_APP_ID / FEISHU_APP_SECRET")
    body = json.dumps({"app_id": app_id, "app_secret": secret}).encode()
    req = urllib.request.Request("https://open.feishu.cn/open-apis/auth/v3/tenant_access_token/internal",
                                 data=body, method="POST", headers={"content-type": "application/json"})
    with urllib.request.urlopen(req, timeout=20) as r:
        res = json.loads(r.read().decode("utf-8"))
    if res.get("code") not in (None, 0) or not res.get("tenant_access_token"):
        raise RuntimeError("飞书 token 失败: %s" % res.get("msg"))
    _feishu_token.update(value=res["tenant_access_token"], expires_at=now + max(60, int(res.get("expire", 7200))))
    return _feishu_token["value"]


def send_feishu(chat_id, title, text, dedup_seed):
    token = feishu_token()
    chunks = [text[i:i + 28000] for i in range(0, len(text), 28000)] or [""]
    for idx, chunk in enumerate(chunks):
        t = title if idx == 0 else "%s（续 %d/%d）" % (title, idx + 1, len(chunks))
        content = {"zh_cn": {"title": t[:120], "content": [[{"tag": "text", "text": chunk}]]}}
        body = json.dumps({
            "receive_id": chat_id, "msg_type": "post",
            "content": json.dumps(content, ensure_ascii=False),
            "uuid": str(uuid.uuid5(uuid.NAMESPACE_URL, "itougu-neican:%s:%s:%d" % (dedup_seed, chat_id, idx))),
        }, ensure_ascii=False).encode()
        req = urllib.request.Request("https://open.feishu.cn/open-apis/im/v1/messages?receive_id_type=chat_id",
                                     data=body, method="POST",
                                     headers={"content-type": "application/json", "authorization": "Bearer " + token})
        with urllib.request.urlopen(req, timeout=20) as r:
            res = json.loads(r.read().decode("utf-8"))
        if res.get("code") not in (None, 0):
            raise RuntimeError("飞书发送失败: code=%s msg=%s" % (res.get("code"), res.get("msg")))


# ---------------- state ----------------
def load_state():
    try:
        s = json.loads(STATE_FILE.read_text(encoding="utf-8"))
        if isinstance(s.get("seen"), dict):
            return s
    except (FileNotFoundError, json.JSONDecodeError, OSError):
        pass
    return {"seen": {}}


def save_state(state):
    STATE_FILE.parent.mkdir(parents=True, exist_ok=True)
    for bid, ids in state["seen"].items():
        state["seen"][bid] = ids[-500:]     # 每个内参最多记 500 个已发 id
    STATE_FILE.write_text(json.dumps(state, ensure_ascii=False, indent=2), encoding="utf-8")


# ---------------- 核心：取增量并发送 ----------------
def deliver_new(products=None, chat_ids=None, dry_run=False, bootstrap=False, verbose=True, delivery_label=""):
    products = products or WATCH
    chat_ids = chat_ids or None
    headers = load_headers()
    state = load_state()
    total_sent = 0
    for bid, name in products.items():
        try:
            items = fetch_append(bid, headers)
        except Exception as e:
            if verbose:
                print("[%s] 拉取失败: %s" % (name, e), flush=True)
            continue
        seen = set(state["seen"].get(bid, []))
        new = [it for it in items if str(it.get("appendContentId")) not in seen]
        new.reverse()   # 旧→新 顺序发
        if bootstrap:
            state["seen"][bid] = state["seen"].get(bid, []) + [str(it.get("appendContentId")) for it in items]
            if verbose:
                print("[%s] bootstrap: 标记 %d 条为已读，不发送" % (name, len(items)), flush=True)
            continue
        for it in new:
            aid = str(it.get("appendContentId"))
            title, text = format_item(name, it, delivery_label=delivery_label)
            if dry_run:
                if verbose:
                    print("── DRY [%s] %s\n%s\n" % (name, title, text[:400]), flush=True)
            else:
                for cid in chat_ids_for_product(bid, override=chat_ids):
                    send_feishu(cid, title, text, aid)
                total_sent += 1
                if verbose:
                    print("✅ 已发飞书 [%s] %s (%s)" % (name, title, aid), flush=True)
            state["seen"].setdefault(bid, []).append(aid)
    if not dry_run:
        save_state(state)
    return total_sent


def trigger_from_push(username, article_url, verbose=False):
    """供 wechat-biz-relay 调用：看到爱投顾内参这条聊天消息就触发。非致命。"""
    try:
        if username != GH or "internalReference" not in (article_url or ""):
            return 0
        m = re.search(r"productId=(\d+)", article_url or "")
        pid = m.group(1) if m else None
        if not pid or pid not in WATCH:
            return 0            # 只处理目标内参的推送，其它内参卡片忽略
        return deliver_new(products={pid: WATCH[pid]}, verbose=verbose, delivery_label="database")
    except Exception as e:
        if verbose:
            print("itougu trigger 失败(忽略): %s" % e, flush=True)
        return 0


def ensure_baseline(products):
    """首次运行(state 文件不存在)时静默建立去重基线：标记当前所有内参为已读、不发历史。"""
    if STATE_FILE.exists():
        return
    try:
        deliver_new(products=products, bootstrap=True, verbose=True)
        print("首次运行：已建立去重基线（历史不发送）", flush=True)
    except Exception as e:
        print("建立基线失败(忽略，下轮再试): %s" % e, flush=True)


def in_trading_hours():
    now = datetime.now(CST)
    if now.weekday() >= 5:          # 周末
        return False
    hm = now.hour * 60 + now.minute
    return (9 * 60 + 30 <= hm <= 11 * 60 + 30) or (13 * 60 <= hm <= 15 * 60)  # A股上午/下午


def main():
    ap = argparse.ArgumentParser(description="爱投顾内参 → 飞书 中继")
    ap.add_argument("--once", action="store_true", help="拉一次增量并发送")
    ap.add_argument("--loop", action="store_true", help="循环轮询（兵底）")
    ap.add_argument("--interval", type=float, default=90, help="轮询间隔秒（默认90）")
    ap.add_argument("--dry-run", action="store_true", help="只打印不发送")
    ap.add_argument("--bootstrap", action="store_true", help="把当前所有内参标记为已读（不发送），首次部署用")
    ap.add_argument("--trading-hours-only", action="store_true", help="仅交易时段轮询")
    ap.add_argument("--product", action="append", help="只处理指定 productId")
    args = ap.parse_args()

    prods = {p: WATCH.get(p, "内参") for p in args.product} if args.product else WATCH

    if args.bootstrap:
        deliver_new(products=prods, bootstrap=True)
        return 0
    if args.loop:
        print("itougu 内参兵底轮询启动 interval=%ss" % args.interval, flush=True)
        ensure_baseline(prods)
        while True:
            if not args.trading_hours_only or in_trading_hours():
                try:
                    deliver_new(products=prods, dry_run=args.dry_run,
                                delivery_label=os.environ.get("ITOUGU_DELIVERY_LABEL", "轮询"))
                except Exception as e:
                    print("轮询异常(忽略): %s" % e, flush=True)
            time.sleep(args.interval)
    else:
        if args.trading_hours_only and not in_trading_hours():
            return 0   # 非交易时段静默跳过（避免定时任务日志刷屏）
        n = deliver_new(products=prods, dry_run=args.dry_run,
                        delivery_label=os.environ.get("ITOUGU_DELIVERY_LABEL", "轮询"))
        if n:
            print("完成，发送 %d 条" % n, flush=True)
    return 0


if __name__ == "__main__":
    sys.exit(main())
