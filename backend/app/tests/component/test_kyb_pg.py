"""Company verification service on PostgreSQL (ZID-ED-03): signed append-only evidence, idempotent requests, refresh that
never rewrites history, a reviewer's ruling with its rationale, tenant isolation and an audited decision."""

from __future__ import annotations

import ast
import asyncio
import inspect
import uuid
from dataclasses import replace
from datetime import UTC, datetime, timedelta
from decimal import Decimal
from typing import Any

import pytest
from conftest import ctx, engine
from sqlalchemy import text
from sqlalchemy.exc import DBAPIError
from zid_crypto import KeyRing, LocalSigner, verify_jws
from zid_db import tenant_transaction
from zid_errors import ZidError
from zid_platform.modules.commercial.application import vendor_service as vendors
from zid_platform.modules.organization.application import kyb as service
from zid_platform.modules.organization.domain.kyb import (
    Applicant,
    Capabilities,
    CompanyFacts,
    KybError,
    KybStatus,
    Policy,
    Review,
    Route,
    Routing,
    SourceKind,
)
from zid_platform.modules.organization.infrastructure import kyb_adapters

NOW = datetime(2026, 11, 2, 12, 0, tzinfo=UTC)
TYP = "zid-kyb-evidence+jwt"
SIGNER = LocalSigner("kyb-test")
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
    return RING.sign(body, typ=TYP)


def company(
    name: str = "Acme Widgets Limited", number: str = "00123456", active: bool = True
) -> CompanyFacts:
    return CompanyFacts(
        adapter_id="companies-house",
        source_kind=SourceKind.OFFICIAL_REGISTER,
        jurisdiction="GB",
        registry_authority="companies-house",
        registration_number=number,
        legal_name=name,
        legal_form="ltd",
        registration_status="active" if active else "dissolved",
        active=active,
        registered_address="1 High St, London",
        source_uri=f"https://api.company-information.service.gov.uk/company/{number}",
        observed_at=NOW,
        response_hash="b" * 64,
    )


class Register:
    def __init__(self, answer: CompanyFacts | None | str) -> None:
        self.adapter_id, self.kind = "companies-house", SourceKind.OFFICIAL_REGISTER
        self.capabilities = Capabilities(frozenset({"GB"}))
        self.answer = answer

    async def search(self, query: str, jurisdiction: str, now: datetime) -> list[Any]:
        return []

    async def get(self, number: str, country: str, now: datetime) -> CompanyFacts | None:
        if self.answer == "down":
            raise KybError("SOURCE_UNAVAILABLE")
        assert not isinstance(self.answer, str)
        return self.answer


def test_cases_evidence_review_isolation_and_audit(rt_url: str) -> None:
    tenant, other_tenant, requester, reviewer = uid(), uid(), uid(), uid()
    applicant = Applicant("GB", "00123456", "Acme Widgets Limited", "ltd")

    async def run() -> None:
        async with engine(rt_url) as eng:
            # 1. an exact register match: verified, signed, hashed, expiring, and audited
            async with tenant_transaction(eng, ctx(tenant)) as c:
                v = await service.request_verification(
                    c, tenant, applicant=applicant, requested_by=requester, idempotency_key="key-00000001",
                    adapters={"companies-house": Register(company())}, routing=ROUTING, sign=sign, now=NOW,
                )  # fmt: skip
                assert (
                    v.status is KybStatus.VERIFIED
                    and v.version == 1
                    and v.expiry_at == NOW + timedelta(days=365)
                )
                rows = await service.history(c, tenant, v.case_id)
                assert len(rows) == 1
                _, claims = verify_jws(rows[0].jws, keys=[SIGNER.public_jwk()], typ=TYP)
                assert claims["evidence_hash"] == rows[0].evidence_hash == v.evidence_hash
                assert (
                    claims["decision_status"] == "VERIFIED"
                    and claims["registration_number"] == "00123456"
                )
                audit = (
                    await c.execute(
                        text(
                            "SELECT outcome, evidence_class::text, body FROM evidence.audit_record "
                            "WHERE tenant_id = :t AND event_type = 'organization.kyb.decided'"
                        ),
                        {"t": uuid.UUID(tenant)},
                    )
                ).all()
                assert (
                    len(audit) == 1
                    and audit[0].outcome == "SUCCESS"
                    and audit[0].evidence_class == "E3"
                )
            case_id = v.case_id

            # 2. the same key is the same case; the same key for another company is refused
            async with tenant_transaction(eng, ctx(tenant)) as c:
                again = await service.request_verification(
                    c, tenant, applicant=applicant, requested_by=requester, idempotency_key="key-00000001",
                    adapters={"companies-house": Register("down")}, routing=ROUTING, sign=sign, now=NOW,
                )  # fmt: skip
                assert (
                    again.case_id == case_id and again.status is KybStatus.VERIFIED
                )  # nothing was re-asked
                assert len(await service.history(c, tenant, case_id)) == 1
            async with tenant_transaction(eng, ctx(tenant)) as c:
                with pytest.raises(KybError, match="IDEMPOTENCY_CONFLICT"):
                    await service.request_verification(
                        c, tenant, applicant=Applicant("GB", "00999999", "Other Limited"), requested_by=requester,
                        idempotency_key="key-00000001", adapters={}, routing=ROUTING, sign=sign, now=NOW,
                    )  # fmt: skip

            # 3. a refresh while the register is down appends UNAVAILABLE; the earlier evidence is untouched
            async with tenant_transaction(eng, ctx(tenant)) as c:
                r = await service.refresh(
                    c, tenant, case_id, adapters={"companies-house": Register("down")}, routing=ROUTING,
                    sign=sign, now=NOW + timedelta(days=1),
                )  # fmt: skip
                assert r.status is KybStatus.UNAVAILABLE and r.version == 2
                rows = await service.history(c, tenant, case_id)
                assert [x.decision_status for x in rows] == [
                    KybStatus.VERIFIED,
                    KybStatus.UNAVAILABLE,
                ]
                assert rows[0].evidence_hash != rows[1].evidence_hash

            # 4. history cannot be edited or deleted, even by the owner of the data
            async with tenant_transaction(eng, ctx(tenant)) as c:
                with pytest.raises(DBAPIError, match="append-only|permission denied"):
                    await c.execute(text("UPDATE org.kyb_evidence SET review_reason = 'x'"))
            async with tenant_transaction(eng, ctx(tenant)) as c:
                with pytest.raises(DBAPIError, match="append-only|permission denied"):
                    await c.execute(text("DELETE FROM org.kyb_evidence"))

            # 5. another company cannot see or touch the case
            async with tenant_transaction(eng, ctx(other_tenant)) as c:
                with pytest.raises(KybError, match="CASE_NOT_FOUND"):
                    await service.get_case(c, other_tenant, case_id, now=NOW)
                n = (await c.execute(text("SELECT count(*) FROM org.kyb_evidence"))).scalar_one()
                assert n == 0

            # 6. a name the register contradicts goes to review; a reviewer rules with a rationale
            async with tenant_transaction(eng, ctx(tenant)) as c:
                review_case = await service.request_verification(
                    c, tenant, applicant=Applicant("GB", "00777777", "Acme Global Industries Limited"),
                    requested_by=requester, idempotency_key="key-00000002",
                    adapters={"companies-house": Register(company(number="00777777"))}, routing=ROUTING,
                    sign=sign, now=NOW,
                )  # fmt: skip
                assert (
                    review_case.status is KybStatus.NEEDS_REVIEW
                    and review_case.review_reason == "NAME_MISMATCH"
                )
            async with tenant_transaction(eng, ctx(tenant)) as c:
                with pytest.raises(KybError, match="REVIEWER_IS_REQUESTER"):
                    await service.review(
                        c, tenant, review_case.case_id,
                        ruling=Review(requester, KybStatus.VERIFIED, "I checked it myself, honestly."),
                        routing=ROUTING, sign=sign, now=NOW,
                    )  # fmt: skip
            async with tenant_transaction(eng, ctx(tenant)) as c:
                with pytest.raises(KybError, match="RATIONALE_REQUIRED"):
                    await service.review(
                        c, tenant, review_case.case_id, ruling=Review(reviewer, KybStatus.VERIFIED, "ok"),
                        routing=ROUTING, sign=sign, now=NOW,
                    )  # fmt: skip
            async with tenant_transaction(eng, ctx(tenant)) as c:
                done = await service.review(
                    c, tenant, review_case.case_id,
                    ruling=Review(reviewer, KybStatus.VERIFIED, "Certificate of change of name sighted and filed."),
                    routing=ROUTING, sign=sign, now=NOW,
                )  # fmt: skip
                assert done.status is KybStatus.VERIFIED and done.version == 2
                rows = await service.history(c, tenant, review_case.case_id)
                assert [x.decision_status for x in rows] == [
                    KybStatus.NEEDS_REVIEW,
                    KybStatus.VERIFIED,
                ]
                assert (
                    rows[1].decided_by == reviewer
                    and "certificate" in (rows[1].rationale or "").lower()
                )
                assert (
                    rows[1].body["reviewed"] is True
                    and rows[1].body["legal_name"] == "Acme Widgets Limited"
                )
                assert "REVIEWER_RULING" in rows[1].reasons and "NAME_MISMATCH" in rows[1].reasons
            async with tenant_transaction(eng, ctx(tenant)) as c:
                with pytest.raises(KybError, match="CASE_NOT_IN_REVIEW"):
                    await service.review(
                        c, tenant, review_case.case_id,
                        ruling=Review(reviewer, KybStatus.REJECTED, "Changed my mind about this one."),
                        routing=ROUTING, sign=sign, now=NOW,
                    )  # fmt: skip

            # 7. a verified outcome expires: after its validity it reads as EXPIRED, never verified
            async with tenant_transaction(eng, ctx(tenant)) as c:
                late = await service.get_case(
                    c, tenant, review_case.case_id, now=NOW + timedelta(days=366)
                )
                assert late.status is KybStatus.EXPIRED
                assert (
                    await service.get_case(c, tenant, review_case.case_id, now=NOW)
                ).status is KybStatus.VERIFIED

            # 8. a country that is not open creates no case at all
            def closed(country: str) -> None:
                raise ZidError("COUNTRY_CAPABILITY_UNAVAILABLE", country, http_status=422)

            async with tenant_transaction(eng, ctx(tenant)) as c:
                with pytest.raises(ZidError):
                    await service.request_verification(
                        c, tenant, applicant=applicant, requested_by=requester, idempotency_key="key-00000003",
                        adapters={"companies-house": Register(company())}, routing=ROUTING, sign=sign, now=NOW,
                        country_gate=closed,
                    )  # fmt: skip
                count = (
                    await c.execute(
                        text(
                            "SELECT count(*) FROM org.kyb_case WHERE idempotency_key = 'key-00000003'"
                        )
                    )
                ).scalar_one()
                assert count == 0

    asyncio.run(run())


def test_concurrent_requests_with_one_key_are_one_case_and_concurrent_refreshes_are_serialised(
    rt_url: str,
) -> None:
    tenant, requester = uid(), uid()
    applicant = Applicant("GB", "00123456", "Acme Widgets Limited", "ltd")

    async def run() -> None:
        async with engine(rt_url) as eng:

            async def request() -> str:
                async with tenant_transaction(eng, ctx(tenant)) as c:
                    v = await service.request_verification(
                        c, tenant, applicant=applicant, requested_by=requester, idempotency_key="race-00000001",
                        adapters={"companies-house": Register(company())}, routing=ROUTING, sign=sign, now=NOW,
                    )  # fmt: skip
                    return v.case_id

            ids = await asyncio.gather(*(request() for _ in range(4)))
            assert len(set(ids)) == 1  # a retry storm is one case, never a duplicate or a crash
            case_id = ids[0]
            async with tenant_transaction(eng, ctx(tenant)) as c:
                assert len(await service.history(c, tenant, case_id)) == 1

            async def refresh() -> int:
                async with tenant_transaction(eng, ctx(tenant)) as c:
                    r = await service.refresh(
                        c, tenant, case_id, adapters={"companies-house": Register(company())}, routing=ROUTING,
                        sign=sign, now=NOW + timedelta(days=1),
                    )  # fmt: skip
                    return r.version

            versions = sorted(await asyncio.gather(refresh(), refresh(), refresh()))
            assert versions == [
                2,
                3,
                4,
            ]  # each refresh took its own turn on the case: no two share a version
            async with tenant_transaction(eng, ctx(tenant)) as c:
                assert [x.version for x in await service.history(c, tenant, case_id)] == [
                    1,
                    2,
                    3,
                    4,
                ]

    asyncio.run(run())


def test_supplier_lookups_are_costed_and_an_outage_is_never_chargeable(rt_url: str) -> None:
    tenant, requester, actor = uid(), uid(), uid()
    vid = f"kyb-{uuid.uuid4().hex[:8]}"
    supplier_id = f"creditsafe-{vid}"
    routing = Routing(
        "r1",
        Policy("p1", timedelta(days=365)),
        {"GB": Route((supplier_id,), None, ())},
        {supplier_id: "ENABLED"},
        {"GB": frozenset({supplier_id})},
    )

    class Supplier(Register):
        def __init__(self, answer: CompanyFacts | None | str) -> None:
            super().__init__(answer)
            self.adapter_id, self.kind = supplier_id, SourceKind.SUPPLIER

    def supplier_facts() -> CompanyFacts:
        return replace(company(), adapter_id=supplier_id, source_kind=SourceKind.SUPPLIER)

    async def run() -> None:
        async with engine(rt_url) as eng:
            async with eng.begin() as c:
                await vendors.register(
                    c, vendor_id=vid, display_name="Creditsafe", legal_role="SUBPROCESSOR", adapter_id=supplier_id,
                    sku_id="ZID-KYB", spike_threshold_pct=None, actor=actor,
                )  # fmt: skip
                await vendors.record_rate(
                    c, vendor_id=vid, check_type="KYB_LOOKUP", country="GB", base_price=Decimal("0.80"),
                    retry_price=Decimal("0.30"), currency="USD", confidence="SIGNED_TARIFF",
                    source_ref="tariff test", actor=actor,
                )  # fmt: skip
            async with tenant_transaction(eng, ctx(tenant)) as c:
                ok = await service.request_verification(
                    c, tenant, applicant=Applicant("GB", "00123456", "Acme Widgets Limited", "ltd"),
                    requested_by=requester, idempotency_key="cost-00000001",
                    adapters={supplier_id: Supplier(supplier_facts())}, routing=routing, sign=sign, now=NOW,
                )  # fmt: skip
                assert ok.status is KybStatus.VERIFIED
                await service.refresh(
                    c, tenant, ok.case_id, adapters={supplier_id: Supplier("down")}, routing=routing,
                    sign=sign, now=NOW + timedelta(days=1),
                )  # fmt: skip
                rows = (
                    await c.execute(
                        text(
                            "SELECT outcome, chargeable, charge_basis, cost, check_type FROM commercial.vendor_cost_event "
                            "WHERE tenant_id = :t AND vendor_id = :v ORDER BY occurred_at"
                        ),
                        {"t": uuid.UUID(tenant), "v": vid},
                    )
                ).all()
                assert [(r.outcome, r.chargeable, r.check_type) for r in rows] == [
                    ("COMPLETED", True, "KYB_LOOKUP"),  # a lookup that answered
                    (
                        "PROVIDER_ERROR",
                        False,
                        "KYB_LOOKUP",
                    ),  # an outage is the supplier's, not the customer's
                ]
                assert rows[0].cost == Decimal("0.800000") and rows[1].charge_basis == "NONE"

    asyncio.run(run())


def test_company_evidence_stays_in_the_tenants_own_tables_and_no_source_response_is_kept() -> None:
    """ED-03 section 06: raw results and evidence stay in the tenant's approved home region; no global cache or lake."""
    # the adapters hold configuration only: no response cache, no module-level store of anything a source returned
    for cls in (
        kyb_adapters.CompaniesHouseKyb,
        kyb_adapters.GleifKyb,
        kyb_adapters.CreditsafeKyb,
        kyb_adapters.SireneKyb,
    ):
        assert not [n for n in vars(cls) if "cache" in n.lower()], cls.__name__
    tree = ast.parse(inspect.getsource(kyb_adapters))
    assert not [n for n in ast.walk(tree) if isinstance(n, ast.Global | ast.Nonlocal)]
    decorators = [
        ast.unparse(d)
        for n in ast.walk(tree)
        if isinstance(n, ast.FunctionDef | ast.AsyncFunctionDef | ast.ClassDef)
        for d in n.decorator_list
    ]
    assert not [d for d in decorators if "cache" in d.lower()], decorators
    assert "redis" not in inspect.getsource(kyb_adapters).lower()
    # what is stored is the normalized fact and the response *hash*; the raw body has no column to live in
    store_source = inspect.getsource(service.store)
    assert "org.kyb_case" in store_source and "org.kyb_evidence" in store_source
    assert "raw" not in store_source.lower().replace("raise", "")
