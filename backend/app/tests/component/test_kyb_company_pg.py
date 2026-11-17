"""The stored company grade and mandates on PostgreSQL (WF02 K05, K08; CR-109; acceptance T14, T15, T16, T22, T33).

K0 with nothing, K2 from a register match, K3 once ownership is settled with verified owners and screening is clear, K4 once a verified person holds a mandate, back to K3 when it is revoked, and
lower still when time alone passes: the grade is worked out again from the stored facts when asked."""

from __future__ import annotations

import asyncio
import uuid
from datetime import UTC, datetime, timedelta
from typing import Any

import pytest
from conftest import ctx, engine
from sqlalchemy import text
from sqlalchemy.exc import DBAPIError
from zid_crypto import KeyRing, LocalSigner
from zid_db import tenant_transaction
from zid_platform.modules.organization.application import kyb as kyb_service
from zid_platform.modules.organization.application import kyb_assessment as assessment
from zid_platform.modules.organization.application import kyb_company as company
from zid_platform.modules.organization.application import ownership_service as ownership
from zid_platform.modules.organization.domain import kyb_grade as g
from zid_platform.modules.organization.domain.kyb import (
    Applicant,
    Capabilities,
    CompanyFacts,
    Policy,
    Route,
    Routing,
    SourceKind,
)
from zid_platform.modules.organization.domain.kyb_mandate import MandateError

NOW = datetime.now(UTC).replace(microsecond=0)
SIGNER = LocalSigner("kyb-company-test")
RING = KeyRing(SIGNER)
ROUTING = Routing(
    "r1",
    Policy("p1", timedelta(days=365)),
    {"GB": Route(("companies-house",), None, ())},
    {"companies-house": "ENABLED"},
    {},
)


def uid() -> str:
    return str(uuid.uuid4())


def sign(body: dict[str, Any]) -> str:
    return RING.sign(body, typ="zid-kyb-evidence+jwt")


class Register:
    def __init__(self, observed: datetime) -> None:
        self.adapter_id, self.kind = "companies-house", SourceKind.OFFICIAL_REGISTER
        self.capabilities = Capabilities(frozenset({"GB"}))
        self.observed = observed

    async def search(self, query: str, jurisdiction: str, now: datetime) -> list[Any]:
        return []

    async def get(self, number: str, country: str, now: datetime) -> CompanyFacts:
        return CompanyFacts(
            "companies-house", SourceKind.OFFICIAL_REGISTER, "GB", "companies-house", number, "Acme Widgets Limited", "ltd", "active", True,
            "1 High St", f"https://x/{number}", self.observed, "b" * 64,
        )  # fmt: skip


@pytest.fixture
def people(monkeypatch: pytest.MonkeyPatch) -> set[str]:
    """Who currently holds a valid identity assertion. The identity check itself is the verification module's; here it is a switch."""
    verified: set[str] = set()

    async def current(conn: Any, principal_id: str, now: datetime) -> bool:
        return principal_id in verified

    company.configure_sources(current, company._screening_status)
    monkeypatch.setattr(company, "_person_assurance", current)
    return verified


@pytest.fixture
def screening(monkeypatch: pytest.MonkeyPatch) -> dict[str, str]:
    state = {"status": "VERIFIED"}

    class Case:
        def __init__(self, status: str) -> None:
            self.status = type("S", (), {"value": status})()

    async def case_for_kyb(conn: Any, tenant_id: str, case_id: str, *, now: datetime) -> Any:
        return (Case(state["status"]), True)

    async def status(conn: Any, tenant_id: str, case_id: str, now: datetime) -> str | None:
        return state["status"]

    monkeypatch.setattr(company, "_screening_status", status)
    return state


def test_grade_is_stored_read_live_and_moved_by_mandates(
    rt_url: str, people: set[str], screening: dict[str, str]
) -> None:
    tenant, requester, author, reviewer, alice, bob, recorder, cosigner = (uid() for _ in range(8))
    people.update({alice, bob})

    async def run() -> None:
        async with engine(rt_url) as eng:

            async def tx(fn):
                async with tenant_transaction(eng, ctx(tenant)) as c:
                    return await fn(c)

            # 0. nothing recorded: K0, nothing allowed. Absence is not permission; the gate off lets anything through
            e0 = await tx(lambda c: assessment.allowed(c, tenant, person=alice, now=NOW))
            assert (
                e0.grade is g.Grade.K0 and e0.actions == frozenset() and e0.stored_version is None
            )
            with pytest.raises(assessment.GateError, match="KYB_GRADE_INSUFFICIENT"):
                await tx(
                    lambda c: assessment.gate(
                        c, tenant, "SIGN_AGREEMENT", person=alice, mode="enforce", now=NOW
                    )
                )
            shadow = await tx(
                lambda c: assessment.gate(
                    c, tenant, "SIGN_AGREEMENT", person=alice, mode="shadow", now=NOW
                )
            )
            assert (
                "SIGN_AGREEMENT" not in shadow.actions
            )  # shadow reports what would be refused and refuses nothing
            off = await tx(
                lambda c: assessment.gate(
                    c, tenant, "SIGN_AGREEMENT", person=alice, mode="off", now=NOW
                )
            )
            assert "SIGN_AGREEMENT" in off.actions
            with pytest.raises(assessment.GateError, match="KYB_GATE_MODE_INVALID"):
                await tx(
                    lambda c: assessment.gate(c, tenant, "X", person=None, mode="maybe", now=NOW)
                )

            # 1. a register match: K2 (an exact, active, fresh register match), and the grade is issued on the outbox
            async def verified_case(c):
                return await kyb_service.request_verification(
                    c, tenant, applicant=Applicant("GB", "00123456", "Acme Widgets Limited"), requested_by=requester, idempotency_key="key-co-0001",
                    adapters={"companies-house": Register(NOW - timedelta(days=5))}, routing=ROUTING, sign=sign, now=NOW,
                )  # fmt: skip

            case = await tx(verified_case)
            assert case.status.value == "VERIFIED"
            s1 = await tx(
                lambda c: company.reassess(c, tenant, trigger="TEST", actor=None, now=NOW)
            )
            assert (
                s1.grade is g.Grade.K2 and s1.version == 1 and "OWNERSHIP_INCOMPLETE" in s1.reasons
            )
            events = await tx(lambda c: _events(c, tenant))
            assert events == ["com.zoiko.id.organization.kyb.assertion_issued.v1"]

            # 2. a binding act is still refused at K2: a registry match with no verified representative cannot sign (T15)
            e2 = await tx(lambda c: assessment.allowed(c, tenant, person=alice, now=NOW))
            assert (
                e2.grade is g.Grade.K2
                and "SIGN_AGREEMENT" not in e2.actions
                and "ORG_ROUTINE_FUNCTIONS" in e2.actions
            )

            # 3. ownership settled: Alice owns it all, a different person confirms her, pointing at her identity assertion
            graph = await tx(
                lambda c: ownership.save(
                    c, tenant, actor=author, target="acme", entities=[{"id": "acme", "kind": "COMPANY"}, {"id": "alice", "kind": "PERSON"}],
                    links=[{"owner": "alice", "owned": "acme", "percent": "100", "evidenced": True}], threshold="25", now=NOW,
                )
            )  # fmt: skip
            s_open = await tx(
                lambda c: company.reassess(c, tenant, trigger="TEST", actor=None, now=NOW)
            )
            assert (
                s_open.grade is g.Grade.K2 and "OWNERSHIP_INCOMPLETE" in s_open.reasons
            )  # a candidate nobody decided is not settled
            await tx(
                lambda c: ownership.confirm(
                    c, tenant, graph["graph_id"], entity_id="alice", decision="CONFIRMED", reason="Confirmed against the share register and her passport check.",
                    evidence_ref=f"verification:{alice}", reviewer=reviewer, now=NOW,
                )
            )  # fmt: skip
            s3 = await tx(
                lambda c: company.reassess(
                    c, tenant, trigger="OWNERSHIP_CONFIRMED", actor=reviewer, now=NOW
                )
            )
            assert s3.grade is g.Grade.K3 and "NO_MANDATE_IN_FORCE" in s3.reasons

            # 4. a mandate: the representative cannot record their own, the evidence is required, payments always need a second signatory
            window = {
                "valid_from": NOW - timedelta(days=1),
                "valid_to": NOW + timedelta(days=200),
                "now": NOW,
            }
            for bad, code in (
                ({"person": bob, "recorder": bob, "role": "DIRECTOR", "source": "BOARD_MINUTE", "powers": ["SIGN_AGREEMENT"], "evidence_ref": "minute-1"}, "MANDATE_SELF_RECORDED"),
                ({"person": bob, "recorder": recorder, "role": "DIRECTOR", "source": "BOARD_MINUTE", "powers": ["SIGN_AGREEMENT"], "evidence_ref": " "}, "MANDATE_EVIDENCE_REQUIRED"),
                ({"person": bob, "recorder": recorder, "role": "DIRECTOR", "source": "BOARD_MINUTE", "powers": ["READ_FILES"], "evidence_ref": "m"}, "MANDATE_POWERS_INVALID"),
            ):  # fmt: skip
                with pytest.raises(MandateError, match=code):
                    await tx(lambda c, b=bad: company.record_mandate(c, tenant, **b, **window))
            m_id = await tx(
                lambda c: company.record_mandate(
                    c, tenant, person=bob, recorder=recorder, role="DIRECTOR", source="BOARD_MINUTE", powers=["SIGN_AGREEMENT", "AUTHORIZE_PAYMENT"],
                    evidence_ref="board-minute-2026-11-01", **window,
                )
            )  # fmt: skip
            # a payment power forces the second signatory; until someone else co-signs, the binding acts stay refused
            e4a = await tx(lambda c: assessment.allowed(c, tenant, person=bob, now=NOW))
            assert e4a.grade is g.Grade.K4 and not (
                {"SIGN_AGREEMENT", "AUTHORIZE_PAYMENT"} & e4a.actions
            )
            for who, code in (
                (bob, "COSIGNER_IS_REPRESENTATIVE"),
                (recorder, "COSIGNER_IS_RECORDER"),
            ):
                with pytest.raises(MandateError, match=code):
                    await tx(
                        lambda c, w=who: company.cosign_mandate(
                            c, tenant, m_id, cosigner=w, now=NOW
                        )
                    )
            await tx(lambda c: company.cosign_mandate(c, tenant, m_id, cosigner=cosigner, now=NOW))
            e4 = await tx(lambda c: assessment.allowed(c, tenant, person=bob, now=NOW))
            assert {"SIGN_AGREEMENT", "AUTHORIZE_PAYMENT"} <= e4.actions
            carol = await tx(lambda c: assessment.allowed(c, tenant, person=uid(), now=NOW))
            assert not (
                carol.actions & g.BINDING_ACTIONS
            )  # a different person gets nothing: T16, no identity swapping
            assert (await tx(lambda c: assessment.latest(c, tenant))).grade is g.Grade.K4

            # 5. the grade is worked out again when asked: with time alone, the register check goes stale and the mandate ends
            later = NOW + timedelta(days=210)
            e_late = await tx(lambda c: assessment.allowed(c, tenant, person=bob, now=later))
            assert (
                e_late.grade is g.Grade.K0
                and e_late.lowered_since_assessed is True
                and not (e_late.actions & g.BINDING_ACTIONS)
            )
            assert (
                await tx(lambda c: assessment.latest(c, tenant))
            ).grade is g.Grade.K4  # nobody edited the record

            # 6. an identity assertion lapses: the person is no longer verified, so the authority goes with it
            people.discard(bob)
            e_unverified = await tx(lambda c: assessment.allowed(c, tenant, person=bob, now=NOW))
            assert (
                e_unverified.grade is g.Grade.K4
            )  # the stored facts still say verified: reassessing is what moves it
            s6 = await tx(
                lambda c: company.reassess(
                    c, tenant, trigger="IDENTITY_ASSERTION_LAPSED", actor=None, now=NOW
                )
            )
            assert s6.grade is g.Grade.K3 and "NO_MANDATE_IN_FORCE" in s6.reasons
            people.add(bob)
            s6b = await tx(
                lambda c: company.reassess(
                    c, tenant, trigger="IDENTITY_ASSERTION_RENEWED", actor=None, now=NOW
                )
            )
            assert s6b.grade is g.Grade.K4

            # 7. revoking the mandate is a new fact with a reason: K3 again, the revoked claims go out as an event, and the row stays
            with pytest.raises(MandateError, match="REASON_REQUIRED"):
                await tx(
                    lambda c: company.revoke_mandate(
                        c, tenant, m_id, by=recorder, reason="no", now=NOW
                    )
                )
            await tx(
                lambda c: company.revoke_mandate(
                    c,
                    tenant,
                    m_id,
                    by=recorder,
                    reason="Representative left the company on 1 November.",
                    now=NOW,
                )
            )
            s7 = await tx(lambda c: assessment.latest(c, tenant))
            assert s7.grade is g.Grade.K3 and s7.trigger == "MANDATE_REVOKED"
            assert "com.zoiko.id.organization.kyb.assertion_revoked.v1" in await tx(
                lambda c: _events(c, tenant)
            )
            e7 = await tx(lambda c: assessment.allowed(c, tenant, person=bob, now=NOW))
            assert not (
                e7.actions & g.BINDING_ACTIONS
            )  # T22: a revoked mandate denies the act though the register is fine
            with pytest.raises(MandateError, match="MANDATE_REVOKED"):
                await tx(lambda c: company.cosign_mandate(c, tenant, m_id, cosigner=uid(), now=NOW))
            listed = await tx(lambda c: company.list_mandates(c, tenant))
            assert len(listed) == 1 and listed[0]["revoked"] is True

            # 8. screening that is not clear holds the company below K3 (a potential match is a review, never a pass)
            screening["status"] = "REVIEW_REQUIRED"
            s8 = await tx(
                lambda c: company.reassess(
                    c, tenant, trigger="SANCTIONS_ALERT", actor=None, now=NOW
                )
            )
            assert s8.grade is g.Grade.K2 and "SCREENING_POTENTIAL_MATCH" in s8.reasons

            # 9. the record is append-only and another company cannot read it
            hist = await tx(lambda c: assessment.history(c, tenant))
            assert [h.version for h in hist] == sorted(
                (h.version for h in hist), reverse=True
            ) and hist[-1].version == 1
            async with tenant_transaction(eng, ctx(tenant)) as c:
                with pytest.raises(DBAPIError, match="never changes|permission denied"):
                    await c.execute(text("UPDATE org.kyb_assessment SET grade = 'K4'"))
            async with tenant_transaction(eng, ctx(tenant)) as c:
                with pytest.raises(DBAPIError, match="never deleted|permission denied"):
                    await c.execute(text("DELETE FROM org.kyb_mandate"))
            async with tenant_transaction(eng, ctx(uid())) as c:
                assert (
                    await c.execute(text("SELECT count(*) FROM org.kyb_assessment"))
                ).scalar_one() == 0
                assert (await assessment.latest(c, tenant)) is None

    async def _events(c: Any, tenant_id: str) -> list[str]:
        rows = await c.execute(
            text(
                "SELECT event_type FROM events.outbox_event WHERE tenant_id = :t AND event_type LIKE 'com.zoiko.id.organization.kyb.assertion_%' ORDER BY created_at, event_id"
            ),
            {"t": uuid.UUID(tenant_id)},
        )
        return [r.event_type for r in rows]

    asyncio.run(run())
