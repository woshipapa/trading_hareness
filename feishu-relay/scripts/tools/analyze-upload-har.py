#!/usr/bin/env python3
"""Summarise the image/file upload requests in a browser HAR, with secrets redacted.

Usage: analyze-upload-har.py capture.har [--all-hosts]

Export the HAR from DevTools (Network tab, "Preserve log", then the download
arrow) after attaching one small image in the Feishu web messenger composer.
Cookies, authorization and token-like headers are never printed; bodies are
shown only as sizes, hex heads and a raw protobuf field walk, so the output is
safe to paste into a chat or commit as protocol notes.
"""
from __future__ import annotations

import argparse
import base64
import json
import re
import sys
from urllib.parse import parse_qsl, urlsplit

HOST_RE = re.compile(r"(feishu|larksuite|feishucdn)", re.I)
PATH_RE = re.compile(r"(upload|pre_upload|/files|/part|/stream|/complete|file_meta|image|resource|/im/gateway|gateway)", re.I)
SENSITIVE = re.compile(r"(cookie|authorization|token|csrf|passport|session|ticket|secret|signature|x-ss-|x-tt-)", re.I)
KEEP_VALUES = {"content-type", "content-length", "content-encoding", "x-command", "x-command-version", "x-appid", "x-source",
               "x-fingerprint-type", "x-file-name", "x-file-size", "x-pre-upload", "x-compress-algorithm", "x-lgw-os-type",
               "x-lgw-terminal-type", "origin", "referer", "accept", "range", "content-range"}


def redact_headers(headers):
    out = {}
    for h in headers:
        name = h["name"].lower()
        if name.startswith(":"):
            continue
        out[name] = h["value"] if name in KEEP_VALUES and not SENSITIVE.search(name) else ("<redacted>" if SENSITIVE.search(name) else f"<{len(h['value'])} chars>")
    return out


def varint(buf, i):
    shift = value = 0
    while i < len(buf):
        b = buf[i]; i += 1
        value |= (b & 0x7F) << shift
        if not b & 0x80:
            return value, i
        shift += 7
    raise ValueError("truncated varint")


def walk_proto(buf, depth=0, limit=60):
    """Best-effort raw protobuf walk: (field, wiretype, preview) tuples, recursing into plausible sub-messages."""
    lines, i = [], 0
    pad = "  " * depth
    try:
        while i < len(buf) and len(lines) < limit:
            tag, i = varint(buf, i)
            field, wt = tag >> 3, tag & 7
            if field == 0:
                raise ValueError("field 0")
            if wt == 0:
                v, i = varint(buf, i); lines.append(f"{pad}{field}: varint {v}")
            elif wt == 1:
                lines.append(f"{pad}{field}: fixed64 {buf[i:i+8].hex()}"); i += 8
            elif wt == 5:
                lines.append(f"{pad}{field}: fixed32 {buf[i:i+4].hex()}"); i += 4
            elif wt == 2:
                n, i = varint(buf, i)
                chunk = buf[i:i + n]
                if len(chunk) != n:
                    raise ValueError("truncated")
                i += n
                sub = walk_proto(chunk, depth + 1, limit) if depth < 4 and n > 1 else None
                try:
                    text = chunk.decode("utf-8")
                    printable = text.isprintable() and n > 0
                except UnicodeDecodeError:
                    printable = False
                if printable:
                    lines.append(f"{pad}{field}: str {text[:80]!r}")
                elif sub:
                    lines.append(f"{pad}{field}: msg({n})"); lines.extend(sub)
                else:
                    lines.append(f"{pad}{field}: bytes({n}) {chunk[:16].hex()}")
            else:
                raise ValueError("wire type")
    except Exception:
        return lines or None
    return lines


def body_bytes(content):
    text = content.get("text")
    if text is None:
        return b""
    return base64.b64decode(text) if content.get("encoding") == "base64" else text.encode("utf-8", "replace")


def describe(label, mime, data):
    print(f"    {label}: {len(data)} bytes, {mime or '?'}; head={data[:24].hex()}")
    if "protobuf" in (mime or "") or (data and data[0] in (0x08, 0x0A, 0x10, 0x12)):
        tree = walk_proto(data)
        if tree:
            print("      protobuf walk:"); print("\n".join("        " + l for l in tree))
    elif data and all(32 <= b < 127 or b in (9, 10, 13) for b in data[:200]):
        print(f"      text: {data[:300].decode()!r}")


def main():
    ap = argparse.ArgumentParser(); ap.add_argument("har"); ap.add_argument("--all-hosts", action="store_true")
    args = ap.parse_args()
    entries = json.load(open(args.har, encoding="utf-8"))["log"]["entries"]
    shown = 0
    for e in entries:
        req, res = e["request"], e["response"]
        u = urlsplit(req["url"])
        if not args.all_hosts and not HOST_RE.search(u.netloc):
            continue
        if not PATH_RE.search(u.path) and "x-command" not in {h["name"].lower() for h in req["headers"]}:
            continue
        shown += 1
        query = {k: ("<set>" if SENSITIVE.search(k) else v[:40]) for k, v in parse_qsl(u.query)}
        print(f"[{shown}] {req['method']} {u.netloc}{u.path}  query={query}  -> {res['status']}")
        print(f"    req headers: {json.dumps(redact_headers(req['headers']), ensure_ascii=False)}")
        post = req.get("postData") or {}
        if post.get("params") and not post.get("text"):
            print(f"    multipart fields: {[(p['name'], p.get('fileName')) for p in post['params']]}")
        describe("req body", post.get("mimeType"), body_bytes(post)) if post.get("text") else None
        print(f"    res headers: {json.dumps(redact_headers(res['headers']), ensure_ascii=False)}")
        describe("res body", res["content"].get("mimeType"), body_bytes(res["content"]))
    if not shown:
        print("no matching requests; try --all-hosts", file=sys.stderr); return 1


if __name__ == "__main__":
    sys.exit(main())
