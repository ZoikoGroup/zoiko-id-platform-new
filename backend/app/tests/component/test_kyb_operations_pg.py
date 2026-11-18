"""Appeals, rechecks and the signed evidence export on PostgreSQL (WF02 K06, K10; CR-109; acceptance T33, T37)."""

from __future__ import annotations

import asyncio
import uuid
from datetime import UTC, datetime, timedelta
from typing import Any

import pytest
from conftest import ctx, engine
from sqlalchemy import text
from sqlalchemy.exc import DBAPIError
from zid_crypto import KeyRing, LocalSigner, verify_jws
from zid_db import tenant_transaction
from zid_platform.modules.organization.application import kyb as kyb_service
from zid_platform.modules.organization.application import kyb_assessment as assessment
from zid_platform.modules.organization.application import kyb_company as company
from zid_platform.modules.organization.application import kyb_operations as ops
from zid_platform.modules.organization.domain import appeal as ap
from zid_platform.modules.organization.domain.kyb import (
    Applicant,
    Capabilities,
    CompanyFacts,
    Policy,
    Route,
    Routing,
    SourceKind,
)

NOW = datetime.now(UTC).replace(microsecond=0)
SIGNER = LocalSigner("kyb-ops-test")
RING = KeyRing(SIGNER)
TYP = "zid-kyb-export+jwt"
ROUTING = Routing(
    "r1",
    Policy("p1", timedelta(days=365)),
    {"GB": Route(("companies-house",), None, ())},
    {"companies-house": "ENABLED"},
    {},
)
WHY = (
    "The registry extract we sent is current and shows the same registered office as the register."
)
JUDGED = "Looked at the extract and the register again with fresh eyes and the facts support the company."


def uid() -> str:
    return str(uuid.uuid4())


def sign(body: dict[str, Any]) -> str:
    return RING.sign(body, typ="zid-kyb-evidence+jwt")


def sign_export(body: dict[str, Any]) -> str:
    return RING.sign(body, typ=TYP)


class Register:
    adapter_id, kind = "companies-house", SourceKind.OFFICIAL_REGISTER
    capabilities = Capabilities(frozenset({"GB"}))

    async def search(self, query: str, jurisdiction: str, now: datetime) -> list[Any]:
        return []

    async def get(self, number: str, country: str, now: datetime) -> CompanyFacts:
        return CompanyFacts(
            "companies-house", SourceKind.OFFICIAL_REGISTER, "GB", "companies-house", number, "Acme Widgets Limited", "ltd", "active", True,
            "1 High St", f"https://x/{number}", NOW - timedelta(days=5), "b" * 64,
        )  # fmt: skip


def test_appeals_rechecks_events_and_the_signed_export(rt_url: str, owner_url: str) -> None:
    tenant, requester, agent, judge = uid(), uid(), uid(), uid()

    async def run() -> None:
        async with engine(rt_url) as eng, engine(owner_url) as worker:

            async def tx(fn):
                async with tenant_transaction(eng, ctx(tenant)) as c:
                    return await fn(c)

            await tx(
                lambda c: kyb_service.request_verification(
                    c, tenant, applicant=Applicant("GB", "00123456", "Acme Widgets Limited"), requested_by=requester, idempotency_key="key-ops-0001",
                    adapters={"companies-house": Register()}, routing=ROUTING, sign=sign, now=NOW,
                )
            )  # fmt: skip
            s1 = await tx(
                lambda c: company.reassess(c, tenant, trigger="TEST", actor=None, now=NOW)
            )
            assert s1.grade.value == "K2" and s1.version == 1

            # 1. opening an appeal: a reason, the window, one at a time
            with pytest.raises(ap.AppealError, match="REASON_REQUIRED"):
                await tx(
                    lambda c: ops.open_appeal(
                        c, tenant, opened_by=agent, version=1, reason="no", now=NOW
                    )
                )
            with pytest.raises(ap.AppealError, match="APPEAL_WINDOW_CLOSED"):
                await tx(
                    lambda c: ops.open_appeal(
                        c,
                        tenant,
                        opened_by=agent,
                        version=1,
                        reason=WHY,
                        now=NOW + timedelta(days=45),
                    )
                )
            with pytest.raises(ap.AppealError, match="DECISION_NOT_FOUND"):
                await tx(
                    lambda c: ops.open_appeal(
                        c, tenant, opened_by=agent, version=9, reason=WHY, now=NOW
                    )
                )
            first = await tx(
                lambda c: ops.open_appeal(
                    c, tenant, opened_by=agent, version=1, reason=WHY, now=NOW
                )
            )
            with pytest.raises(ap.AppealError, match="APPEAL_ALREADY_OPEN"):
                await tx(
                    lambda c: ops.open_appeal(
                        c, tenant, opened_by=agent, version=1, reason=WHY, now=NOW
                    )
                )

            # 2. nobody who is a party decides it, and a decision gives its reasons (T37)
            for who in (requester, agent):
                with pytest.raises(ap.AppealError, match="DECIDER_IS_A_PARTY"):
                    await tx(
                        lambda c, w=who: ops.decide_appeal(
                            c,
                            tenant,
                            first,
                            decider=w,
                            outcome=ap.State.UPHELD,
                            reason=JUDGED,
                            now=NOW,
                        )
                    )
            with pytest.raises(ap.AppealError, match="REASON_REQUIRED"):
                await tx(
                    lambda c: ops.decide_appeal(
                        c,
                        tenant,
                        first,
                        decider=judge,
                        outcome=ap.State.UPHELD,
                        reason="fine",
                        now=NOW,
                    )
                )
            upheld = await tx(
                lambda c: ops.decide_appeal(
                    c, tenant, first, decider=judge, outcome=ap.State.UPHELD, reason=JUDGED, now=NOW
                )
            )
            assert (
                upheld["state"] == "UPHELD"
                and "stands" in upheld["message"]
                and "grade" not in upheld
            )
            with pytest.raises(ap.AppealError, match="APPEAL_ALREADY_DECIDED"):
                await tx(
                    lambda c: ops.decide_appeal(
                        c,
                        tenant,
                        first,
                        decider=judge,
                        outcome=ap.State.ALLOWED,
                        reason=JUDGED,
                        now=NOW,
                    )
                )
            assert (
                await tx(lambda c: assessment.latest(c, tenant))
            ).version == 1  # an upheld appeal changes nothing

            # 3. a decided appeal cannot be edited behind the service, and the table refuses a decider who opened it
            async with tenant_transaction(eng, ctx(tenant)) as c:
                with pytest.raises(DBAPIError, match="never changes|permission denied"):
                    await c.execute(text("UPDATE org.kyb_appeal SET state = 'ALLOWED'"))
            async with tenant_transaction(eng, ctx(tenant)) as c:
                with pytest.raises(DBAPIError, match="never deleted|permission denied"):
                    await c.execute(text("DELETE FROM org.kyb_appeal"))

            # 4. after an upheld appeal another may be opened; an allowed one sets the decision aside by adding an assessment
            second = await tx(
                lambda c: ops.open_appeal(
                    c,
                    tenant,
                    opened_by=agent,
                    version=1,
                    reason=WHY,
                    now=NOW + timedelta(minutes=1),
                )
            )
            allowed = await tx(
                lambda c: ops.decide_appeal(
                    c,
                    tenant,
                    second,
                    decider=judge,
                    outcome=ap.State.ALLOWED,
                    reason=JUDGED,
                    now=NOW,
                )
            )
            assert (
                allowed["state"] == "ALLOWED"
                and "assessed again" in allowed["message"]
                and allowed["grade"] == "K2"
            )
            rows = await tx(lambda c: assessment.history(c, tenant))
            assert [r.version for r in rows] == [2, 1] and rows[0].trigger == "APPEAL_ALLOWED:1"
            listed = await tx(lambda c: ops.appeals(c, tenant))
            assert [a["state"] for a in listed] == ["ALLOWED", "UPHELD"]

            # 4b. the applicant's own alert inbox says so, once, in the safe words (no threshold, rule or detector)
            alerts = await tx(lambda c: _alerts(c, tenant))
            assert [a.kind for a in alerts] == ["kyb.appeal.decided", "kyb.appeal.decided"] and len(
                {a.key for a in alerts}
            ) == 2
            assert all(
                w not in " ".join(a.body for a in alerts).lower()
                for w in ("threshold", "score", "rule", "fraud")
            )

            # 5. the recheck pass: a company is due when its next check has passed; the worker reassesses it and nobody else
            later = NOW + timedelta(days=400)
            async with worker.begin() as w:
                assert tenant in await ops.due_tenants(w, later)
                assert tenant not in await ops.due_tenants(w, NOW)
            async with worker.begin() as w:
                done = await ops.recheck_pass(w, later)
            assert (
                done[tenant] == "K0"
            )  # the register check has aged out: nothing independent is proven any more
            latest = await tx(lambda c: assessment.latest(c, tenant))
            assert latest.version == 3 and latest.trigger == "RECHECK_DUE"

            # 6. events: one that cannot change a grade does nothing, one that can reassesses at once (T33)
            assert await tx(lambda c: ops.apply_event(c, tenant, "PAGE_VIEWED", now=NOW)) is None
            moved = await tx(
                lambda c: ops.apply_event(c, tenant, "MANDATE_REVOKED", actor=judge, now=NOW)
            )
            assert (
                moved is not None
                and moved.trigger == "EVENT:MANDATE_REVOKED"
                and moved.version == 4
            )

            # 7. the export: signed, watermarked, scoped, with no names or documents
            with pytest.raises(ap.AppealError, match="PURPOSE_REQUIRED"):
                await tx(
                    lambda c: ops.evidence_export(
                        c, tenant, requester=judge, purpose="x", sign=sign_export, now=NOW
                    )
                )
            out = await tx(
                lambda c: ops.evidence_export(
                    c,
                    tenant,
                    requester=judge,
                    purpose="Annual customer due diligence review",
                    sign=sign_export,
                    now=NOW,
                )
            )
            _, claims = verify_jws(out["jws"], keys=[SIGNER.public_jwk()], typ=TYP)
            body = out["summary"]
            assert (
                claims["export_id"] == body["export_id"]
                and claims["watermark"]["requested_by"] == judge
            )
            assert body["watermark"]["purpose"] == "Annual customer due diligence review"
            assert [h["version"] for h in body["history"]] == [1, 2, 3, 4] and body["history"][-1][
                "trigger"
            ] == "EVENT"
            blob = str(body).lower()
            assert all(
                w not in blob for w in ("acme", "high st", "00123456", "widgets")
            )  # no company name, address or number

    async def _alerts(c: Any, tenant_id: str) -> list[Any]:
        from types import SimpleNamespace

        rows = await c.execute(
            text(
                "SELECT kind, body, correlation_key FROM platform.notification_request WHERE tenant_id = :t AND kind = 'kyb.appeal.decided' ORDER BY first_seen_at"
            ),
            {"t": uuid.UUID(tenant_id)},
        )
        return [SimpleNamespace(kind=r.kind, body=r.body, key=r.correlation_key) for r in rows]

    asyncio.run(run())
