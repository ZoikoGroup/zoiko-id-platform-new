"""Authority history, signed status lists, disclosure-tier exports and the signed what-if (ZID-CP-WF08 v2.0 E02, E06, E09, E12; CR-170) against PostgreSQL."""

from __future__ import annotations

import asyncio
import uuid
from datetime import UTC, datetime, timedelta
from typing import Any

import pytest
from conftest import ctx, engine
from sqlalchemy import text
from test_evidence_pg import decide, fresh_runtime, receipt_of, seed_admin  # noqa: F401 - the fixture sets the evidence runtime
from zid_db import tenant_transaction
from zid_errors import ZidError
from zid_platform.modules.evidence.application import wf08_services as svc
from zid_platform.modules.evidence.application.runtime import verify_evidence
from zid_platform.modules.evidence.domain import status_list as sl
from zid_platform.modules.evidence.infrastructure import wf08_store
from zid_platform.modules.evidence.ports import worker_pass

D = "sha-256:" + "cd" * 32


def uid() -> str:
    return str(uuid.uuid4())


def edge(**over: Any) -> dict[str, Any]:
    now = datetime.now(UTC)
    base = {
        "edge_type": "GRANT", "edge_id": uid(), "version": 2, "subject": "agent-9", "state": "ACTIVE", "issuer": "owner", "obtained_at": now - timedelta(minutes=2),
        "effective_from": now - timedelta(days=1), "effective_until": None, "evidence_digest": D,
    }  # fmt: skip
    return {**base, **over}


def run(rt_url: str, tenant: str, fn: Any) -> Any:
    async def go() -> Any:
        async with engine(rt_url) as eng, tenant_transaction(eng, ctx(tenant)) as c:
            return await fn(c)

    return asyncio.run(go())


# ---------------------------------------------------------------------------------------------------------- authority history


def test_the_authority_history_is_written_once_signed_and_never_edited(
    rt_url: str, owner_url: str
) -> None:
    tenant = uid()
    at = datetime.now(UTC)

    async def record(c: Any, edges: list[dict[str, Any]], ref: str) -> dict[str, Any]:
        return await svc.record_authority_snapshot(
            c,
            tenant,
            decision_ref=ref,
            subject_ref="agent-9",
            decision_at=at,
            edges=edges,
            created_by=None,
        )

    complete = run(rt_url, tenant, lambda c: record(c, [edge()], "dec-1"))
    assert (
        complete["verdict"] == "COMPLETE"
        and complete["integrity"] == "VALID"
        and complete["body"]["edges"][0]["evidence_digest"] == D
    )
    claims = verify_evidence(
        complete["signature"], svc.SNAPSHOT_TYP
    )  # signed with the evidence key
    assert (
        claims["snapshot_digest"] == complete["snapshot_digest"]
        and claims["decision_ref"] == "dec-1"
    )

    again = run(
        rt_url, tenant, lambda c: record(c, [edge(state="REVOKED")], "dec-1")
    )  # a second call never replaces history
    assert (
        again["snapshot_digest"] == complete["snapshot_digest"] and again["verdict"] == "COMPLETE"
    )

    missing = run(rt_url, tenant, lambda c: record(c, [], "dec-2"))
    assert missing["verdict"] == "INDETERMINATE" and missing["reasons"] == ["MISSING_EDGE:GRANT"]
    contradictory = run(
        rt_url,
        tenant,
        lambda c: record(c, [edge(edge_id="g", version=1), edge(edge_id="g", version=2)], "dec-3"),
    )
    assert (
        contradictory["verdict"] == "INDETERMINATE"
        and "CONTRADICTORY_EDGES:GRANT:agent-9" in contradictory["reasons"]
    )

    async def tamper() -> None:
        async with engine(owner_url) as eng, eng.begin() as c:
            await c.execute(
                text(
                    "UPDATE evidence.authority_snapshot SET verdict = 'COMPLETE' WHERE decision_ref = 'dec-2'"
                )
            )

    with pytest.raises(Exception, match="append-only"):  # the database refuses, even for the owner
        asyncio.run(tamper())

    other = uid()
    assert (
        run(rt_url, other, lambda c: wf08_store.load_snapshot(c, "dec-1")) is None
    )  # another tenant cannot see it


# ------------------------------------------------------------------------------------------------------------- status lists


def test_a_status_list_hides_who_it_is_about_and_never_reads_stale_as_active(
    rt_url: str, owner_url: str
) -> None:
    tenant = uid()
    issuer, env = "zoiko-cell-1", "test"

    async def setup(c: Any) -> tuple[dict[str, Any], dict[str, Any]]:
        return await svc.allocate_slot(c, tenant, "revocation", "agent-a"), await svc.allocate_slot(
            c, tenant, "revocation", "agent-b"
        )

    a, b = run(rt_url, tenant, setup)
    assert (
        a["position"] != b["position"]
        and a["size_bits"] == sl.MIN_BITS
        and 0 <= a["position"] < sl.MIN_BITS
    )
    assert (
        run(rt_url, tenant, lambda c: svc.allocate_slot(c, tenant, "revocation", "agent-a")) == a
    )  # idempotent per subject

    async def publish(c: Any) -> dict[str, Any]:
        return await svc.publish_list(c, tenant, "revocation", issuer=issuer, environment=env)

    first = run(rt_url, tenant, publish)
    now = datetime.now(UTC)
    ok = timedelta(hours=1)
    assert (
        svc.check_published(
            first["jws"], a["position"], now=now, max_age=ok, issuer=issuer, environment=env
        )
        == "ACTIVE"
    )

    run(
        rt_url,
        tenant,
        lambda c: svc.change_status(
            c, tenant, "revocation", "agent-a", revoke=True, reason="key compromised", by=None
        ),
    )
    assert (
        svc.check_published(
            first["jws"], a["position"], now=now, max_age=ok, issuer=issuer, environment=env
        )
        == "ACTIVE"
    )  # an old list does not know yet
    second = run(rt_url, tenant, publish)
    assert second["version"] == first["version"] + 1
    assert (
        svc.check_published(
            second["jws"], a["position"], now=now, max_age=ok, issuer=issuer, environment=env
        )
        == "REVOKED"
    )
    assert (
        svc.check_published(
            second["jws"], b["position"], now=now, max_age=ok, issuer=issuer, environment=env
        )
        == "ACTIVE"
    )  # the other agent is untouched

    run(
        rt_url,
        tenant,
        lambda c: svc.change_status(
            c, tenant, "revocation", "agent-a", revoke=False, reason="cleared after review", by=None
        ),
    )
    third = run(rt_url, tenant, publish)
    assert (
        svc.check_published(
            third["jws"], a["position"], now=now, max_age=ok, issuer=issuer, environment=env
        )
        == "ACTIVE"
    )  # reinstated by its own recorded act

    later = now + timedelta(
        hours=3
    )  # a status outage: the list is old and its relying party's age policy says so
    assert (
        svc.check_published(
            third["jws"], a["position"], now=later, max_age=ok, issuer=issuer, environment=env
        )
        == "UNKNOWN"
    )
    assert (
        svc.check_published(
            third["jws"], a["position"], now=now, max_age=ok, issuer="other", environment=env
        )
        == "UNKNOWN"
    )
    assert (
        svc.check_published(
            third["jws"][:-5] + "AAAAA",
            a["position"],
            now=now,
            max_age=ok,
            issuer=issuer,
            environment=env,
        )
        == "UNKNOWN"
    )  # a forged list

    with pytest.raises(ZidError) as e:
        run(
            rt_url,
            tenant,
            lambda c: svc.change_status(
                c, tenant, "revocation", "nobody", revoke=True, reason="no such subject", by=None
            ),
        )
    assert e.value.code == "STATUS_SLOT_UNKNOWN"

    # the published list alone is readable with no tenant; the positions and who they belong to are not
    async def anonymous() -> tuple[str | None, int]:
        async with engine(rt_url) as eng, eng.begin() as c:
            lid = await run_owner_list_id(owner_url, tenant)
            jws = await wf08_store.public_publication(c, lid)
        async with engine(rt_url) as eng, eng.begin() as c:
            slots = (
                await c.execute(text("SELECT count(*) FROM evidence.status_slot"))
            ).scalar_one()
        return jws, int(slots)

    jws, slots = asyncio.run(anonymous())
    assert jws == third["jws"] and slots == 0


async def run_owner_list_id(owner_url: str, tenant: str) -> uuid.UUID:
    async with engine(owner_url) as eng, eng.begin() as c:
        return (
            await c.execute(
                text(
                    "SELECT list_id FROM evidence.status_list WHERE tenant_id = :t AND purpose = 'revocation'"
                ),
                {"t": uuid.UUID(tenant)},
            )
        ).scalar_one()


def test_changes_to_a_status_slot_are_append_only(rt_url: str, owner_url: str) -> None:
    tenant = uid()
    run(rt_url, tenant, lambda c: svc.allocate_slot(c, tenant, "suspension", "agent-z"))
    run(
        rt_url,
        tenant,
        lambda c: svc.change_status(
            c, tenant, "suspension", "agent-z", revoke=True, reason="paused for review", by=None
        ),
    )

    async def tamper() -> None:
        async with engine(owner_url) as eng, eng.begin() as c:
            await c.execute(
                text("DELETE FROM evidence.status_change WHERE tenant_id = :t"),
                {"t": uuid.UUID(tenant)},
            )

    with pytest.raises(Exception, match="append-only"):
        asyncio.run(tamper())


# ----------------------------------------------------------------------------------------------------------------- exports


def sealed_receipt(rt_url: str, owner_url: str) -> tuple[str, str, str, str]:
    tenant, admin, other = uid(), uid(), uid()
    out: dict[str, str] = {}

    async def go() -> None:
        async with engine(rt_url) as eng, tenant_transaction(eng, ctx(tenant)) as c:
            await seed_admin(c, tenant, admin, ["evidence.export"])
            d = await decide(c, tenant, admin, "evidence.export")
            out["receipt"] = str((await receipt_of(c, d.decision_id)).receipt_id)
        async with engine(owner_url) as eng, eng.begin() as c:
            await worker_pass(c, force_seal=True)

    asyncio.run(go())
    return tenant, admin, other, out["receipt"]


AUDITOR = {"name": "Big Four LLP", "kind": "AUDITOR", "contact": "audit@bigfour.test"}


def test_each_tier_discloses_a_different_amount_and_a_private_one_needs_a_named_recipient_and_a_second_person(
    rt_url: str, owner_url: str
) -> None:
    tenant, admin, other, receipt = sealed_receipt(rt_url, owner_url)

    def request(tier: str, **over: Any) -> Any:
        kw: dict[str, Any] = {
            "receipt_id": receipt,
            "tier": tier,
            "recipient": None,
            "purpose": "",
            "legal_basis": None,
            "retention_days": 30,
            "recipient_country": None,
            "tenant_country": "GB",
            "requested_by": admin,
        }
        return run(rt_url, tenant, lambda c: svc.request_export(c, tenant, **{**kw, **over}))

    # a private tier without its recipient, basis or purpose is refused, and so is an unauthenticated one
    for bad in (
        {"recipient": None},
        {"legal_basis": None},
        {"purpose": "x"},
        {"recipient_country": None},
    ):
        with pytest.raises(ZidError) as e:
            request(
                "AUDITOR_DOSSIER",
                **{
                    "recipient": AUDITOR,
                    "purpose": "Annual audit of access decisions",
                    "legal_basis": "AUDIT_ENGAGEMENT",
                    "recipient_country": "GB",
                    "retention_days": 90,
                    **bad,
                },
            )
        assert e.value.code == "EXPORT_TIER_REQUEST_REFUSED"
    with pytest.raises(ZidError):
        run(
            rt_url,
            tenant,
            lambda c: svc.request_export(
                c,
                tenant,
                receipt_id=receipt,
                tier="AUDITOR_DOSSIER",
                recipient=AUDITOR,
                purpose="Annual audit",
                legal_basis="AUDIT_ENGAGEMENT",
                retention_days=30,
                recipient_country="GB",
                tenant_country="GB",
                requested_by=admin,
                authenticated=False,
            ),
        )

    # public: no recipient, issued without a second person, shows almost nothing of the receipt
    pub = request("PUBLIC_MINIMAL")
    assert pub["needs_second_person"] is False
    issued = run(
        rt_url, tenant, lambda c: svc.issue_export(c, tenant, pub["request_id"], issued_by=admin)
    )
    view = issued["package"]["bundle"]["disclosure"]["view"]
    assert (
        set(view) <= {"receipt_id", "schema_version", "evidence_class", "decision_type"}
        and issued["package"]["bundle"]["record"] is None
    )
    assert (
        issued["request"]["state"] == "ISSUED"
        and verify_evidence(issued["manifest_jws"], svc.EXPORT_TYP)["package_digest"]
        == issued["request"]["package_digest"]
    )

    # auditor dossier: refused until a second person approves; the requester can never approve
    dossier = request(
        "AUDITOR_DOSSIER",
        recipient=AUDITOR,
        purpose="Annual audit of access decisions",
        legal_basis="AUDIT_ENGAGEMENT",
        recipient_country="GB",
        retention_days=90,
    )
    assert (
        dossier["needs_second_person"] is True
        and dossier["state"] == "REQUESTED"
        and dossier["cross_border"] is False
    )
    with pytest.raises(ZidError) as e:
        run(
            rt_url,
            tenant,
            lambda c: svc.issue_export(c, tenant, dossier["request_id"], issued_by=admin),
        )
    assert e.value.code == "EXPORT_SECOND_PERSON_REQUIRED"
    with pytest.raises(ZidError) as e:
        run(
            rt_url,
            tenant,
            lambda c: svc.decide_export(
                c, dossier["request_id"], approve=True, decided_by=admin, reason=None
            ),
        )
    assert e.value.code == "EXPORT_SECOND_PERSON_REQUIRED"
    with pytest.raises(ZidError) as e:
        run(
            rt_url,
            tenant,
            lambda c: svc.decide_export(
                c, dossier["request_id"], approve=False, decided_by=other, reason=" "
            ),
        )
    assert e.value.code == "EXPORT_REFUSAL_NEEDS_REASON"
    approved = run(
        rt_url,
        tenant,
        lambda c: svc.decide_export(
            c, dossier["request_id"], approve=True, decided_by=other, reason=None
        ),
    )
    assert approved["state"] == "APPROVED" and approved["decided_by"] == other
    full = run(
        rt_url,
        tenant,
        lambda c: svc.issue_export(c, tenant, dossier["request_id"], issued_by=admin),
    )
    assert (
        full["package"]["bundle"]["record"] is not None
        and full["manifest"]["withheld_fields"] == []
        and full["manifest"]["second_person"] == other
    )
    assert full["manifest"]["recipient"] == {
        "name": "Big Four LLP",
        "kind": "AUDITOR",
    } and "contact" not in str(
        full["manifest"]["recipient"]
    )  # the contact stays out of the signed manifest
    with pytest.raises(ZidError) as e:
        run(
            rt_url,
            tenant,
            lambda c: svc.issue_export(c, tenant, dossier["request_id"], issued_by=admin),
        )
    assert e.value.code == "EXPORT_ALREADY_ISSUED"

    # customer tier crossing a border needs a second person even though the tier alone would not
    foreign = request(
        "CUSTOMER_EVIDENCE",
        recipient={"name": "Acme Inc", "kind": "CUSTOMER", "contact": "legal@acme.test"},
        purpose="Customer dispute review",
        legal_basis="CONTRACT",
        recipient_country="US",
    )
    assert foreign["cross_border"] is True and foreign["needs_second_person"] is True
    domestic = request(
        "CUSTOMER_EVIDENCE",
        recipient={"name": "Acme Ltd", "kind": "CUSTOMER", "contact": "legal@acme.co.uk"},
        purpose="Customer dispute review",
        legal_basis="CONTRACT",
        recipient_country="GB",
    )
    assert domestic["needs_second_person"] is False
    got = run(
        rt_url,
        tenant,
        lambda c: svc.issue_export(c, tenant, domestic["request_id"], issued_by=admin),
    )
    assert (
        "actor_handle" in got["manifest"]["withheld_fields"]
        and "actor_handle" not in got["package"]["bundle"]["disclosure"]["view"]
    )

    # refused requests stay refused
    refused = run(
        rt_url,
        tenant,
        lambda c: svc.decide_export(
            c,
            foreign["request_id"],
            approve=False,
            decided_by=other,
            reason="no basis for this transfer",
        ),
    )
    assert refused["state"] == "REFUSED"
    with pytest.raises(ZidError) as e:
        run(
            rt_url,
            tenant,
            lambda c: svc.issue_export(c, tenant, foreign["request_id"], issued_by=admin),
        )
    assert e.value.code == "EXPORT_REQUEST_REFUSED"

    async def self_approve() -> (
        None
    ):  # the database keeps the two-person rule even if the service did not
        async with engine(owner_url) as eng, eng.begin() as c:
            await c.execute(
                text(
                    "UPDATE evidence.export_request SET decided_by = requested_by WHERE request_id = :i"
                ),
                {"i": uuid.UUID(domestic["request_id"])},
            )

    with pytest.raises(Exception, match="check"):
        asyncio.run(self_approve())
