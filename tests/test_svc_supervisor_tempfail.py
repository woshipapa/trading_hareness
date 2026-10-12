import importlib.util
from pathlib import Path

SPEC = importlib.util.spec_from_file_location("svc_supervisor", Path(__file__).resolve().parents[1] / "svc_supervisor.py"
                                              if Path(__file__).resolve().parents[1].name == "scripts"
                                              else Path(__file__).resolve().parents[1] / "scripts" / "svc_supervisor.py")
sup = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(sup)


def _runs(monkeypatch, codes):
    seq = list(codes)
    calls = []
    monkeypatch.setattr(sup, "run_once", lambda t: calls.append(1) or seq.pop(0))
    monkeypatch.setattr(sup, "slog", lambda *_a, **_k: None)
    return calls


def test_retries_exit_75_until_success(monkeypatch):
    calls, waits = _runs(monkeypatch, [75, 75, 0]), []
    rc = sup.run_once_retry_tempfail({"name": "x"}, sleep=waits.append)
    assert rc == 0 and len(calls) == 3 and waits == [300, 900]


def test_gives_up_after_the_delays(monkeypatch):
    calls, waits = _runs(monkeypatch, [75, 75, 75, 75]), []
    rc = sup.run_once_retry_tempfail({"name": "x"}, sleep=waits.append)
    assert rc == 75 and len(calls) == 4 and waits == [300, 900, 2700]


def test_other_failures_are_not_retried(monkeypatch):
    calls, waits = _runs(monkeypatch, [1]), []
    assert sup.run_once_retry_tempfail({"name": "x"}, sleep=waits.append) == 1
    assert len(calls) == 1 and waits == []


def test_shutdown_stops_retrying(monkeypatch):
    calls = _runs(monkeypatch, [75, 0])
    sup._shutdown.set()
    try:
        assert sup.run_once_retry_tempfail({"name": "x"}, sleep=lambda _d: None) == 75
        assert len(calls) == 1
    finally:
        sup._shutdown.clear()
