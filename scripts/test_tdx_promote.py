"""scripts/tdx-promote.py: the checks that decide a status change, and the one-token catalog edit."""

import contextlib
import hashlib
import importlib.util
import io
import json
import os
import re
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
KEY = ["symbol"]
OWN = Binding("tdx_public", CAPABILITY, 70, UNSUPPORTED, spec=BindingSpec(
    field_map={"vol": "volume"}, unit_factors={"volume": 100},
    agreement={"price": {"reference": "tencent_free", "key": KEY, "rel_tol": 0.001},
               "volume": {"reference": "tencent_free", "key": KEY}}))
REFERENCE = Binding("tencent_free", CAPABILITY, 30, LIVE_VERIFIED)
SYMBOLS = [f"{number:06d}.SZ" for number in range(20)]
OWN_ROWS = [{"symbol": symbol, "price": 10.0, "vol": 3} for symbol in SYMBOLS]                  # volume in lots
REFERENCE_ROWS = [{"symbol": symbol, "price": 10.0, "volume": 300} for symbol in reversed(SYMBOLS)]    # in shares, another order
REFERENCE_ROWS[0]["price"] = 10.005       # inside the relative tolerance, outside the absolute one (none is declared)


def record(source, rows, times=MORNING, error=None):
    base = {"source": source, "capability": CAPABILITY, "params": {}, "started_utc": times[0], "finished_utc": times[1]}
    if error:
        return {**base, "error": error}
    return {**base, "rows": len(rows), "coverage": None, "warnings": ["tdx_host=1.2.3.4:7709/login_one"],
            "sample": rows, "error": None}


def answers(**changes):
    return {"tdx_public": record("tdx_public", OWN_ROWS), "tencent_free": record("tencent_free", REFERENCE_ROWS), **changes}


def run_check(egress, probes, bindings=(OWN, REFERENCE), params=None):
    """``check`` on fake probe records, in a scratch repository."""
    with tempfile.TemporaryDirectory() as scratch:
        root = Path(scratch).resolve()
        (root / "data").mkdir()
        calls = []

        def probe(egress_taken, source, _capability, _params, **options):
            calls.append((egress_taken, source, options))
            return probes[source]

        out = io.StringIO()
        with mock.patch.multiple(MODULE, ROOT=root, DATA_DIR=root / "data", run_probe=probe,
                                 bindings_for=lambda capability, states: [b for b in bindings if b.capability == capability]), \
                mock.patch.dict(os.environ, SETTINGS), contextlib.redirect_stdout(out):
            code = MODULE.main(["check", "tdx_public", CAPABILITY, "--egress", egress, "--params", json.dumps(params or {})])
        files = sorted((root / "data").glob("*.json"))
        return SimpleNamespace(code=code, names=[path.name for path in files], calls=calls, stdout=out.getvalue(),
                               evidence=[json.loads(path.read_text(encoding="utf-8")) for path in files])


def evidence_of(egress="owner", **changes):
    return run_check(egress, answers(**changes)).evidence[0]


def failing(evidence):
    return sorted(gate for gate, item in evidence["verdicts"].items() if not item["pass"])


def fake_ssh(root, body):
    path = Path(root) / "ssh"
    path.write_text("#!/bin/sh\n" + body, encoding="utf-8")
    path.chmod(0o755)


class CheckTests(unittest.TestCase):
    def test_an_agreeing_owner_probe_inside_a_session_passes_every_gate(self):
        result = run_check("owner", answers())
        self.assertRegex(result.names[0], r"^tdx_promote_quote\.watch_snapshot_tdx_public_2026-10-12_owner_[0-9a-f]{8}\.json$")
        evidence = result.evidence[0]
        self.assertEqual({gate: item["pass"] for gate, item in evidence["verdicts"].items()},
                         {"owner_egress": True, "agreement": True, "intraday": True})
        volume = evidence["comparison"]["volume"]
        self.assertEqual((volume["matched"], volume["mismatched"], volume["only_left"], volume["only_right"], volume["max_abs_dev"]),
                         (20, 0, 0, 0, 0), "lots became shares, and the order of the rows did not matter")
        self.assertEqual(evidence["projections"], {"tdx_public": {"field_map": {"vol": "volume"}, "unit_factors": {"volume": 100}}})
        self.assertEqual((evidence["schema"], evidence["catalog_status"], evidence["decision_eligible"], evidence["egress"]),
                         ("tdx-promote-v2", "unsupported", False, "owner"))
        self.assertEqual(evidence["session"]["inside"], {"tdx_public": True, "tencent_free": True})
        self.assertEqual(result.calls, [("owner", "tdx_public", {"all_rows": True}), ("owner", "tencent_free", {"all_rows": True})])
        self.assertIn("agreement: pass", result.stdout)

    def test_the_evidence_keeps_the_first_rows_and_the_hash_of_all_of_them(self):
        probe = run_check("owner", answers()).evidence[0]["probes"]["tdx_public"]
        self.assertEqual((probe["rows"], probe["sample"]), (20, OWN_ROWS[:5]))
        self.assertEqual(probe["sample_sha256"],
                         hashlib.sha256(json.dumps(OWN_ROWS, ensure_ascii=False, sort_keys=True).encode("utf-8")).hexdigest())

    def test_two_checks_with_other_parameters_do_not_share_an_evidence_file(self):
        names = {json.dumps(params): run_check("owner", answers(), params=params).names[0]
                 for params in ({}, {"symbols": ["000001.SZ"]}, {"symbols": ["600519.SH"]})}
        self.assertEqual(len(set(names.values())), 3, names)
        self.assertEqual(len({re.sub(r"_[0-9a-f]{8}\.json$", "", name) for name in names.values()}), 1)

    def test_each_gate_fails_for_its_own_reason(self):
        self.assertEqual(failing(evidence_of("mac")), ["owner_egress"])
        self.assertEqual(failing(evidence_of(tdx_public=record("tdx_public", OWN_ROWS, LUNCH))), ["intraday"])
        self.assertEqual(failing(evidence_of(tdx_public=record("tdx_public", OWN_ROWS, CLOSING))), ["intraday"])
        off = evidence_of(tdx_public=record("tdx_public", [{**OWN_ROWS[0], "price": 10.1}, *OWN_ROWS[1:]]))
        self.assertEqual(failing(off), ["agreement"])
        self.assertEqual((off["comparison"]["price"]["agree"], off["comparison"]["volume"]["agree"]), (False, True))
        absent = evidence_of(tdx_public=record("tdx_public", [{"symbol": s, "vol": 3} for s in SYMBOLS]),
                             tencent_free=record("tencent_free", [{"symbol": s, "volume": 300} for s in SYMBOLS]))
        self.assertEqual((failing(absent), absent["comparison"]["price"]["mismatched"]), (["agreement"], 20),
                         "a value neither side has agrees with nothing")
        gone = evidence_of(tencent_free=record("tencent_free", [], error="OSError: refused"))
        self.assertEqual(failing(gone), ["agreement"])
        self.assertEqual(gone["verdicts"]["agreement"]["detail"].count("tencent_free probe failed: OSError: refused"), 2)
        down = evidence_of(tdx_public=record("tdx_public", [], error="TdxProtocolError: no TDX host answered"))
        self.assertEqual(failing(down), ["agreement", "owner_egress"])

    def test_rows_are_joined_on_the_key_and_every_common_row_is_counted(self):
        # The reference misses the first symbol, has one the binding lacks and quotes the last symbol 1% higher.
        reference = [{**row, "price": 10.1} if row["symbol"] == SYMBOLS[-1] else row for row in REFERENCE_ROWS[:-1]]
        reference.append({"symbol": "999999.SZ", "price": 5.0, "volume": 100})
        price = evidence_of(tencent_free=record("tencent_free", reference))["comparison"]["price"]
        self.assertEqual({name: price[name] for name in ("left_rows", "right_rows", "matched", "mismatched", "only_left", "only_right")},
                         {"left_rows": 20, "right_rows": 20, "matched": 18, "mismatched": 1, "only_left": 1, "only_right": 1})
        self.assertEqual((price["coverage"], price["worst_key"], price["agree"]), (0.95, [SYMBOLS[-1]], False))
        self.assertEqual(price["examples"], [{"key": [SYMBOLS[-1]], "left": 10.0, "right": 10.1}])
        self.assertAlmostEqual(price["max_abs_dev"], 0.1)
        self.assertAlmostEqual(price["max_rel_dev"], 0.1 / 10.1)

    def test_the_common_rows_must_cover_the_share_the_entry_asks_for(self):
        def agree(missing, binding=OWN):
            reference = record("tencent_free", REFERENCE_ROWS[:len(REFERENCE_ROWS) - missing])
            result = run_check("owner", answers(tencent_free=reference), bindings=(binding, REFERENCE))
            return result.evidence[0]["comparison"]["price"]["agree"]

        self.assertTrue(agree(1), "19 of 20 is the default 0.95")
        self.assertFalse(agree(2), "18 of 20 is not")
        lenient = {"price": {"reference": "tencent_free", "key": KEY, "rel_tol": 0.001, "min_coverage": 0.5}}
        self.assertTrue(agree(2, Binding("tdx_public", CAPABILITY, 70, UNSUPPORTED, spec=BindingSpec(agreement=lenient))))

    def test_rows_that_cannot_be_joined_fail_the_agreement_with_the_reason(self):
        def why(**changes):
            agreement = evidence_of(**changes)["verdicts"]["agreement"]
            self.assertFalse(agreement["pass"])
            return agreement["detail"]

        self.assertIn("tencent_free printed 20 of its 21 rows",
                      why(tencent_free={**record("tencent_free", REFERENCE_ROWS), "rows": 21}))
        self.assertIn("tencent_free printed rows that are not mappings",
                      why(tencent_free=record("tencent_free", [[{"price": 10.0}], {"fetched": 1}])))     # an adapter that returns a tuple
        self.assertIn("tencent_free rows lack the key fields ['symbol']",
                      why(tencent_free=record("tencent_free", [{"price": 10.0, "volume": 300}])))
        self.assertIn("tencent_free has 1 rows with a repeated key ['symbol']",
                      why(tencent_free=record("tencent_free", [*REFERENCE_ROWS, REFERENCE_ROWS[0]])))

    def test_a_binding_without_tolerances_has_nothing_to_agree_with(self):
        plain = Binding("tdx_public", CAPABILITY, 70, UNSUPPORTED, spec=BindingSpec())
        result = run_check("owner", answers(), bindings=(plain,))
        self.assertEqual(result.calls, [("owner", "tdx_public", {"all_rows": False})], "no reference is probed")
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
                taken = MODULE.run_probe("owner", "tdx_public", "quote.all_a_snapshot", {}, all_rows=True)
            args = Path(root, "args").read_text(encoding="utf-8").split("\n")
            script = Path(root, "stdin").read_text(encoding="utf-8")
        command = ("docker exec trading-hareness-peer-quant-research-1 python -m app.datasources probe tdx_public "
                   "quote.all_a_snapshot --params '{}' --all-rows")
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
    _bind("tdx_public", "quote.watch_snapshot", 70, UNSUPPORTED, None),
    _bind("fuyao_ths", "quote.all_a_snapshot", 12, LIVE_VERIFIED, "store", decision_eligible=True),
    *(_bind("eastmoney_datacenter", f"events.{key}", 50, DECLARED, key) for key in ("a", "b")),
)
'''


def evidence_file(root, name, source, capability, **passes):
    gates = {"owner_egress": True, "agreement": True, "intraday": True, **passes}
    path = root / "scripts" / "data" / name
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps({"schema": MODULE.SCHEMA, "source": source, "capability": capability,
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

    def test_apply_takes_the_file_check_wrote(self):
        written = run_check("owner", answers())
        with scratch_repository() as root:
            path = root / "scripts" / "data" / written.names[0]
            path.parent.mkdir(parents=True)
            path.write_text(json.dumps(written.evidence[0]), encoding="utf-8")
            printed = run_apply(root, path)
            promoted = (root / "catalog.py").read_text(encoding="utf-8")
        self.assertIn("Promote tdx_public quote.watch_snapshot from UNSUPPORTED to DECLARED", printed)
        self.assertIn('_bind("tdx_public", "quote.watch_snapshot", 70, DECLARED, None),', promoted)

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
