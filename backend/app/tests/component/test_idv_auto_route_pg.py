"""The automatic browser route over HTTP on PostgreSQL (WF03; CR-126): the platform verifies when the applicant's typed details match its own reading of the document and every
check holds, rejects on a defined failure, and asks for a retake when the reading is not reliable. It keeps no photograph and leaves the lower grade."""

from __future__ import annotations

from typing import Any

from conftest import ctx  # noqa: F401
from onboarding_fixture import onboard
from test_idv_media_pg import (  # noqa: F401
    READING,
    app_for,
    entry,
    fake_imaging,
    photo,
    rows,
    upload,
)

SCOPE = "verification:execute verification:read"
GOOD: dict[str, Any] = {"surname": "Mueller", "given_names": "Erika", "document_number": "C01X00T47", "birth_date": "1985-03-12"}
PRINTED = {"surname": "MUELLER", "given_names": "ERIKA", "document_number": "C01X00T47", "birth_date": "1985-03-12", "expiry_date": "2099-01-01"}


def auto_entry() -> dict[str, Any]:
    return entry(result_valid_days=30, alternates=[{"route_id": "WEB_AUTO", "may_approve": True, "automatic": True}])


def run(api: Any, who: dict[str, str], declared: dict[str, Any] | None) -> Any:
    case = api.post("/v1/idv/cases", headers=who, json={"market": "GB", "route": "WEB_AUTO"}).json()["case_id"]
    nonce = api.post(f"/v1/idv/cases/{case}/nonce", headers=who).json()["nonce"]
    import base64
    from datetime import UTC, datetime

    body: dict[str, Any] = {"nonce": nonce, "image_base64": base64.b64encode(photo()).decode(), "captured_at": datetime.now(UTC).isoformat(), "channel": "WEB"}
    if declared is not None:
        body["declared"] = declared
    return case, api.post(f"/v1/idv/cases/{case}/document-image", headers=who, json=body)


def test_matching_details_are_verified_with_no_reviewer_no_photograph_and_the_lower_grade(rt_url: str, owner_url: str) -> None:
    o = onboard(rt_url)
    me = o.bearer(o.owner, scope=SCOPE)
    READING["printed"] = dict(PRINTED)
    with app_for(rt_url, o, auto_entry()) as api:
        case, done = run(api, me, GOOD)
        assert done.status_code == 200, done.text
        assert done.json()["decision"] == "APPROVED", done.json()
    assert rows(owner_url, "SELECT grade FROM verify.idv_case_claim WHERE case_id = CAST(:c AS uuid)", c=case)[0][0] == "AUTOMATED_NO_CHIP"
    assert rows(owner_url, "SELECT 1 FROM verify.idv_case_media WHERE case_id = CAST(:c AS uuid)", c=case) == []  # nothing kept
    assert "Mueller" not in str(rows(owner_url, "SELECT signals FROM verify.idv_case WHERE case_id = CAST(:c AS uuid)", c=case))  # nothing typed is stored


def test_details_that_differ_are_rejected_with_a_defined_reason(rt_url: str) -> None:
    o = onboard(rt_url)
    me = o.bearer(o.owner, scope=SCOPE)
    READING["printed"] = dict(PRINTED)
    with app_for(rt_url, o, auto_entry()) as api:
        _, done = run(api, me, {**GOOD, "document_number": "X99999999"})
        assert done.json()["decision"] == "REJECTED" and "DECLARED_DATA_DIFFERS_FROM_DOCUMENT" in done.json()["reasons"]


def test_no_typed_details_or_an_unreliable_reading_is_never_verified_or_accused(rt_url: str) -> None:
    o = onboard(rt_url)
    me = o.bearer(o.owner, scope=SCOPE)
    READING["printed"] = dict(PRINTED)
    with app_for(rt_url, o, auto_entry()) as api:
        _, none = run(api, me, None)
        assert none.json()["decision"] == "INDETERMINATE" and "DECLARED_DATA_NOT_COMPARED" in none.json()["reasons"]
        READING["checks"] = {**READING["checks"], "MRZ_CHECK_DIGITS": "FAIL"}
        _, bad = run(api, me, GOOD)
        assert bad.json()["decision"] == "INDETERMINATE" and "DOCUMENT_READING_NOT_RELIABLE" in bad.json()["reasons"]


def test_an_expired_document_is_rejected_by_the_markets_policy(rt_url: str) -> None:
    o = onboard(rt_url)
    me = o.bearer(o.owner, scope=SCOPE)
    READING["printed"] = {**PRINTED, "expiry_date": "2001-01-01"}
    with app_for(rt_url, o, auto_entry()) as api:
        _, done = run(api, me, GOOD)
        assert done.json()["decision"] == "REJECTED" and "DOCUMENT_EXPIRED" in done.json()["reasons"]
