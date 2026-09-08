#!/usr/bin/env python3
"""Mirror the literature-map PDF library into Baidu Pan, structure preserved.

The papers live in iCloud as `papers/<Venue>/<Category>/NNN - Title.pdf` (the
repo keeps only symlinks). This copies that whole tree to
`/apps/股票paper存储/papers/...` on Baidu Pan, so the library is readable from
any device / shareable, in addition to iCloud.

Idempotent and resumable: before uploading a file, the destination directory's
listing is consulted (cached per directory), and a file already there with the
same byte size is skipped. So a re-run after an interruption only uploads what
is missing -- safe to run repeatedly, and safe to stop with Ctrl-C.

    python3 upload_papers_to_pan.py --dry-run              # count + first paths
    python3 upload_papers_to_pan.py --venue "MICRO 2025"   # one top-level folder
    python3 upload_papers_to_pan.py                        # everything

Uses ~/.config/feishu-relay/baidu-pan-token.json via pan_client (this machine
is authorized; the app 股票paper存储 has ~12 TB, ~1.4% used).
"""
from __future__ import annotations

import argparse
import os
import sys
import time
from pathlib import Path

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import pan_client as pc

SOURCE_ROOT = Path.home() / "Library/Mobile Documents/com~apple~CloudDocs/papers"
PAN_ROOT = "/apps/股票paper存储/papers"
DELAY_SECONDS = float(os.environ.get("PAN_UPLOAD_DELAY", "0.5"))
MAX_FILE_RETRIES = 3

_dir_cache: dict[str, dict[str, int]] = {}  # pan dir -> {filename: size}


def existing_in_dir(pan_dir: str) -> dict[str, int]:
    """Filenames already in a Pan directory, name -> size, listed once and cached."""
    if pan_dir in _dir_cache:
        return _dir_cache[pan_dir]
    listing: dict[str, int] = {}
    start = 0
    while True:
        try:
            r = pc._get("/rest/2.0/xpan/file",
                        {"method": "list", "dir": pan_dir, "limit": 1000, "start": start})
        except Exception:  # noqa: BLE001 - a missing dir just means nothing uploaded yet
            break
        items = r.get("list") or []
        for item in items:
            if not item.get("isdir"):
                listing[item["server_filename"]] = int(item.get("size", 0))
        if len(items) < 1000:
            break
        start += 1000
    _dir_cache[pan_dir] = listing
    return listing


def pan_path_for(local: Path) -> str:
    rel = local.relative_to(SOURCE_ROOT).as_posix()
    return f"{PAN_ROOT}/{rel}"


def iter_files(root: Path, pdf_only: bool):
    for path in sorted(root.rglob("*")):
        if not path.is_file() or path.name == ".DS_Store":
            continue
        if pdf_only and path.suffix.lower() != ".pdf":
            continue
        yield path


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--venue", help="only this top-level folder, e.g. 'MICRO 2025'")
    ap.add_argument("--root", type=Path, default=SOURCE_ROOT, help=argparse.SUPPRESS)
    ap.add_argument("--pdf-only", action="store_true", help="skip the index json/md files")
    ap.add_argument("--limit", type=int, default=0, help="stop after N uploads (0 = no limit)")
    ap.add_argument("--dry-run", action="store_true")
    args = ap.parse_args()

    root = args.root / args.venue if args.venue else args.root
    if not root.exists():
        print(f"source not found: {root}", file=sys.stderr)
        return 2

    files = list(iter_files(root, args.pdf_only))
    total_bytes = sum(f.stat().st_size for f in files)
    print(f"source: {root}")
    print(f"{len(files)} file(s), {total_bytes / 2**30:.2f} GiB -> {PAN_ROOT}/")

    uploaded = skipped = failed = 0
    uploaded_bytes = 0
    started = time.time()
    for index, local in enumerate(files, 1):
        pan_path = pan_path_for(local)
        pan_dir = os.path.dirname(pan_path)
        size = local.stat().st_size
        name = os.path.basename(pan_path)

        if existing_in_dir(pan_dir).get(name) == size:
            skipped += 1
            continue
        if args.dry_run:
            if uploaded < 8:
                print(f"  would upload: {pan_path}")
            uploaded += 1
            continue

        last_error: Exception | None = None
        for attempt in range(MAX_FILE_RETRIES):
            try:
                pc.upload(str(local), pan_path)
                _dir_cache.setdefault(pan_dir, {})[name] = size  # keep cache in sync for resume
                last_error = None
                break
            except Exception as exc:  # noqa: BLE001 - retried
                last_error = exc
                if attempt < MAX_FILE_RETRIES - 1:
                    time.sleep(2 ** attempt)
        if last_error is not None:
            failed += 1
            print(f"  ! FAILED {pan_path}: {str(last_error)[:140]}", file=sys.stderr)
        else:
            uploaded += 1
            uploaded_bytes += size
            if uploaded % 25 == 0 or size > 20 * 2**20:
                rate = uploaded_bytes / max(1e-9, time.time() - started) / 2**20
                print(f"  [{index}/{len(files)}] uploaded {uploaded}, skipped {skipped}, "
                      f"failed {failed}  ({rate:.1f} MiB/s)")
            time.sleep(DELAY_SECONDS)
        if args.limit and uploaded >= args.limit:
            print(f"  reached --limit {args.limit}")
            break

    verb = "would upload" if args.dry_run else "uploaded"
    print(f"\ndone: {verb} {uploaded}, skipped {skipped} (already present), failed {failed}")
    return 1 if failed else 0


if __name__ == "__main__":
    try:
        sys.exit(main())
    except KeyboardInterrupt:
        print("\ninterrupted -- re-run to resume (already-uploaded files are skipped)")
        sys.exit(130)
