"""scripts/tdx-promote.py: the checks that decide a status change, and the one-token catalog edit."""

import contextlib
import hashlib
import importlib.util
import io
import json
import os
import tempfile
import unittest
from datetime import datetime
from pathlib import Path
from types import SimpleNamespace
from unittest import mock

SCRIPT = Path(__file__).with_name("tdx-promote.py")
SPEC = importlib.util.spec_from_file_location("tdx_promote", SCRIPT)
MODULE = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(MODULE)

from app.datasources.catalog import BINDINGS  # noqa: E402
from app.datasources.contracts import LIVE_VERIFIED, UNSUPPORTED, Binding, BindingSpec  # noqa: E402

CAPABILITY = "quote.watch_snapshot"
SETTINGS = {"LONGHU_SSH_HOST": "synthetic-host", "LONGHU_SSH_PORT": "22", "LONGHU_SSH_USER": "synthetic-user",
            "LONGHU_SSH_KEY_PATH": "/secret/key"}
MORNING = ("2026-10-12T01:40:00+00:00", "2026-10-12T01:40:02+00:00")    # Monday 09:40 in Shanghai
LUNCH = ("2026-10-12T04:30:00+00:00", "2026-10-12T04:30:02+00:00")      # Monday 12:30
CLOSING = ("2026-10-12T03:29:58+00:00", "2026-10-12T03:30:02+00:00")    # starts at 11:29:58, ends after the morning session
OWN = Binding("tdx_public", CAPABILITY, 70, UNSUPPORTED, spec=BindingSpec(
    field_map={"vol": "volume"}, unit_factors={"volume": 100},
    agreement={"price": {"reference": "tencent_free", "rel_tol": 0.001}, "volume": {"reference": "tencent_free"}}))
REFERENCE = Binding("tencent_free", CAPABILITY, 30, LIVE_VERIFIED)
OWN_ROWS = [{"price": 10.0, "vol": 3}, {"price": 20.0, "vol": 5}]                    # volume in lots
REFERENCE_ROWS = [{"price": 10.005, "volume": 300}, {"price": 20.0, "volume": 500}]  # volume in shares


def record(source, rows, times=MORNING, error=None):
    base = {"source": source, "capability": CAPABILITY, "params": {}, "started_utc": times[0], "finished_utc": times[1]}
    if error:
        return {**base, "error": error}
    return {**base, "rows": len(rows), "coverage": None, "warnings": ["tdx_host=1.2.3.4:7709/login_one"],
            "sample": rows, "error": None}


def answers(**changes):
    return {"tdx_public": record("tdx_public", OWN_ROWS), "tencent_free": record("tencent_free", REFERENCE_ROWS), **changes}


def run_check(egress, probes, bindings=(OWN, REFERENCE)):
    """``check`` on fake probe records, in a scratch repository."""
    with tempfile.TemporaryDirectory() as scratch:
        root = Path(scratch).resolve()
        (root / "data").mkdir()
        calls = []

        def probe(egress_taken, source, _capability, _params):
            calls.append((egress_taken, source))
            return probes[source]

        out = io.StringIO()
        with mock.patch.multiple(MODULE, ROOT=root, DATA_DIR=root / "data", run_probe=probe,
                                 bindings_for=lambda capability, states: [b for b in bindings if b.capability == capability]), \
                mock.patch.dict(os.environ, SETTINGS), contextlib.redirect_stdout(out):
            code = MODULE.main(["check", "tdx_public", CAPABILITY, "--egress", egress])
        files = sorted((root / "data").glob("*.json"))
        return SimpleNamespace(code=code, names=[path.name for path in files], calls=calls, stdout=out.getvalue(),
                               evidence=[json.loads(path.read_text(encoding="utf-8")) for path in files])


def fake_ssh(root, body):
    path = Path(root) / "ssh"
    path.write_text("#!/bin/sh\n" + body, encoding="utf-8")
    path.chmod(0o755)


class CheckTests(unittest.TestCase):
    def test_an_agreeing_owner_probe_inside_a_session_passes_every_gate(self):
        result = run_check("owner", answers())
        self.assertEqual((result.code, result.names), (0, ["tdx_promote_quote.watch_snapshot_tdx_public_2026-10-12_owner.json"]))
        evidence = result.evidence[0]
        self.assertEqual({gate: item["pass"] for gate, item in evidence["verdicts"].items()},
                         {"owner_egress": True, "agreement": True, "intraday": True})
        self.assertEqual(evidence["comparison"]["volume"]["pairs"], [[300, 300], [500, 500]], "lots became shares first")
        self.assertEqual((evidence["schema"], evidence["catalog_status"], evidence["egress"]), ("tdx-promote-v1", "unsupported", "owner"))
        self.assertEqual(evidence["session"]["inside"], {"tdx_public": True, "tencent_free": True})
        self.assertEqual(result.calls, [("owner", "tdx_public"), ("owner", "tencent_free")])
        self.assertIn("agreement: pass", result.stdout)

    def test_each_gate_fails_for_its_own_reason(self):
        def verdicts(egress="owner", **changes):
            return run_check(egress, answers(**changes)).evidence[0]["verdicts"]

        def failing(items):
            return sorted(gate for gate, item in items.items() if not item["pass"])

        self.assertEqual(failing(verdicts("mac")), ["owner_egress"])
        self.assertEqual(failing(verdicts(tdx_public=record("tdx_public", OWN_ROWS, LUNCH))), ["intraday"])
        self.assertEqual(failing(verdicts(tdx_public=record("tdx_public", OWN_ROWS, CLOSING))), ["intraday"])
        off = verdicts(tdx_public=record("tdx_public", [{"price": 10.1, "vol": 3}, OWN_ROWS[1]]))
        self.assertEqual((failing(off), off["agreement"]["detail"]), (["agreement"], "price: 1 of 2 pairs outside the tolerance"))
        inexact = verdicts(tencent_free=record("tencent_free", [{"price": 10.005, "volume": 301}, REFERENCE_ROWS[1]]))
        self.assertEqual((failing(inexact), inexact["agreement"]["detail"]),
                         (["agreement"], "volume: 1 of 2 pairs outside the tolerance"), "no tolerance given means equal")
        short = verdicts(tencent_free=record("tencent_free", REFERENCE_ROWS[:1]))
        self.assertEqual((failing(short), short["agreement"]["detail"]),
                         (["agreement"], "price: sampled 2 rows against 1; volume: sampled 2 rows against 1"))
        gone = verdicts(tencent_free=record("tencent_free", [], error="OSError: refused"))
        self.assertEqual((failing(gone), gone["agreement"]["detail"].count("tencent_free probe failed: OSError: refused")),
                         (["agreement"], 2))
        down = verdicts(tdx_public=record("tdx_public", [], error="TdxProtocolError: no TDX host answered"))
        self.assertEqual(failing(down), ["agreement", "owner_egress"])

    def test_a_binding_without_tolerances_has_nothing_to_agree_with(self):
        plain = Binding("tdx_public", CAPABILITY, 70, UNSUPPORTED, spec=BindingSpec())
        result = run_check("owner", answers(), bindings=(plain,))
        self.assertEqual(result.calls, [("owner", "tdx_public")], "no reference is probed")
        verdict = result.evidence[0]["verdicts"]["agreement"]
        self.assertTrue(verdict["pass"] and verdict["detail"].startswith("not applicable"), verdict)

    def test_a_session_is_a_weekday_morning_or_afternoon_window(self):
        def inside(text):
            return MODULE.in_session(datetime.fromisoformat(text + "+08:00"))

        for text in ("2026-10-12T09:30:00", "2026-10-12T11:29:59", "2026-10-12T13:00:00", "2026-10-12T14:59:59"):
            self.assertTrue(inside(text), text)
        for text in ("2026-10-12T09:29:59", "2026-10-12T11:30:00", "2026-10-12T12:30:00", "2026-10-12T15:00:00",
                     "2026-10-10T10:00:00"):          # the last one is a Saturday
            self.assertFalse(inside(text), text)

    def test_the_owner_egress_is_refused_naming_only_the_missing_settings(self):
        partial = {name: value for name, value in SETTINGS.items() if name != "LONGHU_SSH_KEY_PATH"}
        with mock.patch.dict(os.environ, partial, clear=True), mock.patch.object(MODULE, "run_probe") as probe, \
                self.assertRaises(SystemExit) as refused:
            MODULE.main(["check", "tdx_public", CAPABILITY, "--egress", "owner"])
        self.assertEqual(str(refused.exception), "the owner egress needs these settings (names only): LONGHU_SSH_KEY_PATH")
        probe.assert_not_called()


class ProbeTransportTests(unittest.TestCase):
    def test_the_owner_probe_is_a_docker_exec_sent_to_bash_over_ssh(self):
        with tempfile.TemporaryDirectory() as root:
            fake_ssh(root, 'printf "%s\\n" "$@" > "$ARGS_FILE"\n'
                           'while IFS= read -r line; do printf "%s\\n" "$line"; done > "$STDIN_FILE"\n'
                           'printf "%s\\n" "$PROBE_JSON"\n')
            env = {"PATH": root, "ARGS_FILE": f"{root}/args", "STDIN_FILE": f"{root}/stdin", **SETTINGS,
                   "PROBE_JSON": json.dumps(record("tdx_public", [{"price": 1.0}]))}
            with mock.patch.dict(os.environ, env, clear=True):
                taken = MODULE.run_probe("owner", "tdx_public", "quote.all_a_snapshot", {})
            args = Path(root, "args").read_text(encoding="utf-8").split("\n")
            script = Path(root, "stdin").read_text(encoding="utf-8")
        command = "docker exec trading-hareness-peer-quant-research-1 python -m app.datasources probe tdx_public quote.all_a_snapshot --params '{}'"
        self.assertEqual(taken["command"], command)
        self.assertEqual(taken["rows"], 1)
        self.assertEqual(args[-4:], ["synthetic-user@synthetic-host", "bash", "-s", ""])
        self.assertIn("StrictHostKeyChecking=yes", args)
        self.assertEqual(script.split("\n")[2:], [command, ""])
        self.assertIn("DOCKER_HOST", script)

    def test_connection_values_never_reach_an_error_message(self):
        with tempfile.TemporaryDirectory() as root:
            fake_ssh(root, 'echo \'Load key "/secret/key" for synthetic-user@synthetic-host: denied\' >&2\nexit 255\n')
            with mock.patch.dict(os.environ, {"PATH": root, **SETTINGS}, clear=True), self.assertRaises(SystemExit) as refused:
                MODULE.run_probe("owner", "tdx_public", "quote.all_a_snapshot", {})
        message = str(refused.exception)
        self.assertIn('Load key "[ssh-key]" for [ssh-user]@[ssh-host]: denied', message)
        for secret in SETTINGS.values():
            if secret != "22":
                self.assertNotIn(secret, message)

    def test_the_mac_probe_runs_the_app_cli_from_the_service_directory(self):
        # A source the catalog does not have is refused by the CLI before any adapter is called: no network.
        with self.assertRaises(SystemExit) as refused:
            MODULE.run_probe("mac", "no_such_source", CAPABILITY, {})
        self.assertIn("no_such_source -> quote.watch_snapshot: no such binding", str(refused.exception))
        self.assertIn("(exit 2)", str(refused.exception))


CATALOG_TEXT = '''\
BINDINGS = (
    _bind("tdx_public", "quote.all_a_snapshot", 80, UNSUPPORTED, None,
          "app/datasources/sources/tdx_legacy_misc.py:fetch_all_a_snapshot",
          notes="全 A 快照；decision_eligible 不变"),
    "说明", _bind("tdx_public", "quote.index_overview", 80, DECLARED, None, decision_eligible=False),
    _bind("fuyao_ths", "quote.all_a_snapshot", 12, LIVE_VERIFIED, "store", decision_eligible=True),
    *(_bind("eastmoney_datacenter", f"events.{key}", 50, DECLARED, key) for key in ("a", "b")),
)
'''


def evidence_file(root, name, source, capability, **passes):
    gates = {"owner_egress": True, "agreement": True, "intraday": True, **passes}
    path = root / "scripts" / "data" / name
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps({"schema": "tdx-promote-v1", "source": source, "capability": capability,
                                "verdicts": {gate: {"pass": ok, "detail": f"{gate} detail"} for gate, ok in gates.items()}}),
                    encoding="utf-8")
    return path


def run_apply(root, *paths):
    out = io.StringIO()
    with mock.patch.multiple(MODULE, ROOT=root, CATALOG=root / "catalog.py"), contextlib.redirect_stdout(out):
        MODULE.main(["apply", *map(str, paths)])
    return out.getvalue()


@contextlib.contextmanager
def scratch_repository():
    with tempfile.TemporaryDirectory() as scratch:
        root = Path(scratch).resolve()
        (root / "catalog.py").write_text(CATALOG_TEXT, encoding="utf-8")
        yield root


class ApplyTests(unittest.TestCase):
    def test_apply_changes_one_status_token_and_prints_the_commit_that_cites_the_evidence(self):
        with scratch_repository() as root:
            # The first step needs no intraday observation.
            evidence = evidence_file(root, "first.json", "tdx_public", "quote.all_a_snapshot", intraday=False)
            digest = hashlib.sha256(evidence.read_bytes()).hexdigest()
            printed = run_apply(root, evidence)
            before, after = CATALOG_TEXT.split("\n"), (root / "catalog.py").read_text(encoding="utf-8").split("\n")
        self.assertEqual(len(before), len(after))
        self.assertEqual([(old, new) for old, new in zip(before, after) if old != new], [(
            '    _bind("tdx_public", "quote.all_a_snapshot", 80, UNSUPPORTED, None,',
            '    _bind("tdx_public", "quote.all_a_snapshot", 80, DECLARED, None,')])
        self.assertTrue(printed.startswith("git commit -F - -- catalog.py <<'EOF'\nPromote tdx_public quote.all_a_snapshot "
                                           "from UNSUPPORTED to DECLARED\n"), printed)
        self.assertIn(f"- scripts/data/first.json (sha256 {digest})", printed)
        self.assertIn("  agreement: agreement detail", printed)
        self.assertNotIn("intraday", printed)
        self.assertTrue(printed.endswith("\nEOF\n"))

    def test_the_status_column_is_found_after_multibyte_text_on_its_line(self):
        with scratch_repository() as root:
            evidence = evidence_file(root, "second.json", "tdx_public", "quote.index_overview")
            run_apply(root, evidence)
            after = (root / "catalog.py").read_text(encoding="utf-8")
        self.assertIn('"说明", _bind("tdx_public", "quote.index_overview", 80, LIVE_VERIFIED, None, decision_eligible=False),', after)

    def test_apply_refuses_unless_every_file_passed_every_gate_of_the_step(self):
        with scratch_repository() as root:
            good = evidence_file(root, "good.json", "tdx_public", "quote.index_overview")
            late = evidence_file(root, "late.json", "tdx_public", "quote.index_overview", intraday=False)
            other = evidence_file(root, "other.json", "tdx_public", "quote.all_a_snapshot")
            live = evidence_file(root, "live.json", "fuyao_ths", "quote.all_a_snapshot")
            for paths, message in (((good, late), "scripts/data/late.json: intraday failed (intraday detail)"),
                                   ((good, other), "different bindings"),
                                   ((live,), "is LIVE_VERIFIED: only UNSUPPORTED and DECLARED bindings are promoted")):
                with self.subTest(message=message), self.assertRaises(SystemExit) as refused:
                    run_apply(root, *paths)
                self.assertIn(message, str(refused.exception))
            self.assertEqual((root / "catalog.py").read_text(encoding="utf-8"), CATALOG_TEXT)

    def test_every_tdx_binding_of_the_catalog_has_its_status_token_where_apply_edits(self):
        text = MODULE.CATALOG.read_text(encoding="utf-8")
        names = {"unsupported": "UNSUPPORTED", "declared": "DECLARED", "live_verified": "LIVE_VERIFIED",
                 "dormant": "DORMANT", "retired": "RETIRED"}
        tdx = [item for item in BINDINGS if item.source.startswith("tdx_")]
        self.assertGreater(len(tdx), 20)
        for item in tdx:
            self.assertEqual(MODULE.bind_call(text, item.source, item.capability).args[3].id, names[item.status],
                             f"{item.source} {item.capability}")


if __name__ == "__main__":
    unittest.main()
