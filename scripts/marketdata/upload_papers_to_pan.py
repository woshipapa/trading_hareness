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
import threading
import time
from concurrent.futures import ThreadPoolExecutor, as_completed
from pathlib import Path

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import pan_client as pc

SOURCE_ROOT = Path.home() / "Library/Mobile Documents/com~apple~CloudDocs/papers"
PAN_ROOT = "/apps/股票paper存储/papers"
WORKERS = int(os.environ.get("PAN_UPLOAD_WORKERS", "6"))
MAX_FILE_RETRIES = 3


def existing_in_dir(pan_dir: str) -> dict[str, int]:
    """Filenames already in a Pan directory, name -> size."""
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


def upload_one(local: Path, pan_path: str) -> None:
    last_error: Exception | None = None
    for attempt in range(MAX_FILE_RETRIES):
        try:
            pc.upload(str(local), pan_path)
            return
        except Exception as exc:  # noqa: BLE001 - retried
            last_error = exc
            if attempt < MAX_FILE_RETRIES - 1:
                time.sleep(2 ** attempt)
    raise last_error  # type: ignore[misc]


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--venue", help="only this top-level folder, e.g. 'MICRO 2025'")
    ap.add_argument("--root", type=Path, default=SOURCE_ROOT, help=argparse.SUPPRESS)
    ap.add_argument("--pdf-only", action="store_true", help="skip the index json/md files")
    ap.add_argument("--workers", type=int, default=WORKERS, help=f"concurrent uploads (default {WORKERS})")
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
    print(f"{len(files)} file(s), {total_bytes / 2**30:.2f} GiB -> {PAN_ROOT}/ ({args.workers} workers)")

    # Resolve which files are missing BEFORE uploading: list each destination
    # directory once (sequential, race-free), so the concurrent phase never
    # lists a directory it is also writing into.
    by_dir: dict[str, list[Path]] = {}
    for local in files:
        by_dir.setdefault(os.path.dirname(pan_path_for(local)), []).append(local)
    missing: list[tuple[Path, str]] = []
    skipped = 0
    for pan_dir in sorted(by_dir):
        present = existing_in_dir(pan_dir)
        for local in by_dir[pan_dir]:
            name = os.path.basename(pan_path_for(local))
            if present.get(name) == local.stat().st_size:
                skipped += 1
            else:
                missing.append((local, pan_path_for(local)))
    print(f"already present: {skipped}; to upload: {len(missing)}")
    if args.limit:
        missing = missing[: args.limit]
    if args.dry_run:
        for _, pan_path in missing[:8]:
            print(f"  would upload: {pan_path}")
        print(f"\ndone: would upload {len(missing)}, skipped {skipped}")
        return 0

    lock = threading.Lock()
    state = {"done": 0, "failed": 0, "bytes": 0}
    started = time.time()

    def work(item: tuple[Path, str]) -> None:
        local, pan_path = item
        try:
            upload_one(local, pan_path)
        except Exception as exc:  # noqa: BLE001
            with lock:
                state["failed"] += 1
            print(f"  ! FAILED {pan_path}: {str(exc)[:140]}", file=sys.stderr)
            return
        with lock:
            state["done"] += 1
            state["bytes"] += local.stat().st_size
            done = state["done"]
        if done % 50 == 0:
            rate = state["bytes"] / max(1e-9, time.time() - started) / 2**20
            print(f"  uploaded {done}/{len(missing)}, failed {state['failed']}  ({rate:.1f} MiB/s)")

    with ThreadPoolExecutor(max_workers=max(1, args.workers)) as pool:
        futures = [pool.submit(work, item) for item in missing]
        for _ in as_completed(futures):
            pass

    print(f"\ndone: uploaded {state['done']}, skipped {skipped} (already present), failed {state['failed']}")
    return 1 if state["failed"] else 0


if __name__ == "__main__":
    try:
        sys.exit(main())
    except KeyboardInterrupt:
        print("\ninterrupted -- re-run to resume (already-uploaded files are skipped)")
        sys.exit(130)
