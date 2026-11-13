"""The regulatory pieces working together on PostgreSQL (CR-072 to CR-077).

1. A claim is publishable only while the exact certification behind it (G02) and the launched market release it names (G10) hold; withdrawing the
   release takes it off at once; a global claim never appears; a proof bundle is exported, verifiable and audited (G07, T05, T33).
2. A statement recorded in the issuer ledger (G04) changes what a live claim check trusts, and a reasoned correction (a backdated revocation)
   takes that trust away again, through the same check code the credential API runs.
"""

from __future__ import annotations

import asyncio
import uuid
from dataclasses import replace
from datetime import UTC, date, datetime, timedelta
from typing import Any

from conftest import ctx, engine
from fastapi.testclient import TestClient
from operator_fixture import ISS
from test_credential_trust_pg import check, license_claim
from test_credential_trust_pg import gate as claim_gate
from test_regulatory_releases_api_pg import _app, _audit, _grant, _people, _seed_pack
from zid_db import tenant_transaction
from zid_platform.modules.credential.domain.issuer_history import Kind, Statement
from zid_platform.modules.credential.infrastructure import issuer_history_store
from zid_platform.modules.credential.infrastructure.registries import current_issuer_registry
from zid_platform.modules.verification.application import market_releases as rel
from zid_platform.modules.verification.domain import market_release as md
from zid_platform.modules.verification.domain import regulated_claims as rc
from zid_platform.modules.verification.domain.attestation import Scope, Snapshot
from zid_platform.modules.verification.domain.certification import CertStatus
from zid_platform.modules.verification.infrastructure import attestation_store

NOW = datetime.now(UTC)
APPROVERS = {"legal": "al", "security_grc": "gus", "qa": "qa-lead", "product_owner": "pat"}


def _run(url: str, work: Any) -> Any:
    async def go() -> Any:
        async with engine(url) as eng, eng.begin() as c:
            return await work(c)

    return asyncio.run(go())


async def _launch(c: Any, rid: str, market: md.MarketKey) -> None:
    await rel.open_release(c, rid, market, author="maker", now=NOW)
    for g in md.GATES:
        await rel.attest_gate(
            c,
            rid,
            1,
            gate=g.gate_id,
            status=md.GateStatus.PASSED,
            evidence_ref=f"EVID-{g.gate_id}",
            role=g.owner_role,
            by=f"rec-{g.owner_role}",
            now=NOW,
        )
    frozen = await rel.freeze_release(c, rid, 1, suppliers=[], now=NOW)
    for role in md.RELEASE_APPROVER_ROLES:
        await rel.approve_release(
            c,
            rid,
            1,
            role=role,
            approver=APPROVERS[role],
            signed_hash=frozen.release_hash or "",
            now=NOW,
        )
    await rel.launch_release(c, rid, 1, now=NOW)


def _snapshot(scope: Scope) -> Snapshot:
    return Snapshot(
        snapshot_id=f"s-{uuid.uuid4().hex[:12]}",
        scope=scope,
        scheme_version="1",
        certificate_ref="C-1",
        component_only=False,
        status=CertStatus.ACTIVE,
        valid_from=date(2025, 1, 1),
        valid_until=None,
        register_url="https://register.example.test/1",
        register_entry_id="E-1",
        cab=None,
        fetched_at=NOW - timedelta(hours=1),
        proof_hash="d" * 64,
        evidence_locator="vault:x/y",
        recorded_by="ops",
        recorded_at=NOW - timedelta(hours=1),
    )


def _approved(t: rc.ClaimTemplate) -> rc.ClaimTemplate:
    digest = rc.template_hash(t)
    return replace(
        t, approvals={r: rc.Approval(f"REF-{r}", digest) for r in rc.CLAIM_APPROVER_ROLES}
    )


def test_a_claim_is_published_only_while_its_certification_and_market_hold_and_its_proof_is_exported(
    rt_url: str, owner_url: str
) -> None:
    product = f"P{uuid.uuid4().hex[:10].upper()}"
    market = md.MarketKey("GB", product, "STANDARD")
    scope = Scope(f"PARTNER-{uuid.uuid4().hex[:8]}", "UK_DIATF", "GB", "RTR")
    rid = f"claims-{uuid.uuid4().hex[:10]}"
    _seed_pack(rt_url, product)
    _run(rt_url, lambda c: attestation_store.record(c, _snapshot(scope), now=NOW))
    _run(rt_url, lambda c: _launch(c, rid, market))

    good = _approved(
        rc.ClaimTemplate(
            template_id=f"gb-rtr-{uuid.uuid4().hex[:6]}",
            version=1,
            text="Right to Rent checks in England use a certified identity service provider.",
            audiences=frozenset({"PUBLIC_WEBSITE"}),
            countries=("GB",),
            certifications=(scope,),
            markets=(market,),
        )
    )
    unrelated = _approved(
        replace(
            good,
            template_id=f"gb-idv-{uuid.uuid4().hex[:6]}",
            certifications=(replace(scope, service_code="IDV"),),
        )
    )
    global_ = _approved(
        replace(good, template_id=f"global-{uuid.uuid4().hex[:6]}", text="Certified worldwide.")
    )

    o, who = _people(rt_url)
    app = _app(rt_url, o)
    app.state.claim_templates = (good, unrelated, global_)
    with TestClient(app, base_url=ISS) as api:

        def call(path: str, **params: Any) -> Any:
            return api.get(path, params=params, headers=o.bearer(who["recorder"]))

        assert call("/v1/regulatory/claims").status_code == 403  # no regulatory capability yet
        _grant(api, o, who, "recorder", "regulatory.attestation.record")

        listed = {
            i["template_id"]: i
            for i in call("/v1/regulatory/claims", audience="PUBLIC_WEBSITE").json()["items"]
        }
        assert listed[good.template_id]["publishable"] is True
        assert not listed[unrelated.template_id][
            "publishable"
        ]  # T05: same company, unrelated service
        assert any(
            r.startswith("certification_hold:") for r in listed[unrelated.template_id]["reasons"]
        )
        assert "TEMPLATE_GLOBAL_CLAIM" in listed[global_.template_id]["reasons"]  # T33

        shown = call(
            "/v1/regulatory/claims", audience="PUBLIC_WEBSITE", publishable_only="true"
        ).json()["items"]
        assert [i["template_id"] for i in shown] == [good.template_id]
        assert (
            call("/v1/regulatory/claims", publishable_only="true").status_code == 422
        )  # an audience is required

        bundle = call(f"/v1/regulatory/claims/{good.template_id}/proof").json()
        assert bundle["publishable"] is True and rc.verify_bundle(bundle)
        kinds = {e["kind"] for e in bundle["evidence"]}
        assert kinds == {"certification", "market"}
        assert call("/v1/regulatory/claims/nobody/proof").status_code == 404

        # withdrawing the release takes the claim off at once, without touching the template
        _run(rt_url, lambda c: rel.withdraw_release(c, rid, 1, now=NOW))
        assert (
            call(
                "/v1/regulatory/claims", audience="PUBLIC_WEBSITE", publishable_only="true"
            ).json()["items"]
            == []
        )
        after = call(f"/v1/regulatory/claims/{good.template_id}/proof").json()
        assert after["publishable"] is False and any(
            r.startswith("market_closed:") for r in after["reasons"]
        )

    exported = [
        r
        for r in _audit(owner_url, f"{good.template_id}:v%")
        if r["event_type"] == "regulatory.claim.proof_exported"
    ]
    assert len(exported) == 2 and exported[0]["body"]["details"]["digest"] == bundle["digest"]


def test_an_issuer_recorded_in_the_ledger_is_trusted_by_a_live_claim_check_and_a_correction_takes_it_away(
    rt_url: str,
) -> None:
    issuer_id = f"ledger-board-{uuid.uuid4().hex[:8]}"
    name = f"Ledger Licensing Board {uuid.uuid4().hex[:6]}"
    record: dict[str, Any] = {
        "issuer_id": issuer_id,
        "kind": "REGULATOR",
        "authority": "OFFICIAL",
        "countries": ["US"],
        "claim_types": ["PROFESSIONAL_LICENSE"],
        "names": [{"name": name}],
    }

    def statement(
        seq: int,
        rec: dict[str, Any],
        *,
        ago: timedelta,
        kind: Kind = Kind.UPDATE,
        reason: str | None = None,
    ) -> Statement:
        return Statement(
            statement_id=f"s-{issuer_id}-{seq}",
            issuer_id=issuer_id,
            seq=seq,
            kind=kind,
            reason=reason,
            record=rec,
            source_url="https://regulator.example.test/board",
            source_hash="e" * 64,
            evidence_locator="vault:issuers/x",
            recorded_by="reg-ops",
            recorded_at=NOW - ago,
        )

    tenant = str(uuid.uuid4())

    async def check_with_current_registry() -> str:
        async with engine(rt_url) as eng, tenant_transaction(eng, ctx(tenant)) as c:
            registry = await current_issuer_registry(c)
            r = await check(
                c,
                tenant,
                str(uuid.uuid4()),
                claimed=license_claim(regulator=name),
                gate=claim_gate(registry=registry),
            )
            return r.assertion.decision.value + ":" + ",".join(r.assertion.reason_codes)

    before = asyncio.run(check_with_current_registry())
    assert (
        before.startswith("NOT_VERIFIED") and "ISSUER_NOT_TRUSTED" in before
    )  # unknown: never trusted

    _run(
        rt_url,
        lambda c: issuer_history_store.record(
            c, statement(1, record, ago=timedelta(hours=2)), now=datetime.now(UTC)
        ),
    )
    assert asyncio.run(check_with_current_registry()).startswith(
        "VERIFIED"
    )  # the ledger is what the live check reads

    revoked = record | {"revoked_on": (NOW - timedelta(days=30)).date().isoformat()}
    _run(
        rt_url,
        lambda c: issuer_history_store.record(
            c,
            statement(
                2,
                revoked,
                ago=timedelta(minutes=1),
                kind=Kind.CORRECTION,
                reason="the regulator revoked this board last month",
            ),
            now=datetime.now(UTC),
        ),
    )
    after = asyncio.run(check_with_current_registry())
    assert after.startswith("NOT_VERIFIED") and "ISSUER_NOT_TRUSTED" in after
