"""The complete, signed section 08.1 assertion and its evidence receipt on PostgreSQL (CR-069).

GID-059 / 14.1  every check writes a signed evidence receipt in its own transaction and carries a signature that binds its exact body
GLB-10          a status change adds a superseding, re-signed check, its own receipt, and the event the document names
"""

from __future__ import annotations

import asyncio
import json
import uuid
from datetime import UTC, datetime, timedelta

import pytest
from conftest import ctx, engine
from sqlalchemy import text
from zid_crypto import LocalSigner, sign_jws, verify_jws
from zid_db import tenant_transaction
from zid_platform.modules.credential.application import assertion_contract as ac
from zid_platform.modules.credential.application import claim_checks as checks
from zid_platform.modules.credential.application import trust_service as svc
from zid_platform.modules.credential.domain import lifecycle as lc
from zid_platform.modules.credential.domain.claims import CredentialStatus
from zid_platform.modules.credential.domain.sources import SourceError
from zid_platform.modules.credential.infrastructure import claim_store
from zid_platform.modules.credential.infrastructure.registries import (
    issuer_registry,
    source_contracts,
)
from zid_platform.modules.verification.ports import pack_registry

NOW = datetime(2026, 11, 11, 12, tzinfo=UTC)
KEY = LocalSigner("assertion-test-key")
LIC = {
    "license_ref": "L-1",
    "regulator": "Sample State Licensing Board",
    "activity_scope": "legal.advice",
}
JURIS = {"country": "US", "subdivision": "CA"}


def uid() -> str:
    return str(uuid.uuid4())


def signer(claims):
    return sign_jws(KEY, claims, typ=ac.SIGNATURE_TYP)


def verified_claims(contract):
    _, claims = verify_jws(contract["signature"], keys=[KEY.public_jwk()], typ=ac.SIGNATURE_TYP)
    return claims


def gate(**over) -> checks.Gate:
    args = {
        "registry": issuer_registry(),
        "contracts": source_contracts(),
        "signer": signer,
        **over,
    }
    return checks.Gate(**args)


async def lic_check(c, tenant, subject, g=None, claimed=None, **kw):
    return await checks.run_check(
        c, tenant_id=tenant, subject_principal_id=subject, kind="PROFESSIONAL_LICENSE", claimed=claimed or LIC,
        jurisdiction=JURIS, profession=None, packs=pack_registry(), source=checks.SimulatorSource("test"),
        now=NOW, environment="test", gate=g or gate(), **kw,
    )  # fmt: skip


def run(coro):
    return asyncio.run(coro)


def test_a_check_returns_the_complete_section_08_1_contract_signed_and_equal_to_what_is_stored(
    rt_url: str,
) -> None:
    tenant, subject, ops = uid(), uid(), uid()

    async def go() -> None:
        async with engine(rt_url) as eng, tenant_transaction(eng, ctx(tenant)) as c:
            r = await lic_check(
                c, tenant, subject, gate(actor=ops, consumer="zoikorum", consent_ref="cons_9")
            )
            k = r.to_contract()
            assert k["assertion_id"] == f"asrt_{r.check_id}" and k["actor_ref"] == f"sub_{subject}"
            assert k["consumer"] == "zoikorum" and k["consent_ref"] == "cons_9"
            assert k["revocation_status"] == "GOOD" and k["subject_match"] == {
                "decision": "MATCHED",
                "method": "PRIMARY_SOURCE",
            }
            assert k["policy_ref"]["id"] == "sim-license" and len(k["evidence_receipts"]) == 1
            assert k["evidence_receipts"][0].startswith("rcpt_")
            # the five facts are still five separate fields
            for f in (
                "credential_status",
                "issuer_authenticity",
                "primary_source_verified",
                "professional_eligibility",
                "current_consumer_entitlement",
            ):
                assert f in k
            claims = verified_claims(k)  # the signature verifies with the platform's public key
            assert ac.binds(k, claims)
            stored = (await claim_store.load_check(c, r.check_id))["assertion"]
            assert (
                json.loads(json.dumps(k, default=str)) == stored
            )  # what was signed is what is stored and returned

    run(go())


def test_the_receipt_is_written_in_the_same_transaction_and_holds_commitments_not_claim_values(
    rt_url: str,
) -> None:
    tenant, subject, ops = uid(), uid(), uid()

    async def go() -> None:
        async with engine(rt_url) as eng, tenant_transaction(eng, ctx(tenant)) as c:
            r = await lic_check(c, tenant, subject, gate(actor=ops))
            sql = (
                "SELECT decision_type, outcome, action, resource_class, risk_class, represented_handle IS NOT NULL, "
                "input_commitment IS NOT NULL, body::text FROM evidence.decision_receipt WHERE decision_id = :d"
            )
            row = (await c.execute(text(sql), {"d": uuid.UUID(r.check_id)})).one()
            assert tuple(row[:7]) == (
                "CREDENTIAL",
                "VERIFIED",
                "credential.claim.verify",
                "credential-claim",
                "R2",
                True,
                True,
            )
            assert (
                "L-1" not in row[7]
                and "Sample State Licensing Board" not in row[7]
                and "legal.advice" not in row[7]
            )
            rid = r.to_contract()["evidence_receipts"][0].removeprefix("rcpt_")
            assert (
                await c.execute(
                    text("SELECT count(*) FROM evidence.decision_receipt WHERE receipt_id = :r"),
                    {"r": uuid.UUID(rid)},
                )
            ).scalar_one() == 1

    run(go())


def test_a_refused_check_writes_no_receipt_and_no_check(rt_url: str) -> None:
    tenant, subject = uid(), uid()

    async def go() -> None:
        async with engine(rt_url) as eng, tenant_transaction(eng, ctx(tenant)) as c:
            with pytest.raises(SourceError):
                await lic_check(c, tenant, subject, gate(purpose="MARKETING"))
            for table in ("evidence.decision_receipt", "cred.claim_check"):
                assert (await c.execute(text(f"SELECT count(*) FROM {table}"))).scalar_one() == 0, (
                    table
                )

    run(go())


def test_any_change_to_a_signed_contract_breaks_its_binding(rt_url: str) -> None:
    tenant, subject = uid(), uid()

    async def go() -> None:
        async with engine(rt_url) as eng, tenant_transaction(eng, ctx(tenant)) as c:
            k = (await lic_check(c, tenant, subject)).to_contract()
            claims = verified_claims(k)
            assert ac.binds(dict(reversed(list(k.items()))), claims)  # key order is irrelevant
            for tampered in (
                {**k, "decision": "NOT_VERIFIED"}, {**k, "jurisdiction": {"country": "US", "subdivision": "NY"}},
                {**k, "consumer": "rooms"}, {**k, "revocation_status": "REVOKED"}, {**k, "evidence_receipts": []},
            ):  # fmt: skip
                assert not ac.binds(tampered, claims)
            with pytest.raises(Exception):  # noqa: B017 - a forged signature never verifies
                verify_jws(
                    k["signature"][:-4] + "AAAA", keys=[KEY.public_jwk()], typ=ac.SIGNATURE_TYP
                )
            with pytest.raises(Exception):  # noqa: B017 - nor does a different key
                verify_jws(
                    k["signature"], keys=[LocalSigner("other").public_jwk()], typ=ac.SIGNATURE_TYP
                )

    run(go())


def test_without_a_signer_the_signature_is_null_and_a_stopped_check_says_it_matched_nobody(
    rt_url: str,
) -> None:
    tenant, subject = uid(), uid()

    async def go() -> None:
        async with engine(rt_url) as eng, tenant_transaction(eng, ctx(tenant)) as c:
            assert (await lic_check(c, tenant, subject, gate(signer=None))).to_contract()[
                "signature"
            ] is None
            stopped = await lic_check(
                c, tenant, subject, claimed={**LIC, "regulator": "Totally Unknown Board"}
            )
            k = stopped.to_contract()
            assert (
                k["subject_match"] == {"decision": "NOT_CHECKED", "method": "NOT_CHECKED"}
                and k["revocation_status"] == "UNKNOWN"
            )
            assert k["evidence_receipts"] and ac.binds(k, verified_claims(k))
            outcome = (
                await c.execute(
                    text(
                        "SELECT outcome, reason_codes FROM evidence.decision_receipt WHERE decision_id = :d"
                    ),
                    {"d": uuid.UUID(stopped.check_id)},
                )
            ).one()
            assert outcome[0] == "NOT_VERIFIED" and list(outcome[1]) == [
                "ISSUER_NOT_TRUSTED"
            ]  # a refusal is evidenced too

    run(go())


def test_glb_10_a_status_change_adds_a_resigned_check_its_own_receipt_and_the_named_event(
    rt_url: str,
) -> None:
    tenant, subject, ops = uid(), uid(), uid()

    async def go() -> None:
        async with engine(rt_url) as eng, tenant_transaction(eng, ctx(tenant)) as c:
            first = await lic_check(c, tenant, subject, gate(actor=ops, consumer="zoikorum"))
            ev = lc.SourceEvent(
                "e1", first.check_id, CredentialStatus.SUSPENDED, NOW + timedelta(hours=1), 5
            )
            changed, status = await svc.apply_source_event(
                c,
                tenant_id=tenant,
                check_id=first.check_id,
                event=ev,
                now=NOW,
                signer=signer,
                actor=ops,
            )
            assert changed and status == "SUSPENDED"
            live = (await svc.trust_store.live_checks(c, subject))[0]
            k = live["assertion"]
            assert str(live["check_id"]) != first.check_id
            assert (
                k["assertion_id"] == f"asrt_{live['check_id']}"
                and k["previous_check_id"] == first.check_id
            )
            assert (
                k["revocation_status"] == "SUSPENDED"
                and k["consumer"] == "zoikorum"
                and k["decision"] == "NOT_VERIFIED"
            )
            assert (
                k["evidence_receipts"] != first.to_contract()["evidence_receipts"]
                and len(k["evidence_receipts"]) == 1
            )
            assert ac.binds(k, verified_claims(k))  # re-signed over the NEW body
            assert not ac.binds(first.to_contract(), verified_claims(k))  # and not over the old one
            events = (
                (
                    await c.execute(
                        text(
                            "SELECT event_type FROM events.outbox_event WHERE event_type LIKE '%status_changed%'"
                        )
                    )
                )
                .scalars()
                .all()
            )
            assert events == ["com.zoiko.id.credential.status_changed.v1"]
            assert (
                await c.execute(
                    text("SELECT count(*) FROM evidence.decision_receipt WHERE decision_id = :d"),
                    {"d": live["check_id"]},
                )
            ).scalar_one() == 1

    run(go())


def test_a_disciplinary_check_is_signed_and_evidenced_too(rt_url: str) -> None:
    tenant, subject = uid(), uid()

    async def go() -> None:
        async with engine(rt_url) as eng, tenant_transaction(eng, ctx(tenant)) as c:
            r = await checks.run_disciplinary_check(
                c, tenant_id=tenant, subject_principal_id=subject, regulator="Sample State Licensing Board", reference="SIM-ADVERSE",
                jurisdiction=JURIS, packs=pack_registry(), source=checks.SimulatorSource("test", "simulator-disciplinary"), now=NOW,
                environment="test", gate=gate(consent_ref="consent-1", authorized_user=True, reviewer_assigned=True),
            )  # fmt: skip
            k = r.to_contract()
            assert k["decision"] == "VERIFIED" and k["disciplinary"] == "ADVERSE_FINDING"
            assert (
                k["consent_ref"] == "consent-1"
                and ac.binds(k, verified_claims(k))
                and len(k["evidence_receipts"]) == 1
            )

    run(go())
