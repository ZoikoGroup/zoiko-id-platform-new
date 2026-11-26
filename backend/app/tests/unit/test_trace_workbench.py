"""The traceability workbench (ZID-ENG-TRC-001; ENG-AT-02, 03, 04, 26, 27): unknown stays unknown, changed code expires a PASS, a release needs its own market and independent signers."""

from __future__ import annotations

import importlib.util
import sys
from pathlib import Path

SPEC = importlib.util.spec_from_file_location("trace_workbench", Path(__file__).resolve().parents[4] / "tools" / "scripts" / "trace_workbench.py")
tw = importlib.util.module_from_spec(SPEC)  # type: ignore[arg-type]
sys.modules["trace_workbench"] = tw
SPEC.loader.exec_module(tw)  # type: ignore[union-attr]

SIGN = {"release_manager": "rita", "qa": "quinn", "security": "sam", "privacy_legal": "lee"}


def ready(**kw: object) -> object:
    base: dict[str, object] = {
        "release_id": "R", "market": "US", "tenant_class": "design-partner", "provider": "P1", "method": "doc", "build_digest": "sha256:" + "a" * 64,
        "approved_markets": ("US",), "dtv_missing": 0, "partner_e2e_signed_webhook": True, "signatures": SIGN, "implementer": "ivan", "rollback_rehearsed": True,
    }  # fmt: skip
    base.update(kw)
    return tw.Manifest(**base)


def test_nineteen_dtv_ids_stay_missing_source_and_none_is_countable_as_done() -> None:
    reg = tw.dtv_register()
    assert len(reg) == 34 and sum(r["state"] == "MISSING_SOURCE" for r in reg) == 19
    assert all(r["countable_as_done"] == "no" for r in reg)
    assert {r["id"] for r in reg if r["state"] == "CONDITIONAL_HOLD"} == {"DTV-03", "DTV-26", "DTV-27", "DTV-32", "DTV-33", "DTV-34"}


def test_the_sheet_says_not_assessed_where_there_is_no_source_and_never_claims_more_than_a_passed_run() -> None:
    req = [{"requirement_id": "FR-X-1", "priority": "P0", "owning_units_modules": "tenant", "evidence_link": "", "test_status": "NOT_STARTED"}]
    acc = [{"test_id": "T-1", "source_spec": "S", "evidence_link": "a/b.py::test_x", "test_status": "IN_PROGRESS"}]
    r0, r1 = tw.sheet_rows(req, acc, [{"id": "T-1", "result": "PASSED", "commit": "abc"}])
    assert r0["build_digest"] == tw.NA and r0["environment"] == tw.NA and r0["evidence_maturity"] == "E0" and r0["gate"] == "G0"
    assert r1["evidence_maturity"] == "E2" and r1["commit_sha"] == "abc" and r1["market"] == tw.NA
    assert set(r0) == set(tw.SHEET_FIELDS)


def test_a_pass_on_a_file_that_changed_is_expired_and_cannot_close_a_gate() -> None:
    acc = [{"test_id": f"T-{i}", "evidence_link": f"p{i}.py::t"} for i in range(4)] + [{"test_id": "T-9", "evidence_link": ""}]
    st = {s.test_id: s for s in tw.staleness(acc, {"p0.py": "a", "p1.py": "a", "p2.py": "a"}, {"p0.py": "a", "p1.py": "CHANGED", "p3.py": "x"})}
    assert st["T-0"].state == "CURRENT" and tw.valid_pass(st["T-0"])
    assert st["T-1"].state == "EXPIRED" and not tw.valid_pass(st["T-1"])
    assert st["T-2"].state == "MISSING" and st["T-3"].state == "UNRECORDED" and "T-9" not in st


def test_a_complete_manifest_passes_every_gate_and_an_empty_one_is_blocked_everywhere() -> None:
    assert tw.production_eligible(tw.assess(ready()))
    res = tw.assess(tw.Manifest(release_id="EMPTY"))
    assert not tw.production_eligible(res) and "NOT ASSESSED" in res[0].blockers[0]


def test_one_markets_approval_does_not_activate_another() -> None:
    uk = tw.assess(ready(market="UK"))
    assert not uk[0].passed and "approved scope" in " ".join(uk[0].blockers)
    assert not tw.production_eligible(uk)


def test_a_failing_condition_blocks_its_gate_and_every_later_one() -> None:
    res = tw.assess(ready(cross_tenant_failures=1))
    assert [g.passed for g in res] == [True, False, False, False, False]
    assert "earlier gate" in res[3].blockers[-1]


def test_nothing_without_a_build_digest_is_deployed_and_no_p0_passes_without_evidence() -> None:
    assert not tw.assess(ready(build_digest=None))[1].passed
    assert not tw.assess(ready(p0_without_evidence=2))[0].passed
    assert not tw.assess(ready(dtv_missing=19))[0].passed


def test_the_implementer_cannot_sign_and_one_person_cannot_hold_two_signing_roles() -> None:
    assert "implementer" in " ".join(tw.assess(ready(implementer="quinn"))[3].blockers)
    assert "more than one role" in " ".join(tw.assess(ready(signatures={**SIGN, "qa": "rita"}))[3].blockers)
    assert "has not signed" in " ".join(tw.assess(ready(signatures={}))[3].blockers)


def test_unsupported_claims_an_unrehearsed_rollback_expired_evidence_and_an_open_incident_each_block() -> None:
    assert not tw.assess(ready(unsupported_public_claims=1))[3].passed
    assert not tw.assess(ready(rollback_rehearsed=False))[3].passed
    assert not tw.assess(ready(evidence_expired=3))[1].passed
    assert not tw.assess(ready(open_material_incidents=1))[4].passed
    assert not tw.assess(ready(partner_e2e_signed_webhook=False))[2].passed


def test_the_manifest_example_in_the_document_is_blocked() -> None:
    m = tw.manifest_from_json({"release_id": "ZID-RC-SAMPLE-001", "scope": {"market": "US", "tenant_class": "design-partner", "provider": "UNASSIGNED"}, "build_digest": None})
    assert not tw.production_eligible(tw.assess(m))
