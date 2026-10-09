#!/usr/bin/env python3
"""Read-only forensics over the bridge's raw WebSocket frame captures.

LARKX_RAW_CAPTURE_ALL_FRAMES keeps the frames the pipeline could not explain
(non-push commands, pushes that decoded to zero messages, undecodable frames).
This groups them by WebSocket command number and prints a generic protobuf
field tree for a sample of each, so a human can decide which command actually
carries messages before any decoder is written for it — evidence first, never
a guess.

Dependency-free generic protobuf walker: no larkx import, so it can run on the
edge against /var/lib/larkagentx/raw-capture or on a copied sample locally.
Run with -I. Prints structure only; it does not resolve or forward anything.
"""

from __future__ import annotations

import argparse
import json
import sys
from collections import Counter
from pathlib import Path

CHATLIKE_MIN_DIGITS = 17  # Feishu numeric chat/message ids are ~19 digits.


def read_varint(data: bytes, offset: int) -> tuple[int, int]:
    result = shift = 0
    while True:
        byte = data[offset]
        offset += 1
        result |= (byte & 0x7F) << shift
        shift += 7
        if not byte & 0x80:
            return result, offset


def walk(data: bytes, depth: int = 0, max_depth: int = 6) -> list | None:
    """Best-effort protobuf field walk; returns None if bytes are not protobuf."""
    out: list = []
    offset = 0
    try:
        while offset < len(data):
            tag, offset = read_varint(data, offset)
            field_number, wire = tag >> 3, tag & 0x07
            if field_number == 0 or field_number > 536870911:
                return None
            if wire == 0:
                value, offset = read_varint(data, offset)
                out.append((field_number, "varint", value))
            elif wire == 2:
                length, offset = read_varint(data, offset)
                chunk = data[offset:offset + length]
                if len(chunk) < length:
                    return None
                offset += length
                sub = walk(chunk, depth + 1, max_depth) if depth < max_depth and length > 1 else None
                if sub:
                    out.append((field_number, "msg", sub))
                else:
                    try:
                        text = chunk.decode("utf-8")
                        printable = text.isprintable() or all(c in "\n\t" for c in text if not c.isprintable())
                        out.append((field_number, "str" if (printable and text) else "bytes",
                                    text if printable and text else chunk[:32].hex()))
                    except UnicodeDecodeError:
                        out.append((field_number, "bytes", chunk[:32].hex()))
            elif wire == 5:
                out.append((field_number, "fix32", data[offset:offset + 4].hex())); offset += 4
            elif wire == 1:
                out.append((field_number, "fix64", data[offset:offset + 8].hex())); offset += 8
            else:
                return None
    except (IndexError, ValueError):
        return None
    return out


def chatlike(value: str) -> bool:
    return value.isdigit() and len(value) >= CHATLIKE_MIN_DIGITS


def render(nodes: list, indent: int = 1) -> list[str]:
    lines: list[str] = []
    for field_number, kind, value in nodes:
        if kind == "msg":
            lines.append("  " * indent + f"f{field_number}:")
            lines.extend(render(value, indent + 1))
        else:
            flag = "  <== id-like" if kind == "str" and chatlike(str(value)) else ""
            lines.append("  " * indent + f"f{field_number} {kind}: {str(value)[:90]}{flag}")
    return lines


def frame_payload(raw: bytes) -> tuple[int | None, bytes | None]:
    """Extract (cmd, packet.payload) from a raw frame via the generic walk."""
    frame = walk(raw) or []
    packet_bytes = next((v for fn, k, v in frame if fn == 8 and k in {"msg", "bytes"}), None)
    if packet_bytes is None:
        return None, None
    packet = walk(packet_bytes) if isinstance(packet_bytes, bytes) else packet_bytes
    if not isinstance(packet, list):
        return None, None
    cmd = next((v for fn, k, v in packet if fn == 3 and k == "varint"), None)
    payload = next((v for fn, k, v in packet if fn == 5 and k == "msg"), None)
    return cmd, payload


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--dir", default="/var/lib/larkagentx/raw-capture")
    parser.add_argument("--cmd", type=int, default=None, help="只解剖这个 cmd")
    parser.add_argument("--samples", type=int, default=1, help="每个 cmd 打印几个样本")
    args = parser.parse_args()

    directory = Path(args.dir).expanduser()
    metas = sorted(directory.glob("*.json"))
    if not metas:
        print(f"没有捕获文件: {directory}", file=sys.stderr)
        return 1

    by_cmd: dict[int, list[Path]] = {}
    counter: Counter = Counter()
    for meta_path in metas:
        try:
            meta = json.loads(meta_path.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError):
            continue
        cmd = meta.get("cmd")
        counter[cmd] += 1
        by_cmd.setdefault(cmd, []).append(meta_path.with_suffix(".bin"))

    print("=== cmd 分布（帧数）===")
    for cmd, count in counter.most_common():
        print(f"  cmd={cmd}: {count}")

    for cmd in sorted((c for c in by_cmd if c is not None), key=lambda c: -counter[c]):
        if args.cmd is not None and cmd != args.cmd:
            continue
        print(f"\n=== cmd={cmd} 结构样本 ===")
        for bin_path in by_cmd[cmd][: args.samples]:
            if not bin_path.exists():
                continue
            raw = bin_path.read_bytes()
            frame_cmd, payload = frame_payload(raw)
            print(f"--- {bin_path.name} (len={len(raw)})")
            if isinstance(payload, list):
                print("\n".join(render(payload)))
            else:
                tree = walk(raw)
                print("\n".join(render(tree)) if tree else "  (无法按 protobuf 解析)")
    return 0


if __name__ == "__main__":
    sys.exit(main())
