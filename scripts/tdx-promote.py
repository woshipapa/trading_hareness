#!/usr/bin/env python3
"""Promote a TDX binding on recorded evidence: check observes it, apply moves its status one step.

    tdx-promote.py check <source> <capability> [--params '<json>'] --egress mac|owner
    tdx-promote.py apply <evidence.json>...

``check`` runs ``python -m app.datasources probe`` for the binding, on this machine (``mac``) or inside the owner
quant-research container over ssh (``owner``: a read-only ``docker exec`` reached with the LONGHU_SSH_* settings). The
exec runs the code baked into the image: a code-only overlay (QUANT_HOTFIX_ENABLED) is on the service's PYTHONPATH, not
on the exec's, so the image itself needs the probe command and the adapters. When the binding's BindingSpec declares
agreement tolerances, ``check`` reads every row of the binding and of each reference the same way (a reference whose
entry names a ``reference_adapter`` is read through ``probe --adapter``, with the parameters ``reference_params`` and
``reference_fixed`` give it; a reader whose rows are not canonical gets the projection registered for it in PROJECTIONS),
projects both sides as the resolver projects them (field_map, unit_factors), joins them on the entry's key and compares
each field on every common row. It writes
scripts/data/tdx_promote_<capability>_<source>_<date>_<egress>_<hash of the params>.json (date in Asia/Shanghai) with
every input, the probes (their first rows and a hash of all printed rows), the comparison (row counts, the largest
deviations, examples) and one verdict per gate, and exits 0 whatever the verdicts:

    owner_egress  the probe ran inside the owner container and answered with rows
    agreement     every declared tolerance holds (nothing to hold when none is declared)
    intraday      every probe started and finished inside a session of the XSHG calendar of exchange_calendars (the
                  Mac's, which knows the holidays and the lunch break; the evidence names the calendar and its version)

``apply`` takes the evidence files of one binding and moves the status token of that binding in catalog.py (never
``decision_eligible``) one step when every file passed the gates of the step: UNSUPPORTED -> DECLARED needs owner_egress and
agreement, DECLARED -> LIVE_VERIFIED also needs intraday. It prints the ``git commit -F - -- <catalog>`` command whose
message cites the files and their hashes and commits nothing: commit the evidence first, the status change on its own.
"""

from __future__ import annotations

import argparse
import ast
import hashlib
import json
import os
import shlex
import subprocess
import sys
from collections.abc import Callable, Mapping
from datetime import datetime, timezone
from pathlib import Path
from typing import Any
from zoneinfo import ZoneInfo

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "quant-service"))
from app.datasources.catalog import bindings_for  # noqa: E402
from app.datasources.contracts import BINDING_STATES, Binding  # noqa: E402
from app.datasources.resolver import _normalise_rows  # noqa: E402

SCHEMA = "tdx-promote-v2"
DATA_DIR = ROOT / "scripts" / "data"
CATALOG = ROOT / "quant-service" / "app" / "datasources" / "catalog.py"
CONTAINER = "trading-hareness-peer-quant-research-1"
OWNER_SETTINGS = ("LONGHU_SSH_HOST", "LONGHU_SSH_PORT", "LONGHU_SSH_USER", "LONGHU_SSH_KEY_PATH")
#: Rootless Docker on the owner, as in release-sync-status.sh; the script goes to ``bash -s`` on stdin.
OWNER_DOCKER = ('export XDG_RUNTIME_DIR="${XDG_RUNTIME_DIR:-/run/user/$(id -u)}"\n'
                'export DOCKER_HOST="${DOCKER_HOST:-unix://$XDG_RUNTIME_DIR/docker.sock}"\n')
CN = ZoneInfo("Asia/Shanghai")
#: Status token in catalog.py -> (the token after the step, the gates every evidence file must have passed).
STEPS = {"UNSUPPORTED": ("DECLARED", ("owner_egress", "agreement")),
         "DECLARED": ("LIVE_VERIFIED", ("owner_egress", "agreement", "intraday"))}
#: Rows of a probe kept in the evidence file; the printed rows as a whole are represented by their hash.
EVIDENCE_ROWS = 5
#: Mismatched rows listed per field in the evidence file.
EXAMPLES = 5
#: The share of the binding's rows that must have a counterpart in the reference when the entry does not say.
MIN_COVERAGE = 0.95


def redacted(text: str) -> str:
    for name, label in (("LONGHU_SSH_KEY_PATH", "[ssh-key]"), ("LONGHU_SSH_HOST", "[ssh-host]"), ("LONGHU_SSH_USER", "[ssh-user]")):
        value = os.environ.get(name)
        if value:
            text = text.replace(os.path.expanduser(value), label).replace(value, label)
    return text


def ssh_argv() -> list[str]:
    env = os.environ
    return ["ssh", "-o", "BatchMode=yes", "-o", "IdentitiesOnly=yes", "-o", "StrictHostKeyChecking=yes",
            "-o", "ConnectTimeout=20", "-i", env["LONGHU_SSH_KEY_PATH"], "-p", env["LONGHU_SSH_PORT"],
            f"{env['LONGHU_SSH_USER']}@{env['LONGHU_SSH_HOST']}", "bash", "-s"]


def run_probe(egress: str, source: str, capability: str, params: Mapping[str, Any], *, adapter: str | None = None,
              all_rows: bool = False) -> dict[str, Any]:
    """The probe record of one binding (or of ``adapter``, a reference's reader), taken on ``egress``."""
    probe = ["python", "-m", "app.datasources", "probe", source, capability, "--params", json.dumps(params)]
    if adapter:
        probe += ["--adapter", adapter]
    if all_rows:
        probe.append("--all-rows")
    if egress == "mac":
        command, argv, cwd, script = shlex.join(probe), [sys.executable, *probe[1:]], ROOT / "quant-service", None
    else:
        command = shlex.join(["docker", "exec", CONTAINER, *probe])
        argv, cwd, script = ssh_argv(), None, OWNER_DOCKER + command + "\n"
    done = subprocess.run(argv, cwd=cwd, input=script, capture_output=True, encoding="utf-8")
    if not done.stdout.strip():
        sys.exit(f"{source} {capability}: the probe printed nothing on the {egress} egress (exit {done.returncode}): "
                 + redacted(done.stderr)[-500:])
    return {"command": command, **json.loads(done.stdout)}


def lookup(source: str, capability: str) -> Binding:
    found = [item for item in bindings_for(capability, states=BINDING_STATES) if item.source == source]
    if not found:
        sys.exit(f"no binding {source} -> {capability} in the catalog")
    return found[0]


def xshg() -> tuple[Any, str]:
    """The Shanghai exchange's calendar and the name that goes into the evidence; only the Mac has the library."""
    import exchange_calendars  # noqa: PLC0415 - not installed where this script is only imported (CI)
    return exchange_calendars.get_calendar("XSHG"), f"exchange_calendars {exchange_calendars.__version__} XSHG"


def probed_in_session(calendar: Any, record: Mapping[str, Any]) -> bool:
    return all(calendar.is_open_on_minute(datetime.fromisoformat(record[key])) for key in ("started_utc", "finished_utc"))


def iso_date(compact: str) -> str:
    return f"{compact[:4]}-{compact[4:6]}-{compact[6:]}"


def tencent_minutes(record: Mapping[str, Any]) -> list[dict[str, Any]]:
    """tencent_intraday_minutes: today's finished minutes as bars.minute rows (the tape carries no date)."""
    day = datetime.fromisoformat(record["finished_utc"]).astimezone(CN).date().isoformat()
    return [{"symbol": row["ts_code"], "bar_time": f"{day}T{row['time'][:2]}:{row['time'][2:]}:00+08:00",
             "close": row["close"], "volume": row["volume_lot"] * 100, "amount": row["amount"]}
            for row in record["sample"] if row["is_complete"]]


def tencent_quotes(record: Mapping[str, Any]) -> list[dict[str, Any]]:
    """tencent_order_book_quotes: the day's cumulative volume (lots) and amount as quote.watch_snapshot rows."""
    return [{"symbol": row["ts_code"], "price": row["price"], "volume": row["cumulative_volume_lot"] * 100,
             "amount": row["cumulative_amount"]} for row in record["sample"]]


def tencent_index(record: Mapping[str, Any]) -> list[dict[str, Any]]:
    """tencent_index_daily: bars.index_daily rows; its volume unit is not confirmed, so only the prices are kept."""
    return [{"symbol": row["ts_code"], "trade_date": iso_date(row["trade_date"]), "open": float(row["open"]),
             "close": float(row["close"])} for row in record["sample"]]


def tencent_limits(record: Mapping[str, Any]) -> list[dict[str, Any]]:
    """tencent_quotes_blocking answers (rows, health); the exchange's limit prices are quote fields 47 and 48."""
    rows, _health = record["sample"]
    return [{"symbol": row["ts_code"], "trade_date": iso_date(row["trade_date"]), "up_limit": row["up_limit"],
             "down_limit": row["down_limit"]} for row in rows]


#: (reader, capability) -> rows in the capability's canonical fields, for a reference reader whose own rows are not.
PROJECTIONS = {
    ("app/free_market_providers.py:tencent_intraday_minutes", "bars.minute"): tencent_minutes,
    ("app/free_market_providers.py:tencent_order_book_quotes", "quote.watch_snapshot"): tencent_quotes,
    ("app/free_market_providers.py:tencent_index_daily", "bars.index_daily"): tencent_index,
    ("app/longhu_vendor_source.py:tencent_quotes_blocking", "limits.prices"): tencent_limits,
}


def reference_kwargs(entry: Mapping[str, Any], params: Mapping[str, Any]) -> dict[str, Any]:
    """What the entry's reference is asked: the check's parameters, or those ``reference_params`` renames, and the fixed ones."""
    named = entry.get("reference_params")
    asked = dict(params) if named is None else {keyword: params[name] for keyword, name in named.items()}
    return {**asked, **entry.get("reference_fixed", {})}


def reference_reads(agreement: Mapping[str, Mapping[str, Any]], params: Mapping[str, Any]) -> dict[str, tuple[str, str | None, dict]]:
    """Label -> (reference source, reader, keyword arguments) for every distinct read the entries need."""
    reads: dict[str, tuple[str, str | None, dict]] = {}
    for entry in agreement.values():
        adapter = entry.get("reference_adapter")
        wanted = (entry["reference"], adapter, reference_kwargs(entry, params))
        label = adapter or entry["reference"]
        if reads.setdefault(label, wanted) != wanted:
            sys.exit(f"the agreement entries read {label} with different parameters")
    return reads


def reading(binding: Binding, record: Mapping[str, Any],
            projection: Callable[[Mapping[str, Any]], list[dict[str, Any]]] | None = None) -> tuple[list[dict[str, Any]], str]:
    """The printed rows as the resolver (or the reader's projection) gives them, and why they cannot be compared."""
    label, rows = record["source"], record.get("sample")
    if record["error"]:
        return [], f"{label} probe failed: {record['error']}"
    if len(rows) != record["rows"]:
        return [], f"{label} printed {len(rows)} of its {record['rows']} rows"
    if projection:
        return projection(record), ""
    if not all(isinstance(row, dict) for row in rows):
        return [], f"{label} printed rows that are not mappings"
    projected = _normalise_rows(rows, binding)
    if projected.status:
        return [], f"{label} rows are {projected.status}: {', '.join(projected.warnings)}"
    return projected.rows, ""


def keyed(rows: list[dict[str, Any]], key: list[str], label: str) -> tuple[dict[tuple, dict[str, Any]], str]:
    """The rows by key, and why they cannot be joined (empty when they can)."""
    if any(name not in row for row in rows for name in key):
        return {}, f"{label} rows lack the key fields {key}"
    index = {tuple(row[name] for name in key): row for row in rows}
    if len(index) != len(rows):
        return {}, f"{label} has {len(rows) - len(index)} rows with a repeated key {key}"
    return index, ""


def gaps(left: Any, right: Any) -> tuple[float, float] | None:
    """The absolute and the relative difference of two numbers; None when either is not a number."""
    if not all(isinstance(value, (int, float)) and not isinstance(value, bool) for value in (left, right)):
        return None
    absolute = abs(left - right)
    scale = max(abs(left), abs(right))
    return absolute, absolute / scale if scale else 0.0


def agrees(left: Any, right: Any, rel_tol: float, abs_tol: float) -> bool:
    gap = gaps(left, right)
    if gap is None:
        return left is not None and left == right
    return gap[0] <= abs_tol or gap[1] <= rel_tol


def compare_field(name: str, entry: Mapping[str, Any], left: Mapping[tuple, dict[str, Any]],
                  right: Mapping[tuple, dict[str, Any]], unusable: str) -> dict[str, Any]:
    """One field over the rows both sides have; ``why`` is empty when the field agrees."""
    rel_tol, abs_tol = entry.get("rel_tol", 0.0), entry.get("abs_tol", 0.0)
    min_coverage = entry.get("min_coverage", MIN_COVERAGE)
    summary = {"reference": entry["reference"], "key": list(entry["key"]), "rel_tol": rel_tol, "abs_tol": abs_tol,
               "min_coverage": min_coverage}
    if unusable:
        return {**summary, "agree": False, "why": unusable}
    common = [key for key in left if key in right]
    pairs = [(key, left[key].get(name), right[key].get(name)) for key in common]
    wrong = [pair for pair in pairs if not agrees(pair[1], pair[2], rel_tol, abs_tol)]
    measured = [(gap, key) for key, one, other in pairs if (gap := gaps(one, other)) is not None]
    widest = max(measured, key=lambda item: item[0][0], default=None)
    coverage = len(common) / len(left) if left else 0.0
    reasons = []
    if not left:
        reasons.append("the binding returned no rows")
    elif coverage < min_coverage:
        reasons.append(f"the common rows are {coverage:.3f} of the binding's {len(left)}, below {min_coverage}")
    if wrong:
        reasons.append(f"{len(wrong)} of {len(common)} common rows are outside the tolerance")
    why = "; ".join(reasons)
    return {**summary, "left_rows": len(left), "right_rows": len(right), "matched": len(common) - len(wrong),
            "mismatched": len(wrong), "only_left": len(left) - len(common), "only_right": len(right) - len(common),
            "coverage": coverage, "max_abs_dev": widest[0][0] if widest else None,
            "max_rel_dev": max((gap[1] for gap, _ in measured), default=None),
            "worst_key": list(widest[1]) if widest else None,
            "examples": [{"key": list(key), "left": one, "right": other} for key, one, other in wrong[:EXAMPLES]],
            "agree": not why, "why": why}


def compare(source: str, agreement: Mapping[str, Mapping[str, Any]], bindings: Mapping[str, Binding],
            probes: Mapping[str, Mapping[str, Any]]) -> dict[str, Any]:
    """Each agreement field of ``source`` against the reference that field names, joined on the entry's key."""
    mine, mine_why = reading(bindings[source], probes[source])
    result = {}
    for name, entry in agreement.items():
        reference, adapter = entry["reference"], entry.get("reference_adapter")
        projection = PROJECTIONS.get((adapter, probes[source]["capability"]))
        theirs, their_why = reading(bindings[reference], probes[adapter or reference], projection)
        left, left_why = keyed(mine, entry["key"], source)
        right, right_why = keyed(theirs, entry["key"], reference)
        result[name] = compare_field(name, entry, left, right, mine_why or their_why or left_why or right_why)
    return result


def compact(record: Mapping[str, Any]) -> dict[str, Any]:
    """A probe record for the evidence file: its first rows, and the hash of all the rows it printed."""
    if record["error"]:
        return dict(record)
    digest = hashlib.sha256(json.dumps(record["sample"], ensure_ascii=False, sort_keys=True).encode("utf-8")).hexdigest()
    return {**record, "sample": record["sample"][:EVIDENCE_ROWS], "sample_sha256": digest}


def verdicts(egress: str, source: str, probes: Mapping[str, Mapping[str, Any]], comparison: Mapping[str, Any],
             inside: Mapping[str, bool], calendar: str) -> dict[str, dict[str, Any]]:
    own = probes[source]
    disagreeing = [f"{name}: {item['why']}" for name, item in comparison.items() if not item["agree"]]
    outside = [name for name, in_it in inside.items() if not in_it]
    return {
        "owner_egress": {
            "pass": egress == "owner" and not own["error"] and own["rows"] > 0,
            "detail": f"egress {egress}, " + (f"failed: {own['error']}" if own["error"] else f"{own['rows']} rows")},
        "agreement": {
            "pass": not disagreeing,
            "detail": "; ".join(disagreeing) or (f"{len(comparison)} fields agree on their common rows" if comparison
                                                 else "not applicable: the binding declares no agreement tolerances")},
        "intraday": {
            "pass": not outside,
            "detail": (f"outside a session: {', '.join(outside)}" if outside else "every probe ran inside a session")
            + f" ({calendar})"},
    }


def run_check(args: argparse.Namespace) -> int:
    missing = [name for name in OWNER_SETTINGS if not os.environ.get(name)]
    if args.egress == "owner" and missing:
        sys.exit("the owner egress needs these settings (names only): " + ", ".join(missing))
    bindings = {args.source: lookup(args.source, args.capability)}
    spec = bindings[args.source].spec
    agreement = spec.agreement if spec else {}
    reads = reference_reads(agreement, args.params)
    for reference, _adapter, _kwargs in reads.values():
        bindings[reference] = lookup(reference, args.capability)
    probes = {args.source: run_probe(args.egress, args.source, args.capability, args.params, all_rows=bool(agreement))}
    for label, (reference, adapter, kwargs) in reads.items():
        probes[label] = run_probe(args.egress, reference, args.capability, kwargs, adapter=adapter, all_rows=True)
    comparison = compare(args.source, agreement, bindings, probes)
    calendar, calendar_name = xshg()
    inside = {label: probed_in_session(calendar, record) for label, record in probes.items()}
    evidence = {
        "schema": SCHEMA, "source": args.source, "capability": args.capability, "params": args.params,
        "egress": args.egress, "catalog_status": bindings[args.source].status,
        "decision_eligible": bindings[args.source].decision_eligible,
        "checked_utc": datetime.now(timezone.utc).isoformat(), "agreement_spec": agreement,
        "projections": {source: {"field_map": binding.spec.field_map, "unit_factors": binding.spec.unit_factors}
                        for source, binding in bindings.items() if binding.spec},
        "probes": {source: compact(record) for source, record in probes.items()}, "comparison": comparison,
        "session": {"calendar": calendar_name, "inside": inside},
        "verdicts": verdicts(args.egress, args.source, probes, comparison, inside, calendar_name),
    }
    day = datetime.fromisoformat(probes[args.source]["finished_utc"]).astimezone(CN).date()
    digest = hashlib.sha256(json.dumps(args.params, sort_keys=True).encode("utf-8")).hexdigest()[:8]
    path = DATA_DIR / f"tdx_promote_{args.capability}_{args.source}_{day.isoformat()}_{args.egress}_{digest}.json"
    path.write_text(json.dumps(evidence, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    print(f"{args.source} {args.capability} on the {args.egress} egress -> {path.relative_to(ROOT)}")
    for gate, verdict in evidence["verdicts"].items():
        print(f"{gate}: {'pass' if verdict['pass'] else 'FAIL'} ({verdict['detail']})")
    return 0


def bind_call(text: str, source: str, capability: str) -> ast.Call:
    calls = [node for node in ast.walk(ast.parse(text))
             if isinstance(node, ast.Call) and isinstance(node.func, ast.Name) and node.func.id == "_bind"
             and [getattr(arg, "value", None) for arg in node.args[:2]] == [source, capability]]
    if len(calls) != 1:
        sys.exit(f"{source} {capability}: catalog.py has {len(calls)} _bind calls naming it literally, need exactly one")
    return calls[0]


def replace_token(text: str, token: ast.expr, new: str) -> str:
    """``text`` with the one-line expression ``token`` replaced; the column offsets of ``ast`` count UTF-8 bytes."""
    lines = text.split("\n")
    line = lines[token.lineno - 1].encode("utf-8")
    lines[token.lineno - 1] = (line[:token.col_offset] + new.encode("utf-8") + line[token.end_col_offset:]).decode("utf-8")
    return "\n".join(lines)


def run_apply(args: argparse.Namespace) -> int:
    paths = [path.resolve().relative_to(ROOT) for path in args.evidence]
    raws = [(ROOT / path).read_bytes() for path in paths]
    documents = [json.loads(raw) for raw in raws]
    for path, document in zip(paths, documents):
        if document.get("schema") != SCHEMA:
            sys.exit(f"{path}: not a {SCHEMA} evidence file")
    bindings = {(document["source"], document["capability"]) for document in documents}
    if len(bindings) != 1:
        sys.exit("the evidence files are about different bindings: " + ", ".join(f"{s} {c}" for s, c in sorted(bindings)))
    [(source, capability)] = bindings
    text = CATALOG.read_text(encoding="utf-8")
    token = bind_call(text, source, capability).args[3]
    if token.id not in STEPS:
        sys.exit(f"{source} {capability} is {token.id}: only UNSUPPORTED and DECLARED bindings are promoted")
    new, gates = STEPS[token.id]
    failed = [f"{path}: {gate} failed ({document['verdicts'][gate]['detail']})"
              for path, document in zip(paths, documents) for gate in gates if not document["verdicts"][gate]["pass"]]
    if failed:
        sys.exit(f"{source} {capability} stays {token.id}:\n" + "\n".join(failed))
    CATALOG.write_text(replace_token(text, token, new), encoding="utf-8")
    message = [f"Promote {source} {capability} from {token.id} to {new}", "",
               f"Every evidence file below passed {', '.join(gates)}. decision_eligible is not touched.", ""]
    for path, raw, document in zip(paths, raws, documents):
        message += [f"- {path} (sha256 {hashlib.sha256(raw).hexdigest()})",
                    *(f"  {gate}: {document['verdicts'][gate]['detail']}" for gate in gates)]
    print(f"git commit -F - -- {CATALOG.relative_to(ROOT)} <<'EOF'\n" + "\n".join(message) + "\nEOF")
    return 0


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    commands = parser.add_subparsers(dest="command", required=True)
    check = commands.add_parser("check", help="probe a binding and record the evidence")
    check.add_argument("source")
    check.add_argument("capability")
    check.add_argument("--params", type=json.loads, default={}, help="JSON object: the adapter's keyword arguments")
    check.add_argument("--egress", choices=("mac", "owner"), required=True)
    apply = commands.add_parser("apply", help="move the binding's status one step on evidence files")
    apply.add_argument("evidence", nargs="+", type=Path)
    args = parser.parse_args(argv)
    return run_check(args) if args.command == "check" else run_apply(args)


if __name__ == "__main__":
    raise SystemExit(main())
