"""The trust-source allowlist at validation, on PostgreSQL (WF03 T02, T03, T38; CR-119): with enforcement on, a real signed master list from an authority that is not recorded for its country is REJECTED and never becomes
a candidate; with it off, the same list validates and carries an advisory."""

from __future__ import annotations

import asyncio
import uuid

from conftest import ctx, engine
from cryptography.hazmat.primitives.serialization import Encoding
from epassport_fixture import make_chip
from pdf_signing import NOW
from test_trust_store_pg import list_for
from zid_db import tenant_transaction
from zid_platform.modules.evidence.application import csca_master_list as ml
from zid_platform.modules.evidence.application import trust_store as ts
from zid_platform.modules.evidence.application.trust_store import _country
from zid_platform.modules.evidence.domain.trust_store import SourcePolicy, TrustSource

SOURCE = "https://pki.example.gov.uk/masterlist.ml"


def run(rt_url: str, policy_for) -> dict:
    tenant, stager, other = str(uuid.uuid4()), str(uuid.uuid4()), str(uuid.uuid4())
    chip = make_chip()
    blob, anchor = list_for(chip.csca.cert)
    country = _country(chip.csca.cert)

    async def go() -> dict:
        async with engine(rt_url) as eng, tenant_transaction(eng, ctx(tenant)) as c:
            staged = await ts.stage(
                c, tenant_id=tenant, actor=stager, master_list=blob, anchors=[anchor.cert.public_bytes(Encoding.PEM)],
                confirmed_anchor_sha256=[ml.fingerprint(anchor.cert)], source_uri=SOURCE, source_note=None, now=NOW,
            )  # fmt: skip
            return await ts.validate(
                c,
                tenant_id=tenant,
                actor=other,
                package_id=str(staged["package_id"]),
                now=NOW,
                source_policy=policy_for(country),
            )

    return asyncio.run(go())


def test_enforcement_rejects_a_list_from_an_unrecorded_authority_and_names_the_country(
    rt_url: str,
) -> None:
    out = run(rt_url, lambda country: SourcePolicy(True, ()))
    assert (
        out["state"] == "REJECTED" and out["list_status"] == "VERIFIED"
    )  # the signature was fine; the publisher is not recorded
    assert any(r.startswith("NO_AUTHORITY_RECORDED_FOR_COUNTRY:") for r in out["list_reasons"])
    assert out["decision_reason"].startswith("SOURCE_NOT_ALLOWLISTED")


def test_enforcement_rejects_the_wrong_host_for_a_recorded_country(rt_url: str) -> None:
    out = run(
        rt_url, lambda country: SourcePolicy(True, (TrustSource(country, "other.example.org"),))
    )
    assert out["state"] == "REJECTED" and any(
        r.startswith("AUTHORITY_NOT_ALLOWLISTED:") for r in out["list_reasons"]
    )


def test_enforcement_accepts_the_recorded_authority(rt_url: str) -> None:
    out = run(
        rt_url, lambda country: SourcePolicy(True, (TrustSource(country, "pki.example.gov.uk"),))
    )
    assert out["state"] == "VALIDATED" and not [r for r in out["list_reasons"] if "AUTHORITY" in r]


def test_with_enforcement_off_the_list_validates_and_carries_an_advisory(rt_url: str) -> None:
    out = run(rt_url, lambda country: SourcePolicy(False, ()))
    assert out["state"] == "VALIDATED"
    assert any(
        r.startswith("ADVISORY:NO_AUTHORITY_RECORDED_FOR_COUNTRY:") for r in out["list_reasons"]
    )
