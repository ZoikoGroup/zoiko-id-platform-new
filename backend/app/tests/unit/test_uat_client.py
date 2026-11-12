"""The independent tester's client: it records, it never decides, and it never leaks the token (ZID-UAT-005)."""

from __future__ import annotations

import importlib.util
import json
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[4]
spec = importlib.util.spec_from_file_location(
    "uat_client", ROOT / "tools" / "scripts" / "uat_client.py"
)
uc = importlib.util.module_from_spec(spec)  # type: ignore[arg-type]
sys.modules["uat_client"] = uc
spec.loader.exec_module(uc)  # type: ignore[union-attr]

RESULTS = {
    "release_unit": {"country": "US"}, "environment_id": "env-1", "build_sha256": "b" * 64, "config_digest": "c" * 64, "fixture_hashes": [],
    "cases": [
        {"case_id": "GOV-01", "events": [{"kind": "request.sent", "payload": {"status": 403}}, {"kind": "response.seen", "payload": {}}], "result": "PASS", "trace_refs": ["t1"]},
        {"case_id": "DOC-02", "events": [], "result": "PASS"},
    ],
}  # fmt: skip


class Fake:
    """A stand-in for the API that answers like it: a thin PASS manifest comes back INCONCLUSIVE."""

    def __init__(self, refuse: str | None = None) -> None:
        self.calls: list[tuple[str, str, dict | None]] = []
        self.refuse = refuse

    def __call__(self, method, path, body):
        self.calls.append((method, path, dict(body) if body else None))
        if self.refuse and self.refuse in path:
            return 403, {"code": "not_the_runs_tester", "detail": "only the run's tester"}
        if path.endswith("/test-runs"):
            return 201, {"run_id": "run-1"}
        if path.endswith("/events"):
            return 201, {"seq": 1}
        case = path.split("/")[-2]
        thin = not (body or {}).get("trace_refs")
        return 201, {
            "verdict": "INCONCLUSIVE" if thin else "PASS",
            "problems": ["NO_TRACE_RECEIPT_OR_SOURCE_SNAPSHOT"] if thin else [],
            "case": case,
        }


def test_a_results_file_becomes_a_run_events_and_one_manifest_per_case() -> None:
    f = Fake()
    out = uc.record(f, RESULTS)
    paths = [(m, p) for m, p, _ in f.calls]
    assert paths[0] == ("POST", "/v2/assurance/test-runs")
    assert paths.count(("POST", "/v2/assurance/test-runs/run-1/events")) == 2
    assert ("POST", "/v2/assurance/test-runs/run-1/cases/GOV-01/manifest") in paths and (
        "POST",
        "/v2/assurance/test-runs/run-1/cases/DOC-02/manifest",
    ) in paths
    assert f.calls[0][2]["case_ids"] == ["GOV-01", "DOC-02"]  # type: ignore[index]
    assert out["run_id"] == "run-1"


def test_the_platform_not_the_client_decides_and_a_thin_pass_is_reported_as_inconclusive() -> None:
    out = uc.record(Fake(), RESULTS)
    by_case = {v["case_id"]: v for v in out["verdicts"]}
    assert by_case["GOV-01"]["verdict"] == "PASS" and by_case["DOC-02"]["verdict"] == "INCONCLUSIVE"
    assert by_case["DOC-02"]["claimed"] == "PASS" and out["all_as_claimed"] is False, (
        "the tool says the claim was not what the evidence supports"
    )


def test_event_keys_are_the_same_every_time_so_a_second_run_of_the_file_records_nothing_twice() -> (
    None
):
    a, b = Fake(), Fake()
    uc.record(a, RESULTS)
    uc.record(b, RESULTS)
    keys = lambda f: [c[2]["idempotency_key"] for c in f.calls if c[1].endswith("/events")]
    assert keys(a) == keys(b) and len(set(keys(a))) == 2
    assert uc.event_key("r", "GOV-01", 0, "k", {"a": 1}) != uc.event_key(
        "r", "GOV-01", 1, "k", {"a": 1}
    )


def test_a_refusal_stops_the_run_and_says_what_was_refused() -> None:
    with pytest.raises(uc.UatError, match="not_the_runs_tester"):
        uc.record(Fake(refuse="/events"), RESULTS)
    with pytest.raises(uc.UatError, match="create run"):
        uc.record(Fake(refuse="/test-runs"), RESULTS)


def test_an_existing_run_can_be_appended_to_without_creating_another() -> None:
    f = Fake()
    uc.record(f, RESULTS, run_id="run-9")
    assert not any(p.endswith("/test-runs") for _m, p, _b in f.calls) and any(
        "run-9" in p for _m, p, _b in f.calls
    )


def test_the_token_comes_from_the_environment_only(monkeypatch, tmp_path, capsys) -> None:
    f = tmp_path / "r.json"
    f.write_text(json.dumps(RESULTS), encoding="utf-8")
    monkeypatch.delenv("ZID_UAT_TOKEN", raising=False)
    assert uc.main([str(f), "--base-url", "http://x"]) == 2
    assert "ZID_UAT_TOKEN" in capsys.readouterr().err
    with pytest.raises(SystemExit):
        uc.main([str(f), "--base-url", "http://x", "--token", "secret"])
