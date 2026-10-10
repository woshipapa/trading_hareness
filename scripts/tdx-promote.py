#!/usr/bin/env python3
"""Promote a TDX binding on recorded evidence: check observes it, apply moves its status one step.

    tdx-promote.py check <source> <capability> [--params '<json>'] --egress mac|owner
    tdx-promote.py apply <evidence.json>...

``check`` runs ``python -m app.datasources probe`` for the binding, on this machine (``mac``) or inside the owner
quant-research container over ssh (``owner``: a read-only ``docker exec`` reached with the LONGHU_SSH_* settings; the
container's image must have the probe command). When the binding's BindingSpec declares agreement tolerances it probes
each reference source the same way, with the same parameters, and compares the sampled rows pair by pair, field by field,
with both sides projected as the resolver projects them (field_map, unit_factors). It writes
scripts/data/tdx_promote_<capability>_<source>_<date>_<egress>.json (date in Asia/Shanghai) with every input, the probes,
the comparison and one verdict per gate, and exits 0 whatever the verdicts:

    owner_egress  the probe ran inside the owner container and answered with rows
    agreement     every declared tolerance holds (nothing to hold when none is declared)
    intraday      every probe started and finished inside a trading session (Asia/Shanghai, weekdays 09:30-11:30 and
                  13:00-15:00; holidays are not read from reference.trade_calendar)

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
import math
import os
import shlex
import subprocess
import sys
from collections.abc import Mapping
from datetime import datetime, time, timezone
from pathlib import Path
from typing import Any
from zoneinfo import ZoneInfo

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "quant-service"))
from app.datasources.catalog import bindings_for  # noqa: E402
from app.datasources.contracts import BINDING_STATES, Binding  # noqa: E402
from app.datasources.resolver import _normalise_rows  # noqa: E402

SCHEMA = "tdx-promote-v1"
DATA_DIR = ROOT / "scripts" / "data"
CATALOG = ROOT / "quant-service" / "app" / "datasources" / "catalog.py"
CONTAINER = "trading-hareness-peer-quant-research-1"
OWNER_SETTINGS = ("LONGHU_SSH_HOST", "LONGHU_SSH_PORT", "LONGHU_SSH_USER", "LONGHU_SSH_KEY_PATH")
#: Rootless Docker on the owner, as in release-sync-status.sh; the script goes to ``bash -s`` on stdin.
OWNER_DOCKER = ('export XDG_RUNTIME_DIR="${XDG_RUNTIME_DIR:-/run/user/$(id -u)}"\n'
                'export DOCKER_HOST="${DOCKER_HOST:-unix://$XDG_RUNTIME_DIR/docker.sock}"\n')
CN = ZoneInfo("Asia/Shanghai")
SESSIONS = ((time(9, 30), time(11, 30)), (time(13, 0), time(15, 0)))
NO_HOLIDAYS = "holidays not checked: every weekday counts as a trading day"
#: Status token in catalog.py -> (the token after the step, the gates every evidence file must have passed).
STEPS = {"UNSUPPORTED": ("DECLARED", ("owner_egress", "agreement")),
         "DECLARED": ("LIVE_VERIFIED", ("owner_egress", "agreement", "intraday"))}


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


def run_probe(egress: str, source: str, capability: str, params: Mapping[str, Any]) -> dict[str, Any]:
    """The probe record of one binding, taken on ``egress``; the command that took it is added."""
    probe = ["python", "-m", "app.datasources", "probe", source, capability, "--params", json.dumps(params)]
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


def in_session(instant: datetime) -> bool:
    local = instant.astimezone(CN)
    return local.weekday() < 5 and any(start <= local.time() < end for start, end in SESSIONS)


def probed_in_session(record: Mapping[str, Any]) -> bool:
    return all(in_session(datetime.fromisoformat(record[key])) for key in ("started_utc", "finished_utc"))


def reading(binding: Binding, record: Mapping[str, Any]) -> tuple[list[dict[str, Any]], str]:
    """The sampled rows as the resolver projects them, and why they cannot be compared (empty when they can)."""
    if record["error"]:
        return [], f"{record['source']} probe failed: {record['error']}"
    projected = _normalise_rows(record["sample"], binding)
    if projected.status:
        return [], f"{record['source']} sample is {projected.status}: {', '.join(projected.warnings)}"
    return projected.rows, ""


def close(left: Any, right: Any, rel_tol: float, abs_tol: float) -> bool:
    numbers = all(isinstance(value, (int, float)) and not isinstance(value, bool) for value in (left, right))
    return numbers and math.isclose(left, right, rel_tol=rel_tol, abs_tol=abs_tol)


def compare_field(name: str, tolerance: Mapping[str, Any], mine: list[dict[str, Any]], theirs: list[dict[str, Any]],
                  unusable: str) -> dict[str, Any]:
    """One field of the two sampled readings, paired in order; ``why`` is empty when they agree."""
    rel_tol, abs_tol = tolerance.get("rel_tol", 0.0), tolerance.get("abs_tol", 0.0)
    pairs = [[left.get(name), right.get(name)] for left, right in zip(mine, theirs)]
    outside = sum(not close(left, right, rel_tol, abs_tol) for left, right in pairs)
    if unusable:
        why = unusable
    elif len(mine) != len(theirs) or not pairs:
        why = f"sampled {len(mine)} rows against {len(theirs)}"
    else:
        why = f"{outside} of {len(pairs)} pairs outside the tolerance" if outside else ""
    return {"reference": tolerance["reference"], "rel_tol": rel_tol, "abs_tol": abs_tol, "pairs": pairs,
            "agree": not why, "why": why}


def compare(source: str, agreement: Mapping[str, Mapping[str, Any]], bindings: Mapping[str, Binding],
            probes: Mapping[str, Mapping[str, Any]]) -> dict[str, Any]:
    """Each agreement field of ``source`` against the reference that field names."""
    mine, mine_why = reading(bindings[source], probes[source])
    result = {}
    for name, tolerance in agreement.items():
        reference = tolerance["reference"]
        theirs, their_why = reading(bindings[reference], probes[reference])
        result[name] = compare_field(name, tolerance, mine, theirs, mine_why or their_why)
    return result


def verdicts(egress: str, source: str, probes: Mapping[str, Mapping[str, Any]], comparison: Mapping[str, Any],
             inside: Mapping[str, bool]) -> dict[str, dict[str, Any]]:
    own = probes[source]
    disagreeing = [f"{name}: {item['why']}" for name, item in comparison.items() if not item["agree"]]
    outside = [name for name, in_it in inside.items() if not in_it]
    return {
        "owner_egress": {
            "pass": egress == "owner" and not own["error"] and own["rows"] > 0,
            "detail": f"egress {egress}, " + (f"failed: {own['error']}" if own["error"] else f"{own['rows']} rows")},
        "agreement": {
            "pass": not disagreeing,
            "detail": "; ".join(disagreeing) or (f"{len(comparison)} fields within tolerance" if comparison
                                                 else "not applicable: the binding declares no agreement tolerances")},
        "intraday": {
            "pass": not outside,
            "detail": (f"outside a session: {', '.join(outside)}" if outside else "every probe ran inside a session")
            + f"; {NO_HOLIDAYS}"},
    }


def run_check(args: argparse.Namespace) -> int:
    missing = [name for name in OWNER_SETTINGS if not os.environ.get(name)]
    if args.egress == "owner" and missing:
        sys.exit("the owner egress needs these settings (names only): " + ", ".join(missing))
    bindings = {args.source: lookup(args.source, args.capability)}
    spec = bindings[args.source].spec
    agreement = spec.agreement if spec else {}
    for reference in sorted({item["reference"] for item in agreement.values()}):
        bindings[reference] = lookup(reference, args.capability)
    probes = {source: run_probe(args.egress, source, args.capability, args.params) for source in bindings}
    comparison = compare(args.source, agreement, bindings, probes)
    inside = {source: probed_in_session(record) for source, record in probes.items()}
    evidence = {
        "schema": SCHEMA, "source": args.source, "capability": args.capability, "params": args.params,
        "egress": args.egress, "catalog_status": bindings[args.source].status,
        "checked_utc": datetime.now(timezone.utc).isoformat(), "agreement_spec": agreement, "probes": probes,
        "comparison": comparison,
        "session": {"timezone": "Asia/Shanghai", "windows": "weekdays 09:30-11:30 and 13:00-15:00", "holidays": NO_HOLIDAYS,
                    "inside": inside},
        "verdicts": verdicts(args.egress, args.source, probes, comparison, inside),
    }
    day = datetime.fromisoformat(probes[args.source]["finished_utc"]).astimezone(CN).date()
    path = DATA_DIR / f"tdx_promote_{args.capability}_{args.source}_{day.isoformat()}_{args.egress}.json"
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
