"""Import a teacher-review pack from inside the service container.

    docker exec -i <quant-research> python -m app.teacher_review_cli import - [--dry-run] < pack.json

The command posts to the co-located API with the container's own
``QUANT_WRITE_API_KEY``, so the operator workstation never holds the write key
and the import runs through exactly the same validation as the HTTP route.
"""

from __future__ import annotations

import argparse
import json
import os
import sys
import urllib.error
import urllib.request


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    sub = parser.add_subparsers(dest="command", required=True)
    importer = sub.add_parser("import")
    importer.add_argument("path", help="pack JSON file, or - for stdin")
    importer.add_argument("--dry-run", action="store_true")
    importer.add_argument("--api", default=os.getenv("TEACHER_REVIEW_API", "http://127.0.0.1:8000"))
    args = parser.parse_args(argv)
    raw = sys.stdin.read() if args.path == "-" else open(args.path, encoding="utf-8").read()
    body = json.dumps({"pack": json.loads(raw), "dry_run": bool(args.dry_run)}, ensure_ascii=False).encode()
    request = urllib.request.Request(
        f"{args.api.rstrip('/')}/api/v1/teacher-review/packs", data=body, method="POST",
        headers={"content-type": "application/json", "X-Quant-Write-Key": os.environ.get("QUANT_WRITE_API_KEY", "")},
    )
    try:
        with urllib.request.urlopen(request, timeout=600) as response:
            payload = json.load(response)
    except urllib.error.HTTPError as error:
        print(error.read().decode("utf-8", "replace"), file=sys.stderr)
        return 1
    if args.dry_run:
        payload.pop("plans", None)
    print(json.dumps(payload, ensure_ascii=False, indent=1, default=str))
    return 0 if payload.get("status") in {"imported", "duplicate", "dry_run"} else 1


if __name__ == "__main__":
    raise SystemExit(main())
