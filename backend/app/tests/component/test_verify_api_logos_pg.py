"""A partner's logo (CR-166): uploaded and re-encoded, shown at once in the sandbox, operator-approved by digest for production, and shown to a customer only by their link."""

from __future__ import annotations

import asyncio
import base64
from typing import Any

import pytest
from test_idv_media_pg import fake_imaging as _fake_imaging  # noqa: F401  (autouse fixture)
from test_idv_media_pg import photo
from test_verify_api_go_live_pg import Company, code, operator_tx, uid
from test_verify_api_live_pg import Customer, fresh_limiter  # noqa: F401
from zid_platform.modules.verify_api.application import logo_service as svc
from zid_platform.modules.verify_api.domain import model as m


def b64(raw: bytes) -> str:
    return base64.b64encode(raw).decode()


def make_session(co: Company) -> tuple[str, dict[str, Any]]:
    server = co.sandbox_server()
    view = co.http.post(
        "/v1/verify-api/verification-sessions",
        headers={**server, "Idempotency-Key": uid()},
        json={"customer_reference": "c-1", "country": "GB", "purpose_code": "customer_onboarding"},
    ).json()
    app_id = co.http.get("/v1/verify-api/applications", headers=co.h).json()["items"][0][
        "application_id"
    ]
    return app_id, view


def test_a_sandbox_logo_shows_at_once_only_to_the_link_holder_and_bad_files_are_refused(
    rt_url: str, owner_url: str
) -> None:
    co = Company(rt_url)
    try:
        app_id, view = make_session(co)
        who = Customer(co, view)
        assert who.get().json()["logo"] is False and who.get("/logo").status_code == 404
        url = f"/v1/verify-api/applications/{app_id}/logo"
        for bad in ("not base64!!", b64(b"short"), b64(b"GIF89a" + b"x" * 500)):
            assert code(co.http.put(url, headers=co.h, json={"image_base64": bad})) in (
                "VALIDATION_FAILED",
            )
        done = co.http.put(url, headers=co.h, json={"image_base64": b64(photo())})
        assert (
            done.status_code == 200
            and done.json()["status"] == "APPROVED"
            and done.json()["shown"] is True
        )
        assert who.get().json()["logo"] is True
        img = who.get("/logo")
        assert (
            img.status_code == 200
            and img.headers["content-type"] == "image/jpeg"
            and img.headers["x-content-type-options"] == "nosniff"
        )
        assert co.http.get(url + "/image", headers=co.h).status_code == 200
        # a wrong link is the same plain 404, and removing the logo returns to the name as text
        assert co.http.get("/v1/verify-api/hosted/zvl_" + "a" * 40 + "/logo").status_code == 404
        assert co.http.delete(url, headers=co.h).json()["status"] == "NONE"
        assert who.get("/logo").status_code == 404 and who.get().json()["logo"] is False
    finally:
        co.close()


def test_a_production_logo_needs_an_operator_to_approve_that_exact_image_and_a_changed_image_goes_back(
    rt_url: str, owner_url: str
) -> None:
    co = Company(rt_url)
    try:
        app_id, _ = make_session(co)

        async def promote() -> None:
            from conftest import engine
            from sqlalchemy import text

            async with engine(owner_url) as eng, eng.begin() as c:
                await c.execute(
                    text(
                        "UPDATE verify.api_application SET environment = 'PRODUCTION' WHERE application_id = CAST(:a AS uuid)"
                    ),
                    {"a": app_id},
                )

        asyncio.run(promote())
        url = f"/v1/verify-api/applications/{app_id}/logo"
        first = co.http.put(url, headers=co.h, json={"image_base64": b64(photo())}).json()
        assert first["status"] == "PENDING" and first["shown"] is False

        async def operate(digest: str, approve: bool, note: str | None = None) -> str:
            async with operator_tx(rt_url) as c:
                pending = await svc.operator_pending(c)
                assert any(
                    p["application_id"] == app_id and p["digest"] == digest and p["image_base64"]
                    for p in pending
                )
                return (await svc.operator_decide(c, app_id, digest, approve, uid(), note))[
                    "status"
                ]

        async def refuse(digest: str) -> str:
            async with operator_tx(rt_url) as c:
                with pytest.raises(m.PartnerError) as e:
                    await svc.operator_decide(c, app_id, digest, True, uid(), None)
                return e.value.code

        # the operator looked at the first image; the partner swaps it before the decision lands
        second = co.http.put(url, headers=co.h, json={"image_base64": b64(photo())}).json()
        assert second["digest"] != first["digest"]
        assert asyncio.run(refuse(first["digest"])) == "LOGO_CHANGED"
        assert asyncio.run(operate(second["digest"], True)) == "APPROVED"
        assert co.http.get(url, headers=co.h).json()["shown"] is True
        # a new upload is pending again and not shown until approved
        third = co.http.put(url, headers=co.h, json={"image_base64": b64(photo())}).json()
        assert third["status"] == "PENDING" and third["shown"] is False
        assert (
            asyncio.run(operate(third["digest"], False, "This is another company's logo."))
            == "REJECTED"
        )
        assert co.http.get(url, headers=co.h).json()["note"] == "This is another company's logo."
    finally:
        co.close()
