"""The reviewed route's remaining gaps, over HTTP on PostgreSQL (WF03; CR-125): an approval leaves a claim that the rest of the platform reads, uploads are scanned and cleaned, the queue and the deadline,
a reviewer's attestation of the date, nothing of the printed data kept without a chip, and rights requests that find, export and retire these cases."""

from __future__ import annotations

import asyncio
import base64
import uuid
from datetime import UTC, datetime
from types import SimpleNamespace
from typing import Any

import pytest
from conftest import ctx, engine
from onboarding_fixture import onboard
from sqlalchemy import text
from test_idv_media_pg import (
    ACTIONS,
    READING,
    RESOURCES,
    SCOPE,
    app_for,
    entry,
    install_fake_imaging,
    photo,
    rows,
    upload,
)
from zid_db import tenant_transaction
from zid_platform.modules.verification import ports as verification_ports
from zid_platform.modules.verification.api import idv_cases
from zid_platform.modules.verification.infrastructure import privacy_adapter as pa
from zid_platform.modules.verification.infrastructure.image_inspect import ImagingUnavailable


@pytest.fixture(autouse=True)
def _imaging(monkeypatch: pytest.MonkeyPatch) -> None:
    install_fake_imaging(monkeypatch)


EICAR = b"X5O!P%@AP[4\\PZX54(P^)7CC)7}$EICAR-STANDARD-ANTIVIRUS-TEST-FILE!$H+H*"


def people(rt_url: str) -> tuple[Any, dict[str, str]]:
    o = onboard(rt_url, extra_actions=ACTIONS, extra_resources=RESOURCES, with_third=True)
    return o, {
        "applicant": o.bearer(o.owner, scope=SCOPE),
        "first": o.bearer(o.second, scope=SCOPE),
        "second": o.bearer(o.third, scope=SCOPE),
    }


def new_case(api: Any, who: dict[str, str]) -> str:
    return api.post(
        "/v1/idv/cases", headers=who["applicant"], json={"market": "GB", "route": "WEB_REVIEW"}
    ).json()["case_id"]


def at(rt_url: str, tenant: str, fn: Any) -> Any:
    async def run() -> Any:
        async with engine(rt_url) as eng, tenant_transaction(eng, ctx(tenant)) as c:
            return await fn(c)

    return asyncio.run(run())


def test_an_approval_leaves_a_claim_the_rest_of_the_platform_reads_and_without_a_validity_period_it_leaves_none(
    rt_url: str, owner_url: str
) -> None:
    o, who = people(rt_url)
    with app_for(rt_url, o, entry(media_retention_days=7, result_valid_days=30)) as api:
        case = new_case(api, who)
        assert upload(api, who["applicant"], case, photo()).status_code == 200
        api.post(f"/v1/idv/cases/{case}/reviews", headers=who["first"])
        done = api.post(f"/v1/idv/cases/{case}/reviews", headers=who["second"]).json()
        assert (
            done["decision"] == "APPROVED"
            and "NO_CLAIM_ISSUED:RESULT_VALIDITY_NOT_SET" not in done["reasons"]
        )
        claim = api.get(f"/v1/idv/cases/{case}/review", headers=who["first"]).json()["claim"]
        assert (
            claim["grade"] == "REVIEWED_NO_CHIP"
            and claim["status"] == "VALID"
            and claim["market"] == "GB"
        )  # never a chip grade

    now = datetime.now(UTC)
    got = at(rt_url, o.tenant, lambda c: verification_ports.current_identity_claim(c, o.owner, now))
    assert got is not None and got[0] == "IDENTITY_VERIFIED_FOR_SCOPE"  # what screening rests on
    assert (
        at(rt_url, o.tenant, lambda c: verification_ports.person_assurance_current(c, o.owner, now))
        is True
    )  # and what the company grade reads
    assert (
        at(rt_url, o.tenant, lambda c: verification_ports.current_identity_claim(c, o.second, now))
        is None
    )  # nobody else got one

    # a market that sets no validity period says so, and leaves no claim
    o2, who2 = people(rt_url)
    with app_for(rt_url, o2, entry(media_retention_days=7)) as api:
        case = new_case(api, who2)
        assert upload(api, who2["applicant"], case, photo()).status_code == 200
        api.post(f"/v1/idv/cases/{case}/reviews", headers=who2["first"])
        done = api.post(f"/v1/idv/cases/{case}/reviews", headers=who2["second"]).json()
        assert (
            done["decision"] == "APPROVED"
            and "NO_CLAIM_ISSUED:RESULT_VALIDITY_NOT_SET" in done["reasons"]
        )
        assert (
            api.get(f"/v1/idv/cases/{case}/review", headers=who2["first"]).json()["claim"] is None
        )
    assert (
        at(rt_url, o2.tenant, lambda c: verification_ports.current_identity_claim(c, o2.owner, now))
        is None
    )


def test_an_upload_is_scanned_and_a_scan_that_cannot_run_refuses_rather_than_passes(
    rt_url: str, owner_url: str
) -> None:
    o, who = people(rt_url)
    with app_for(rt_url, o, entry(media_retention_days=7)) as api:
        case = new_case(api, who)
        bad = upload(api, who["applicant"], case, b"\xff\xd8\xff" + EICAR)
        assert bad.status_code == 422 and bad.json()["error"]["code"] == "IMAGE_REFUSED"
        api.app.state.evidence_scanner = (
            None  # a deployed cell with no scanner never pretends to scan
        )
        none = upload(api, who["applicant"], case, photo())
        assert none.status_code == 503 and none.json()["error"]["code"] == "SCAN_UNAVAILABLE"
    assert (
        rows(
            owner_url,
            "SELECT 1 FROM verify.idv_case_media WHERE case_id = CAST(:c AS uuid)",
            c=case,
        )
        == []
    )  # nothing was kept from either


def test_if_the_photo_service_cannot_clean_the_image_nothing_is_kept_or_read(
    rt_url: str, owner_url: str, monkeypatch: pytest.MonkeyPatch
) -> None:
    o, who = people(rt_url)

    async def down(image: bytes, **_: Any) -> bytes:
        raise ImagingUnavailable("IMAGING_NOT_CONFIGURED")

    monkeypatch.setattr(idv_cases, "sanitize_image", down)
    with app_for(rt_url, o, entry(media_retention_days=7)) as api:
        case = new_case(api, who)
        out = upload(api, who["applicant"], case, photo())
        assert out.status_code == 503 and out.json()["error"]["code"] == "IMAGING_NOT_CONFIGURED"
    assert (
        rows(
            owner_url,
            "SELECT 1 FROM verify.idv_case_media WHERE case_id = CAST(:c AS uuid)",
            c=case,
        )
        == []
    )


def test_nothing_of_the_printed_data_is_kept_on_a_route_with_no_chip(
    rt_url: str, owner_url: str
) -> None:
    o, who = people(rt_url)
    with app_for(rt_url, o, entry(media_retention_days=7)) as api:
        case = new_case(api, who)
        assert upload(api, who["applicant"], case, photo()).status_code == 200
    kept = rows(
        owner_url,
        "SELECT printed_salt, printed_digests FROM verify.idv_case WHERE case_id = CAST(:c AS uuid)",
        c=case,
    )
    assert (
        kept[0].printed_salt is None and kept[0].printed_digests is None
    )  # no chip to compare with, so no digest of the name or number is kept


def test_a_reviewer_attests_the_date_only_when_the_platform_could_not_read_it(
    rt_url: str, owner_url: str
) -> None:
    o, who = people(rt_url)
    with app_for(rt_url, o, entry(media_retention_days=7)) as api:
        READING["printed"] = None  # a document with no machine-readable lines
        case = new_case(api, who)
        out = upload(api, who["applicant"], case, photo()).json()
        assert (
            "DOCUMENT_DATE_NOT_ESTABLISHED" in out["reasons"]
            and out["decision"]
            == "REVIEW_REQUIRED"  # people must look, and the date must still be established before it can be approved
        )
        assert (
            api.get(f"/v1/idv/cases/{case}/review", headers=who["first"]).json()[
                "document_date_known"
            ]
            is False
        )

        assert (
            api.post(
                f"/v1/idv/cases/{case}/attest-date",
                headers=who["applicant"],
                json={"expired": False},
            ).json()["error"]["code"]
            == "REVIEWER_IS_THE_APPLICANT"
        )
        done = api.post(
            f"/v1/idv/cases/{case}/attest-date", headers=who["first"], json={"expired": False}
        ).json()
        assert (
            "DATE_ATTESTED_BY_REVIEWER" in done["reasons"]
            and "DOCUMENT_DATE_NOT_ESTABLISHED" not in done["reasons"]
            and done["decision"] == "REVIEW_REQUIRED"
        )
        assert (
            api.get(f"/v1/idv/cases/{case}/review", headers=who["first"]).json()[
                "document_date_known"
            ]
            is True
        )
        again = api.post(
            f"/v1/idv/cases/{case}/attest-date", headers=who["second"], json={"expired": True}
        )
        assert (
            again.status_code == 409 and again.json()["error"]["code"] == "DATE_ALREADY_ESTABLISHED"
        )  # once established nobody changes it

        # an attestation that it has expired is decided by the market's policy, like a reading
        READING["printed"] = None
        other = new_case(api, who)
        upload(api, who["applicant"], other, photo())
        assert (
            api.post(
                f"/v1/idv/cases/{other}/attest-date", headers=who["first"], json={"expired": True}
            ).json()["decision"]
            == "REJECTED"
        )


def test_the_queue_lists_what_waits_for_this_reviewer_and_says_when_it_falls_due(
    rt_url: str, owner_url: str
) -> None:
    o, who = people(rt_url)
    with app_for(rt_url, o, entry(media_retention_days=7, review_sla_hours=24)) as api:
        case = new_case(api, who)
        assert upload(api, who["applicant"], case, photo()).status_code == 200
        assert (
            api.get("/v1/idv/review-queue", headers=who["applicant"]).json()["items"] == []
        )  # never their own case
        mine = api.get("/v1/idv/review-queue", headers=who["first"]).json()
        item = next(i for i in mine["items"] if i["case_id"] == case)
        assert (
            item["has_photo"] is True
            and item["signatures"] == 0
            and item["overdue"] is False
            and item["due_at"] is not None
            and item["waiting_hours"] == 0
        )
        api.post(f"/v1/idv/cases/{case}/reviews", headers=who["first"])
        assert all(
            i["case_id"] != case
            for i in api.get("/v1/idv/review-queue", headers=who["first"]).json()["items"]
        )  # not one they already signed
        assert any(
            i["case_id"] == case and i["signatures"] == 1
            for i in api.get("/v1/idv/review-queue", headers=who["second"]).json()["items"]
        )


def test_rights_requests_find_export_and_retire_these_cases(rt_url: str, owner_url: str) -> None:
    o, who = people(rt_url)
    with app_for(rt_url, o, entry(media_retention_days=7, result_valid_days=30)) as api:
        case = new_case(api, who)
        assert upload(api, who["applicant"], case, photo()).status_code == 200
        api.post(f"/v1/idv/cases/{case}/reviews", headers=who["first"])
        api.post(f"/v1/idv/cases/{case}/reviews", headers=who["second"])

    now = datetime.now(UTC)
    found = at(rt_url, o.tenant, lambda c: pa._locate_idv(c, o.owner))
    assert [f["object_ref"] for f in found] == [case] and found[0][
        "object_type"
    ] == "verification.idv_case"
    assert (
        at(rt_url, o.tenant, lambda c: pa._locate_idv(c, str(uuid.uuid4()))) == []
    )  # a stranger has nothing here

    entry_ = SimpleNamespace(object_ref=case)
    shown = at(rt_url, o.tenant, lambda c: pa._export_idv(c, entry_))
    assert (
        shown["decision"] == "APPROVED"
        and shown["photograph"]["retired"] is False
        and shown["claim"]["grade"] == "REVIEWED_NO_CHIP"
        and shown["claim"]["status"] == "VALID"
    )
    assert base64.b64encode(b"\xff\xd8\xff")[:4].decode() not in str(shown)  # never the image

    assert (
        at(rt_url, o.tenant, lambda c: verification_ports.current_identity_claim(c, o.owner, now))
        is not None
    )
    at(rt_url, o.tenant, lambda c: pa._dispose_idv(c, entry_, "ERASE"))
    at(rt_url, o.tenant, lambda c: pa._dispose_idv(c, entry_, "ERASE"))  # idempotent
    assert (
        at(rt_url, o.tenant, lambda c: verification_ports.current_identity_claim(c, o.owner, now))
        is None
    )  # nobody can rely on it any more
    gone = at(rt_url, o.tenant, lambda c: pa._export_idv(c, entry_))
    assert gone["photograph"]["retired"] is True and gone["claim"]["status"] == "REVOKED"
    assert text and uuid
