#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""爱投顾专表监听（低延迟版）。

只盯 biz_message_0.db 里爱投顾一张表 Msg_<md5(gh_6569bb074cc9)>，
用"解密 base 一次 + 每秒只应用小 WAL 增量"的方式做到 ~几秒延迟，
比 wechat-biz-relay 整库重建(~30s)快一个量级，且 CPU 几乎空转。

看到新卡片 → 解析内参 URL → 调 itougu_neican_relay.trigger_from_push 拉正文发飞书
（去重由 itougu_neican_relay 的 state + 飞书 uuid 负责；与 biz-relay hook / 兵底轮询叠加不会重复发）。

统一 venv：由 svc_supervisor 用 ~/.venvs/svc/bin/python 启动。
"""
import argparse
import hashlib
import os
import re
import sys
import tempfile
import time
from pathlib import Path

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from wechat_sqlcipher import decrypt_base, apply_wal, load_key, fingerprint, zstd_decode
import itougu_neican_relay as NEICAN

GH = "gh_6569bb074cc9"
TABLE = "Msg_" + hashlib.md5(GH.encode()).hexdigest()
DB_ROOT = Path.home() / "Library/Containers/com.tencent.xinWeChat/Data/Documents/xwechat_files/wxid_s91kbusi1ieu12_7178/db_storage"
EXPORT_ROOT = Path("/Users/papa/codebase/wechat-export-macos")
STATE_DIR = Path("/Users/papa/codebase/n8n/state")
DB = DB_ROOT / "message/biz_message_0.db"
WAL = DB_ROOT / "message/biz_message_0.db-wal"
SNAP = STATE_DIR / "itougu-table.snapshot.db"
import sqlite3

_url_re = re.compile(r"<url><!\[CDATA\[(.*?)\]\]></url>", re.S)


def rebuild_base():
    """全量解密 base（贵，~3.7s），只在 base 变化时调用。返回 salt。"""
    fd, tmp = tempfile.mkstemp(prefix="itougu-base-", suffix=".db", dir=STATE_DIR)
    os.close(fd)
    tmp = Path(tmp)
    key = load_key(EXPORT_ROOT, "message/biz_message_0.db")
    salt = decrypt_base(DB, tmp, key)
    os.replace(tmp, SNAP)
    return key, salt


def apply_wal_inplace(key, salt):
    """把当前 WAL 增量应用到 SNAP（便宜，~0.01s）。"""
    try:
        apply_wal(DB, WAL, SNAP, key, salt)
    except Exception as e:
        print("apply_wal 跳过(%s)" % e, flush=True)


def read_new(cursor):
    """读爱投顾表 local_id>cursor 的新卡片，返回 [(local_id, url)]。"""
    con = sqlite3.connect(f"file:{SNAP}?mode=ro&immutable=1", uri=True, timeout=10)
    con.text_factory = bytes
    out = []
    try:
        exists = con.execute("SELECT name FROM sqlite_master WHERE type='table' AND name=?", (TABLE,)).fetchone()
        if not exists:
            return out, cursor
        rows = con.execute(
            f'SELECT local_id, message_content FROM "{TABLE}" WHERE local_id>? ORDER BY local_id',
            (cursor,)).fetchall()
        mx = cursor
        for lid, content in rows:
            mx = max(mx, int(lid))
            try:
                raw = zstd_decode(content)
            except Exception:
                raw = content.decode("utf-8", "ignore") if isinstance(content, bytes) else str(content)
            m = _url_re.search(raw if isinstance(raw, str) else raw.decode("utf-8", "ignore"))
            out.append((int(lid), m.group(1) if m else ""))
        return out, mx
    finally:
        con.close()


def max_local_id():
    con = sqlite3.connect(f"file:{SNAP}?mode=ro&immutable=1", uri=True, timeout=10)
    con.text_factory = bytes
    try:
        r = con.execute(f'SELECT MAX(local_id) FROM "{TABLE}"').fetchone()
        return int(r[0]) if r and r[0] is not None else 0
    except Exception:
        return 0
    finally:
        con.close()


def main():
    ap = argparse.ArgumentParser(description="爱投顾专表低延迟监听")
    ap.add_argument("--interval", type=float, default=1.5, help="轮询间隔秒（默认1.5）")
    ap.add_argument("--forward-existing", action="store_true", help="首启也处理存量（默认只建游标）")
    ap.add_argument("--once", action="store_true", help="只跑一轮（测试用）")
    args = ap.parse_args()

    STATE_DIR.mkdir(parents=True, exist_ok=True)
    print(f"爱投顾专表监听启动：表={TABLE} interval={args.interval}s", flush=True)
    key, salt = rebuild_base()
    apply_wal_inplace(key, salt)
    base_fp = fingerprint([DB])
    wal_fp = fingerprint([WAL])

    cursor = 0 if args.forward_existing else max_local_id()
    print(f"初始游标 local_id={cursor}", flush=True)

    while True:
        try:
            cur_base = fingerprint([DB])
            if cur_base != base_fp:                 # checkpoint：base 变了，重解
                key, salt = rebuild_base()
                base_fp = cur_base
                wal_fp = None                        # 强制重应用 WAL
            cur_wal = fingerprint([WAL])
            if cur_wal != wal_fp:                    # WAL 变了：应用增量 + 读新卡片
                apply_wal_inplace(key, salt)
                wal_fp = cur_wal
                new, cursor = read_new(cursor)
                for lid, url in new:
                    tag = " [内参]" if "internalReference" in url else ""
                    print(f"新卡片 local_id={lid}{tag} url={url[:80]}", flush=True)
                    if "internalReference" in url:
                        n = NEICAN.trigger_from_push(GH, url, verbose=True)
                        if n:
                            print(f"  → 已发飞书 {n} 条", flush=True)
        except Exception as e:
            print(f"tick 异常(忽略): {type(e).__name__}: {e}", flush=True)
        if args.once:
            return 0
        time.sleep(args.interval)


if __name__ == "__main__":
    sys.exit(main())
