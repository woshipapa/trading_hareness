#!/usr/bin/env python3
"""Bounded Itougu HLS video worker.

This is deliberately a separate, opt-in process.  The Feishu/Itougu relay only
forwards the public URL; this worker can archive a video and/or transcribe its
audio on a storage-capable owner host.  It never feeds transcript text into a
live strategy or downloads an arbitrary URL.
"""
from __future__ import annotations

import argparse
import json
import os
import re
import subprocess
import urllib.request
from pathlib import Path
from urllib.parse import urlparse

ALLOWED_HOSTS = frozenset({"voss.itougu.com"})
DEFAULT_MAX_BYTES = 480 * 1024 * 1024
DEFAULT_MAX_SECONDS = 3 * 60 * 60


def validate_url(value: str) -> str:
    url = str(value or "").strip()
    parsed = urlparse(url)
    if parsed.scheme != "https" or parsed.hostname not in ALLOWED_HOSTS or parsed.username or parsed.password:
        raise ValueError("video URL must be an https voss.itougu.com URL")
    return url


def playlist_info(url: str, timeout: int = 20) -> dict[str, object]:
    url = validate_url(url)
    request = urllib.request.Request(url, headers={"User-Agent": "trading-hareness-video-worker/1"})
    with urllib.request.urlopen(request, timeout=timeout) as response:
        body = response.read(2 * 1024 * 1024).decode("utf-8", "replace")
    if "#EXTM3U" not in body:
        raise ValueError("video URL did not return an HLS playlist")
    durations = [float(x) for x in re.findall(r"#EXTINF:([0-9]+(?:\.[0-9]+)?)", body)]
    return {"url": url, "segments": len(durations), "duration_seconds": round(sum(durations), 3)}


def extract_audio(url: str, output: Path, *, max_seconds: int, max_bytes: int) -> None:
    validate_url(url)
    output.parent.mkdir(parents=True, exist_ok=True)
    command = [
        os.environ.get("FFMPEG", "ffmpeg"), "-hide_banner", "-loglevel", "error",
        "-protocol_whitelist", "file,http,https,tcp,tls,crypto", "-i", url,
        "-t", str(max_seconds), "-vn", "-ac", "1", "-ar", "16000", "-c:a", "pcm_s16le",
        "-fs", str(max_bytes), "-f", "wav", str(output), "-y",
    ]
    subprocess.run(command, check=True, timeout=max_seconds + 120)
    if not output.is_file() or output.stat().st_size <= 44:
        raise RuntimeError("ffmpeg produced no audio")
    if output.stat().st_size > max_bytes:
        raise RuntimeError("audio output exceeded configured bound")


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("url")
    parser.add_argument("--probe", action="store_true")
    parser.add_argument("--audio-out", type=Path)
    parser.add_argument("--max-seconds", type=int, default=DEFAULT_MAX_SECONDS)
    parser.add_argument("--max-bytes", type=int, default=DEFAULT_MAX_BYTES)
    args = parser.parse_args()
    if args.max_seconds < 1 or args.max_seconds > DEFAULT_MAX_SECONDS:
        parser.error("--max-seconds must be between 1 and 10800")
    if args.max_bytes < 1024 or args.max_bytes > DEFAULT_MAX_BYTES:
        parser.error("--max-bytes is outside the safe bound")
    info = playlist_info(args.url)
    if args.probe or args.audio_out is None:
        print(json.dumps(info, ensure_ascii=False))
        return 0
    extract_audio(args.url, args.audio_out, max_seconds=args.max_seconds, max_bytes=args.max_bytes)
    print(json.dumps({**info, "audio_path": str(args.audio_out), "audio_bytes": args.audio_out.stat().st_size}, ensure_ascii=False))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
