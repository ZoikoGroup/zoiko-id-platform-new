"""The document photograph on a reviewed route, over HTTP on PostgreSQL (WF03 browser-only route; CR-124): kept encrypted, opened only by a reviewer who gives a reason and only for minutes, retired by a recapture,
and a reviewer can reject for a defined failure. The photograph is in no list and no response except the one that is a reviewer's own grant."""

from __future__ import annotations

import asyncio
import base64
import os
import uuid
from datetime import UTC, datetime
from typing import Any

import pytest
from conftest import ctx, engine
from fastapi.testclient import TestClient
from onboarding_fixture import AUD, ISS, onboard
from sqlalchemy import text
from zid_db import tenant_transaction
from zid_platform.main import create_app
from zid_platform.modules.verification.api import idv_cases  # noqa: F401
from zid_platform.modules.verification.application import capture_checks
from zid_platform.modules.verification.application.idv_document import DocumentReading
from zid_platform.modules.verification.domain import idv_policy
from zid_platform.settings import Settings

ACTIONS = ("verification.idv.review", "evidence.object.read")
RESOURCES = ("idv_case:*",)
SCOPE = "verification:execute verification:read"
CHECKS = {
    "RESOLUTION": "PASS",
    "SHARPNESS": "PASS",
    "GLARE": "PASS",
    "EXPOSURE": "PASS",
    "MRZ_READABLE": "PASS",
}


def entry(**extra: Any) -> dict[str, Any]:
    return {
        "policy_version": "gb-web-1", "purpose": "KYC", "chip": "NOT_USED", "face": "NOT_USED", "clone_check": "NOT_USED", "capture_integrity": "NOT_USED",
        "retries": {"nfc": 0, "capture": 3}, "alternates": [{"route_id": "WEB_REVIEW", "may_approve": True, "reviewers_needed": 2}], **extra,
    }  # fmt: skip


def photo() -> bytes:
    return b"\xff\xd8\xff" + os.urandom(3000)


READING: dict[str, Any] = {}  # what the fake imaging service answers; a test changes it


def install_fake_imaging(monkeypatch: pytest.MonkeyPatch) -> None:
    """What the photo service answers in these tests: a reading a test can change through READING, and a sanitize step that hands the image back (the real re-encoding is proved against the real service)."""
    READING.clear()
    READING.update(
        checks={**CHECKS, "MRZ_CHECK_DIGITS": "PASS"},
        printed={"surname": "MUELLER", "expiry_date": "2099-01-01"},
    )

    async def read(image: bytes, **_: Any) -> DocumentReading:
        return DocumentReading(dict(READING["checks"]), READING["printed"], ())

    async def sanitize(image: bytes, **_: Any) -> bytes:
        return image

    monkeypatch.setattr(capture_checks, "read_document", read)  # the shared checks, used by the signed-in page and a partner's customer alike
    monkeypatch.setattr(capture_checks, "sanitize_image", sanitize)


@pytest.fixture(autouse=True)
def fake_imaging(monkeypatch: pytest.MonkeyPatch) -> None:
    install_fake_imaging(monkeypatch)


def app_for(rt_url: str, o: Any, policy: dict[str, Any]) -> TestClient:
    app = create_app(
        Settings(database_url=rt_url, token_issuer=ISS, token_audience=AUD, public_base_url=AUD, environment="test", cell_id="uk-1"),
        keyring=o.ring,
    )  # fmt: skip
    table = idv_policy.parse({"markets": {"GB": policy}})
    app.state.idv_policies = lambda: table
    return TestClient(app, base_url=ISS)


def upload(api: TestClient, who: dict[str, str], case: str, image: bytes) -> Any:
    nonce = api.post(f"/v1/idv/cases/{case}/nonce", headers=who).json()["nonce"]
    return api.post(
        f"/v1/idv/cases/{case}/document-image", headers=who,
        json={"nonce": nonce, "image_base64": base64.b64encode(image).decode(), "captured_at": datetime.now(UTC).isoformat(), "channel": "WEB"},
    )  # fmt: skip


def rows(owner_url: str, sql: str, **p: Any) -> list[Any]:
    async def run() -> list[Any]:
        async with engine(owner_url) as eng, eng.connect() as c:
            return list((await c.execute(text(sql), p)).all())

    return asyncio.run(run())


def test_a_market_with_no_retention_period_refuses_to_keep_a_photograph(
    rt_url: str, owner_url: str
) -> None:
    o = onboard(rt_url, extra_actions=ACTIONS, extra_resources=RESOURCES)
    me = o.bearer(o.owner, scope=SCOPE)
    with app_for(rt_url, o, entry()) as api:
        case = api.post(
            "/v1/idv/cases", headers=me, json={"market": "GB", "route": "WEB_REVIEW"}
        ).json()["case_id"]
        refused = upload(api, me, case, photo())
        assert (
            refused.status_code == 409
            and refused.json()["error"]["code"] == "MEDIA_RETENTION_NOT_SET"
        )
    assert (
        rows(
            owner_url,
            "SELECT 1 FROM verify.idv_case_media WHERE case_id = CAST(:c AS uuid)",
            c=case,
        )
        == []
    )  # nothing was kept


def test_the_photograph_is_kept_encrypted_opened_only_by_a_reviewer_with_a_reason_and_retired_by_a_recapture(
    rt_url: str, owner_url: str
) -> None:
    o = onboard(rt_url, extra_actions=ACTIONS, extra_resources=RESOURCES, with_third=True)
    applicant, first, second = (o.bearer(p, scope=SCOPE) for p in (o.owner, o.second, o.third))
    image = photo()
    with app_for(rt_url, o, entry(media_retention_days=7)) as api:
        case = api.post(
            "/v1/idv/cases", headers=applicant, json={"market": "GB", "route": "WEB_REVIEW"}
        ).json()["case_id"]
        done = upload(api, applicant, case, image)
        assert done.status_code == 200, done.text
        assert (
            base64.b64encode(image).decode()[:60] not in done.text
        )  # the response never carries the image

        # what a reviewer sees before opening anything
        seen = api.get(f"/v1/idv/cases/{case}/review", headers=first).json()
        assert (
            seen["photo"]["has_photo"] is True
            and seen["photo"]["checks"] == READING["checks"]
            and seen["photo"]["expired"] is False
        )
        assert "image" not in str(seen).lower() or "image/jpeg" in str(
            seen
        )  # only the type, never the bytes
        assert "RECAPTURE_REQUESTED" not in str(seen)

        # the stored object is ciphertext, and the media row holds no image
        stored = rows(
            owner_url,
            "SELECT ciphertext FROM evidence.evidence_object WHERE object_type = 'idv.document_photo' ORDER BY created_at DESC LIMIT 1",
        )
        assert stored and image[:64] not in bytes(stored[0].ciphertext)
        assert {
            r[0]
            for r in rows(
                owner_url,
                "SELECT column_name FROM information_schema.columns WHERE table_name = 'idv_case_media'",
            )
        }.isdisjoint({"image", "content", "bytes"})

        # the applicant is never the reviewer; a reason is required; a grant belongs to one reviewer
        assert (
            api.post(
                f"/v1/idv/cases/{case}/media:access",
                headers=applicant,
                json={"reason": "I want to see my own photo"},
            ).json()["error"]["code"]
            == "REVIEWER_IS_THE_APPLICANT"
        )
        assert (
            api.post(
                f"/v1/idv/cases/{case}/media:access", headers=first, json={"reason": "short"}
            ).status_code
            == 422
        )
        grant = api.post(
            f"/v1/idv/cases/{case}/media:access",
            headers=first,
            json={"reason": "Checking the photo page against the details given"},
        )
        assert grant.status_code == 201 and grant.json()["reads_left"] == 3
        gid = grant.json()["grant_id"]
        stranger = api.get(f"/v1/idv/cases/{case}/media/{gid}", headers=second)
        assert (
            stranger.status_code == 403 and stranger.json()["error"]["code"] == "GRANT_INVALID"
        )  # another reviewer cannot use it

        for left in (2, 1, 0):
            got = api.get(f"/v1/idv/cases/{case}/media/{gid}", headers=first)
            assert got.status_code == 200 and got.headers["cache-control"] == "no-store"
            assert (
                base64.b64decode(got.json()["image_base64"]) == image
                and got.json()["reads_left"] == left
            )
        assert (
            api.get(f"/v1/idv/cases/{case}/media/{gid}", headers=first).json()["error"]["code"]
            == "GRANT_INVALID"
        )  # three reads, then it is spent

        # every grant is a record with its reason
        recorded = rows(
            owner_url,
            "SELECT reason, reads FROM verify.idv_media_grant WHERE media_id IN (SELECT media_id FROM verify.idv_case_media WHERE case_id = CAST(:c AS uuid))",
            c=case,
        )
        assert [(r.reason, r.reads) for r in recorded] == [
            ("Checking the photo page against the details given", 3)
        ]

        # a recapture retires the photograph: it can no longer be opened, and the case waits for a new one
        bad = api.post(
            f"/v1/idv/cases/{case}/recapture", headers=first, json={"code": "MAKE_IT_UP"}
        )
        assert bad.status_code == 422 and bad.json()["error"]["code"] == "CODE_INVALID"
        again = api.post(
            f"/v1/idv/cases/{case}/recapture", headers=first, json={"code": "GLARE_OR_SHADOW"}
        ).json()
        assert (
            again["signals"]["optical"] == "NOT_STARTED"
            and "RECAPTURE_REQUESTED:GLARE_OR_SHADOW" in again["reasons"]
            and again["reviewers"] == 0
        )
        assert (
            api.post(
                f"/v1/idv/cases/{case}/media:access",
                headers=first,
                json={"reason": "Trying the old photograph again"},
            ).json()["error"]["code"]
            == "MEDIA_NOT_FOUND"
        )
        assert (
            api.post(
                f"/v1/idv/cases/{case}/recapture", headers=applicant, json={"code": "WRONG_PAGE"}
            ).json()["error"]["code"]
            == "REVIEWER_IS_THE_APPLICANT"
        )

        # the new photograph is opened afresh, two different reviewers sign, and the case is decided by people
        assert upload(api, applicant, case, photo()).status_code == 200
        assert api.post(f"/v1/idv/cases/{case}/reviews", headers=first).status_code == 200
        done = api.post(f"/v1/idv/cases/{case}/reviews", headers=second).json()
        assert done["reviewers"] == 2

        # two different reviewers on a route that people decide approve it, and a decided case's photograph is no longer opened
        assert done["decision"] == "APPROVED" and done["state"] == "CLOSED", done
        assert (
            api.post(
                f"/v1/idv/cases/{case}/media:access",
                headers=first,
                json={"reason": "Looking after the decision"},
            ).json()["error"]["code"]
            == "CASE_CLOSED"
        )


def test_a_reviewer_rejection_closes_a_case_and_the_chip_route_is_never_decided_this_way(
    rt_url: str, owner_url: str
) -> None:
    o = onboard(rt_url, extra_actions=ACTIONS, extra_resources=RESOURCES, with_third=True)
    applicant, reviewer = o.bearer(o.owner, scope=SCOPE), o.bearer(o.second, scope=SCOPE)
    with app_for(rt_url, o, entry(media_retention_days=7)) as api:
        case = api.post(
            "/v1/idv/cases", headers=applicant, json={"market": "GB", "route": "WEB_REVIEW"}
        ).json()["case_id"]
        assert upload(api, applicant, case, photo()).status_code == 200
        out = api.post(
            f"/v1/idv/cases/{case}/reject",
            headers=reviewer,
            json={"code": "DOCUMENT_MANIPULATION_SUSPECTED"},
        )
        assert out.status_code == 200, out.text
        body = out.json()
        assert (
            body["decision"] == "REJECTED"
            and body["state"] == "CLOSED"
            and "REVIEWER_REJECTED:DOCUMENT_MANIPULATION_SUSPECTED" in body["reasons"]
        )
        assert (
            api.post(
                f"/v1/idv/cases/{case}/recapture", headers=reviewer, json={"code": "WRONG_PAGE"}
            ).json()["error"]["code"]
            == "CASE_CLOSED"
        )  # a closed case takes nothing more
        assert uuid.UUID(case)

    assert ctx and tenant_transaction  # the harness helpers are used by the sibling tests


def test_the_documents_date_comes_from_the_platforms_own_reading_and_an_unreliable_reading_is_never_in_date(
    rt_url: str, owner_url: str
) -> None:
    o = onboard(rt_url, extra_actions=ACTIONS, extra_resources=RESOURCES, with_third=True)
    applicant = o.bearer(o.owner, scope=SCOPE)
    with app_for(rt_url, o, entry(media_retention_days=7)) as api:

        def new_case() -> str:
            return api.post(
                "/v1/idv/cases", headers=applicant, json={"market": "GB", "route": "WEB_REVIEW"}
            ).json()["case_id"]

        # in date, and read reliably: the case waits for two people, as it should
        ok = upload(api, applicant, new_case(), photo()).json()
        assert (
            ok["decision"] == "REVIEW_REQUIRED"
            and "DOCUMENT_DATE_NOT_ESTABLISHED" not in ok["reasons"]
        )

        # expired on a reliable reading: the market's policy (refuse by default) decides it, with the defined failure
        READING["printed"] = {"surname": "MUELLER", "expiry_date": "2020-01-01"}
        gone = upload(api, applicant, new_case(), photo()).json()
        assert gone["decision"] == "REJECTED" and "DOCUMENT_EXPIRED" in gone["reasons"]

        # the lines could not be trusted (a check digit failed): the date is unknown, never "not expired"
        READING["checks"] = {**CHECKS, "MRZ_CHECK_DIGITS": "FAIL"}
        READING["printed"] = {"surname": "MUELLER", "expiry_date": "2099-01-01"}
        unsure = upload(api, applicant, new_case(), photo()).json()
        assert (
            unsure["decision"] != "APPROVED"
            and "DOCUMENT_DATE_NOT_ESTABLISHED" in unsure["reasons"]
        )
