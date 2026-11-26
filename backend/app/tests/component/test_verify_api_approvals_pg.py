"""Two-person approval of a production credential (CR-162), against the real database.

One administrator asks, a different one approves, and the one who asked spends the approval once, within a day, on exactly what it names. A sandbox application needs none, and one company never sees another's.
"""

from __future__ import annotations

import asyncio
import uuid
from datetime import UTC, datetime, timedelta
from typing import Any

import pytest
from conftest import ctx, engine
from sqlalchemy import text
from test_verify_api_pg import make_app, uid
from zid_crypto import LocalSigner
from zid_db import tenant_transaction
from zid_platform.modules.verify_api.application import approvals_service as appr
from zid_platform.modules.verify_api.application import service
from zid_platform.modules.verify_api.domain import model as m


def tx(eng: Any, tenant: str) -> Any:
    return tenant_transaction(eng, ctx(tenant))


async def production_app(
    eng: Any, owner_url: str, tenant: str, name: str = "Live app"
) -> dict[str, Any]:
    app = await make_app(eng, tenant, name)
    async with (
        engine(owner_url) as oeng,
        oeng.begin() as c,
    ):  # a company reaches production only through the go-live gate; here the row is moved directly
        await c.execute(
            text(
                "UPDATE verify.api_application SET environment = 'PRODUCTION' WHERE application_id = :a"
            ),
            {"a": uuid.UUID(app["application_id"])},
        )
    return app


async def refused(coro: Any) -> str:
    with pytest.raises(m.PartnerError) as e:
        await coro
    return e.value.code


def test_a_sandbox_application_needs_no_approval_and_cannot_ask_for_one(
    rt_url: str, owner_url: str
) -> None:
    tenant, op1 = uid(), uid()

    async def run() -> None:
        async with engine(rt_url) as eng:
            app = await make_app(eng, tenant)
            async with tx(eng, tenant) as t:
                made = await service.issue_credential(
                    t, tenant, op1, app["application_id"]
                )  # no approval, no refusal
                assert made["client_secret"].startswith("zps_")
            async with tx(eng, tenant) as t:
                assert (
                    await refused(
                        appr.request(t, tenant, op1, app["application_id"], {"action": "ISSUE"})
                    )
                    == "VALIDATION_FAILED"
                )

    asyncio.run(run())


def test_a_production_credential_is_made_only_with_an_approval_a_different_person_gave_and_it_is_spent_once(
    rt_url: str, owner_url: str
) -> None:
    tenant, op1, op2 = uid(), uid(), uid()

    async def run() -> None:
        async with engine(rt_url) as eng:
            app = await production_app(eng, owner_url, tenant)
            aid = app["application_id"]
            async with tx(eng, tenant) as t:
                assert (
                    await refused(service.issue_credential(t, tenant, op1, aid))
                    == "APPROVAL_REQUIRED"
                )
            async with tx(eng, tenant) as t:
                assert (
                    await refused(
                        service.issue_credential(t, tenant, op1, aid, approval_id="not-an-id")
                    )
                    == "APPROVAL_REQUIRED"
                )
            async with tx(eng, tenant) as t:
                asked = await appr.request(t, tenant, op1, aid, {"action": "ISSUE"})
            assert asked["status"] == "PENDING" and asked["with_key"] is False
            pid = asked["approval_id"]
            async with tx(eng, tenant) as t:  # waiting is not approval
                assert (
                    await refused(service.issue_credential(t, tenant, op1, aid, approval_id=pid))
                    == "APPROVAL_REQUIRED"
                )
            async with tx(eng, tenant) as t:  # the person who asked cannot decide it
                assert await refused(appr.approve(t, pid, op1, None)) == "APPROVAL_SELF_DECISION"
            async with tx(eng, tenant) as t:
                done = await appr.approve(t, pid, op2, "Looks right.")
                assert done["status"] == "APPROVED" and done["decided_by"] == op2
            async with tx(eng, tenant) as t:  # the approver cannot spend it: it is the requester's
                assert (
                    await refused(service.issue_credential(t, tenant, op2, aid, approval_id=pid))
                    == "APPROVAL_REQUIRED"
                )
            async with tx(eng, tenant) as t:
                made = await service.issue_credential(t, tenant, op1, aid, approval_id=pid)
                assert made["client_secret"].startswith("zps_") and made["auth_method"] == "SECRET"
            async with tx(eng, tenant) as t:  # spent: never again
                assert (
                    await refused(service.issue_credential(t, tenant, op1, aid, approval_id=pid))
                    == "APPROVAL_REQUIRED"
                )
            async with tx(eng, tenant) as t:
                assert (await appr.list_view(t))["items"][0]["status"] == "CONSUMED"

    asyncio.run(run())


def test_an_approval_is_for_one_application_one_key_and_one_credential_and_goes_stale_after_a_day(
    rt_url: str, owner_url: str
) -> None:
    tenant, op1, op2 = uid(), uid(), uid()
    k1, k2 = LocalSigner("key-one").public_jwk(), LocalSigner("key-two").public_jwk()
    k3 = LocalSigner("key-three").public_jwk()

    async def approved(eng: Any, aid: str, body: dict[str, Any]) -> str:
        async with tx(eng, tenant) as t:
            asked = await appr.request(t, tenant, op1, aid, body)
        async with tx(eng, tenant) as t:
            await appr.approve(t, asked["approval_id"], op2, None)
        return str(asked["approval_id"])

    async def run() -> None:
        async with engine(rt_url) as eng:
            app, other = await production_app(eng, owner_url, tenant), None
            aid = app["application_id"]
            other = await production_app(
                eng, owner_url, tenant, "Second live app"
            )  # a second production application of the same company

            # a key approval is for that key: another key, or no key, is refused, and the approval stays unspent
            pid = await approved(eng, aid, {"action": "ISSUE", "public_jwk": k1})
            async with tx(eng, tenant) as t:
                assert (
                    await refused(
                        service.issue_credential(
                            t, tenant, op1, aid, public_jwk=k2, approval_id=pid
                        )
                    )
                    == "APPROVAL_REQUIRED"
                )
            async with tx(eng, tenant) as t:
                assert (
                    await refused(service.issue_credential(t, tenant, op1, aid, approval_id=pid))
                    == "APPROVAL_REQUIRED"
                )
            async with tx(eng, tenant) as t:
                assert (
                    await refused(
                        service.issue_credential(
                            t, tenant, op1, other["application_id"], public_jwk=k1, approval_id=pid
                        )
                    )
                    == "APPROVAL_REQUIRED"
                )  # nor is it for another application
            async with tx(eng, tenant) as t:
                keyed = await service.issue_credential(
                    t, tenant, op1, aid, public_jwk=k1, approval_id=pid
                )
                assert (
                    keyed["auth_method"] == "PRIVATE_KEY_JWT"
                    and keyed["key_id"] == "key-one"
                    and "client_secret" not in keyed
                )

            # a rotation approval names its credential; it cannot rotate a different one
            second = await approved(eng, aid, {"action": "ISSUE", "public_jwk": k3})
            async with tx(eng, tenant) as t:
                other_cred = await service.issue_credential(
                    t, tenant, op1, aid, public_jwk=k3, approval_id=second
                )  # a second key credential, so a rotation to a key is a valid request for it too
            rot = await approved(
                eng,
                aid,
                {"action": "ROTATE", "credential_id": keyed["credential_id"], "public_jwk": k2},
            )
            async with tx(eng, tenant) as t:
                assert (
                    await refused(
                        service.rotate_credential(
                            t,
                            tenant,
                            op1,
                            aid,
                            other_cred["credential_id"],
                            public_jwk=k2,
                            approval_id=rot,
                        )
                    )
                    == "APPROVAL_REQUIRED"
                )
            async with tx(eng, tenant) as t:
                rotated = await service.rotate_credential(
                    t, tenant, op1, aid, keyed["credential_id"], public_jwk=k2, approval_id=rot
                )
                assert (
                    rotated["key_id"] == "key-two" and rotated["replaces"] == keyed["credential_id"]
                )
            async with tx(eng, tenant) as t:  # an ISSUE approval cannot be spent on a rotation
                issue_only = await appr.request(t, tenant, op1, aid, {"action": "ISSUE"})
            async with tx(eng, tenant) as t:
                await appr.approve(t, issue_only["approval_id"], op2, None)
            async with tx(eng, tenant) as t:
                assert (
                    await refused(
                        service.rotate_credential(
                            t,
                            tenant,
                            op1,
                            aid,
                            other_cred["credential_id"],
                            public_jwk=k3,
                            approval_id=issue_only["approval_id"],
                        )
                    )
                    == "APPROVAL_REQUIRED"
                )

            # a day later an approved approval is stale, and a request that waited a day cannot be decided
            stale = await approved(eng, aid, {"action": "ISSUE"})
            async with tx(eng, tenant) as t:
                late = datetime.now(UTC) + timedelta(hours=25)
                assert (
                    await refused(
                        service.issue_credential(t, tenant, op1, aid, approval_id=stale, now=late)
                    )
                    == "APPROVAL_REQUIRED"
                )
            async with tx(eng, tenant) as t:
                waiting = await appr.request(t, tenant, op1, aid, {"action": "ISSUE"})
            async with tx(eng, tenant) as t:
                assert (
                    await refused(
                        appr.approve(
                            t,
                            waiting["approval_id"],
                            op2,
                            None,
                            now=datetime.now(UTC) + timedelta(hours=25),
                        )
                    )
                    == "APPROVAL_STATE_INVALID"
                )
            async with tx(eng, tenant) as t:
                listed = {i["approval_id"]: i["status"] for i in (await appr.list_view(t))["items"]}
                assert (
                    listed[waiting["approval_id"]] == "PENDING"
                )  # still waiting in the table; shown as expired by the view only after its time

    asyncio.run(run())


def test_a_request_can_be_rejected_with_a_reason_or_taken_back_by_the_one_who_asked_and_another_company_sees_none_of_it(
    rt_url: str, owner_url: str
) -> None:
    tenant, stranger, op1, op2 = uid(), uid(), uid(), uid()

    async def run() -> None:
        async with engine(rt_url) as eng:
            app = await production_app(eng, owner_url, tenant)
            aid = app["application_id"]
            async with tx(eng, tenant) as t:
                one = await appr.request(t, tenant, op1, aid, {"action": "ISSUE"})
            async with tx(eng, tenant) as t:
                assert (
                    await refused(appr.reject(t, one["approval_id"], op2, " ")) == "NOTE_REQUIRED"
                )
            async with tx(eng, tenant) as t:
                assert (
                    await refused(appr.reject(t, one["approval_id"], op1, "No."))
                    == "APPROVAL_SELF_DECISION"
                )
            async with tx(eng, tenant) as t:
                assert (await appr.reject(t, one["approval_id"], op2, "Not needed now."))[
                    "status"
                ] == "REJECTED"
            async with tx(eng, tenant) as t:
                assert (
                    await refused(appr.approve(t, one["approval_id"], op2, None))
                    == "APPROVAL_STATE_INVALID"
                )  # a closed request stays closed
            async with tx(eng, tenant) as t:
                two = await appr.request(t, tenant, op1, aid, {"action": "ISSUE"})
            async with tx(eng, tenant) as t:
                assert (
                    await refused(appr.cancel(t, two["approval_id"], op2)) == "NOT_FOUND"
                )  # only the requester takes it back
            async with tx(eng, tenant) as t:
                assert (await appr.cancel(t, two["approval_id"], op1))["status"] == "CANCELED"
            # another company can neither see an approval nor decide one
            async with tx(eng, stranger) as t:
                assert (await appr.list_view(t))["items"] == []
                assert await refused(appr.approve(t, two["approval_id"], op2, None)) == "NOT_FOUND"
            # a rotation must name a credential that exists, and a new credential must not name one
            async with tx(eng, tenant) as t:
                assert (
                    await refused(
                        appr.request(
                            t,
                            tenant,
                            op1,
                            aid,
                            {"action": "ROTATE", "credential_id": str(uuid.uuid4())},
                        )
                    )
                    == "NOT_FOUND"
                )
            async with tx(eng, tenant) as t:
                assert (
                    await refused(appr.request(t, tenant, op1, aid, {"action": "ROTATE"}))
                    == "VALIDATION_FAILED"
                )
            async with tx(eng, tenant) as t:
                assert (
                    await refused(
                        appr.request(
                            t,
                            tenant,
                            op1,
                            aid,
                            {"action": "ISSUE", "credential_id": str(uuid.uuid4())},
                        )
                    )
                    == "VALIDATION_FAILED"
                )

    asyncio.run(run())
