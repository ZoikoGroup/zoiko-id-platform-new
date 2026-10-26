"""A reusable identity relied on from another cell discloses only the approved assertion/commitment (TEN-24).

The identity's home cell answers a relying tenant in another cell through the transfer gateway: the destination must be
allowed to receive it, an approved rule narrows the fields, and the source profile (provider, evidence, reasons, the
claim value) is never part of what is sent.
"""

from __future__ import annotations

import asyncio
import uuid
from datetime import UTC, datetime, timedelta
from typing import Any

import pytest
from conftest import ctx, engine
from zid_db import tenant_transaction
from zid_platform.modules.evidence.application.runtime import configure_runtime, development_runtime
from zid_platform.modules.tenant.application import residency
from zid_platform.modules.verification.application import service
from zid_platform.modules.verification.domain.shared_identity import (
    SHARED_FIELDS,
    SOURCE_PROFILE_FIELDS,
    SharedIdentityError,
    shareable,
)
from zid_platform.modules.verification.infrastructure import verification_store as store

NOW = datetime.now(UTC)


def _row(**over: Any) -> dict[str, Any]:
    base: dict[str, Any] = {
        "assertion_id": uuid.uuid4(),
        "claim_type": "identity.name",
        "assurance_level": "IAL-Z2",
        "status": "VALID",
        "performed_at": NOW - timedelta(days=1),
        "valid_until": NOW + timedelta(days=300),
        "purposes": ["KYC_REUSE"],
        "claim_value_commitment": bytes(range(32)),
        # the source profile: must never be part of what is shared
        "provider_id": "onfido",
        "provider_reference": "chk_8841",
        "evidence_types": ["PASSPORT"],
        "verification_method": "DOCUMENT_NFC",
        "reason_codes": ["MRZ_OK"],
        "jurisdiction": "GB",
        "session_id": uuid.uuid4(),
    }
    return {**base, **over}


def test_only_the_approved_assertion_and_commitment_leave_the_home_cell() -> None:
    out = shareable(
        [_row()], home_cell_id="uk-1", purpose="KYC_REUSE", requested=["identity.name"], now=NOW
    )
    assert len(out) == 1 and set(out[0]) == set(SHARED_FIELDS)
    assert not set(out[0]) & SOURCE_PROFILE_FIELDS
    assert out[0]["commitment"] == bytes(range(32)).hex() and out[0]["home_cell_id"] == "uk-1"
    flat = repr(out)
    for secret in ("onfido", "chk_8841", "PASSPORT", "DOCUMENT_NFC", "MRZ_OK"):
        assert secret not in flat


def test_only_what_was_asked_for_and_may_be_relied_on_is_shared() -> None:
    rows = [
        _row(claim_type="identity.address"),  # not requested
        _row(status="REVOKED"),
        _row(valid_until=NOW - timedelta(seconds=1)),  # expired
        _row(purposes=["MARKETING"]),  # profile does not allow this purpose
        _row(assurance_level="IAL-Z1"),  # below the level asked for
    ]
    assert (
        shareable(
            rows,
            home_cell_id="uk-1",
            purpose="KYC_REUSE",
            requested=["identity.name"],
            now=NOW,
            min_level="IAL-Z2",
        )
        == []
    )


def test_an_approved_rule_can_narrow_the_fields_further() -> None:
    out = shareable(
        [_row()],
        home_cell_id="uk-1",
        purpose="KYC_REUSE",
        requested=["identity.name"],
        now=NOW,
        allowlist=(
            "assertion_id",
            "claim_type",
            "assurance_level",
            "provider_id",
        ),  # provider_id is never shareable
    )
    assert set(out[0]) == {"assertion_id", "claim_type", "assurance_level"}


def test_the_transfer_gateway_decides_whether_another_cell_may_receive_it(
    rt_url: str, monkeypatch: pytest.MonkeyPatch
) -> None:
    tenant, admin, second, person = (str(uuid.uuid4()) for _ in range(4))
    configure_runtime(development_runtime("uk-1"))
    service.configure_transfer_gate(residency.authorize_transfer)

    async def rows(conn: Any, principal_id: str) -> list[dict[str, Any]]:
        return [_row()]

    monkeypatch.setattr(store, "assertions_for", rows)

    async def ask(c: Any, cell: str) -> dict[str, Any]:
        return await service.shared_assertions(
            c,
            tenant_id=tenant,
            principal_id=person,
            home_cell_id="uk-1",
            relying_cell_id=cell,
            purpose="KYC_REUSE",
            requested=["identity.name"],
            now=datetime.now(UTC),  # after the rule took effect
        )

    async def run() -> None:
        async with engine(rt_url) as eng, tenant_transaction(eng, ctx(tenant)) as c:
            await residency.set_residency(
                c, tenant, profile="SRP-1", home_cell_id="uk-1", recovery_cells=[], prohibited_cells=[],
                operator_juris=[], key_profile="PLATFORM_MANAGED", jurisdiction_pack_id="uk-gdpr",
                approval_ref="LEGAL-1", actor=admin,
            )  # fmt: skip
            home = await ask(c, "uk-1")  # the home cell itself
            assert set(home["assertions"][0]) == set(SHARED_FIELDS)

            with pytest.raises(SharedIdentityError, match="RESIDENCY_TRANSFER_DENIED"):
                await ask(c, "eu-1")  # another zone, no approved rule: nothing is shared

            await residency.add_rule(
                c, tenant, data_class="DC2", destination_cell_id="eu-1", purpose_code="KYC_REUSE",
                mechanism="uk_idta", field_allowlist=["assertion_id", "claim_type", "assurance_level", "commitment"],
                approval_ref="LEGAL-3", created_by=admin, approved_by=second, valid_to=None,
            )  # fmt: skip
            shared = await ask(c, "eu-1")
            assert set(shared["assertions"][0]) == {
                "assertion_id",
                "claim_type",
                "assurance_level",
                "commitment",
            }
            assert not set(shared["assertions"][0]) & SOURCE_PROFILE_FIELDS

    try:
        asyncio.run(run())
    finally:
        service.configure_transfer_gate(None)
        configure_runtime(None)
