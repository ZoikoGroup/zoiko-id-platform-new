"""Pure privacy and residency rules (F-W3, F-W5; PRIV-001 §4, §13-14; TENANCY-001 §5, §11, §19)."""

from __future__ import annotations

from datetime import UTC, datetime, timedelta

import pytest
from zid_platform.modules.privacy.domain.model import (
    Activity,
    LawfulBasis,
    PrivacyError,
    Purpose,
    RightsType,
    check_processing,
    initial_verification,
    require_disclosure_verification,
    retention_outcome,
    summarize,
)
from zid_platform.modules.privacy.infrastructure.packs import packs, parse_pack
from zid_platform.modules.tenant.domain.residency import (
    Catalog,
    Cell,
    Residency,
    ResidencyError,
    TransferRule,
    check_placement,
    check_storage,
    check_transfer,
    minimize,
    operator_jurisdictions,
)
from zid_platform.modules.tenant.infrastructure.catalog import catalog

NOW = datetime(2026, 10, 21, tzinfo=UTC)
CAT = Catalog(
    cells={
        "uk-1": Cell(
            "uk-1", "eu-west-2", "GB", "UK", ("SRP-0", "SRP-1", "SRP-2"), ("uk-2",), False
        ),
        "uk-2": Cell("uk-2", "eu-west-1", "GB", "UK", ("SRP-0", "SRP-1"), (), False),
        "eu-1": Cell("eu-1", "eu-central-1", "DE", "EU", ("SRP-0", "SRP-1"), ("eu-1-dr",), False),
        "eu-1-dr": Cell("eu-1-dr", "eu-west-1", "IE", "EU", (), (), True),
    },
    profiles={"SRP-0": {}, "SRP-1": {}, "SRP-2": {}, "SRP-3": {}},
)


def residency(profile: str = "SRP-1", ops: tuple[str, ...] = ()) -> Residency:
    return Residency("t", profile, "uk-1", ("uk-2",), (), ops, "PLATFORM_MANAGED", "uk-gdpr", 1, 1)


def test_repository_catalog_and_packs_load() -> None:
    assert {"uk-1", "eu-1", "eu-1-dr"} <= set(catalog().cells)
    assert {"eu-gdpr", "uk-gdpr", "us-ca-ccpa"} <= set(packs())
    # nothing counsel has not approved is deployable, and no deadline is invented
    assert all(not p.deployable and p.deadlines is None for p in packs().values())


def test_pack_deadlines_come_only_from_counsel() -> None:
    text = (
        "# approval_ref: COUNSEL-2026-11\n# version: 1.0.0\npack_id: x\n"
        "rights_response_deadlines: {default: 30, ERASURE: 45}\n"
    )
    p = parse_pack(text)
    assert p.deployable and p.version == "1.0.0"
    assert p.deadline(RightsType.ACCESS, NOW) == (NOW + timedelta(days=30), "x@1.0.0")
    assert p.deadline(RightsType.ERASURE, NOW)[0] == NOW + timedelta(days=45)
    pending = parse_pack("pack_id: y\nrights_response_deadlines: COUNSEL\n")
    assert pending.deadline(RightsType.ACCESS, NOW) == (None, "COUNSEL_PENDING")


def test_processing_check_is_default_deny() -> None:
    act = Activity(
        "a",
        "t",
        "ACT",
        1,
        ("P",),
        ("D1", "D2"),
        LawfulBasis.CONTRACT,
        ("tenant",),
        "uk-gdpr",
        None,
        "ACTIVE",
        True,
    )
    pur = Purpose("p", "P", 1, ("D1",), ("tenant",), (), (), "ACTIVE")
    base = {
        "activity": act,
        "purpose": pur,
        "purpose_code": "P",
        "data_category": "D1",
        "recipient_class": None,
        "consent_id": None,
        "restricted_purposes": [],
        "activity_code": "ACT",
    }
    assert check_processing(**base).allowed
    assert (
        check_processing(**{**base, "data_category": "D2"}).reason
        == "PRIVACY_DATA_CATEGORY_NOT_ALLOWED"
    )
    assert (
        check_processing(**{**base, "recipient_class": "ad-network"}).reason
        == "PRIVACY_RECIPIENT_NOT_ALLOWED"
    )
    assert (
        check_processing(**{**base, "restricted_purposes": [None]}).reason
        == "PRIVACY_PROCESSING_RESTRICTED"
    )
    assert check_processing(**{**base, "activity": None}).reason == "PRIVACY_ACTIVITY_NOT_ACTIVE"


def test_verification_is_proportional() -> None:
    assert (
        initial_verification(
            RightsType.OPT_OUT_SALE_SHARING, self_request=False, authn_level=0, auth_age=None
        )[0]
        == "NOT_REQUIRED"
    )
    assert initial_verification(
        RightsType.ACCESS, self_request=True, authn_level=2, auth_age=timedelta(minutes=3)
    ) == ("VERIFIED", 2)
    assert (
        initial_verification(
            RightsType.ACCESS, self_request=True, authn_level=2, auth_age=timedelta(hours=2)
        )[0]
        == "PENDING"
    )
    with pytest.raises(PrivacyError):
        require_disclosure_verification(RightsType.PORTABILITY, "VERIFIED", 1)
    assert retention_outcome(due=NOW, held=True, now=NOW) == "HELD"
    assert retention_outcome(due=NOW, held=False, now=NOW) == "DUE"


def test_fulfilment_never_completes_with_pending_stages() -> None:
    assert summarize([{"state": "DONE"}, {"state": "PENDING_PROCESSOR_ACK"}]).value == "FULFILLING"
    assert summarize([{"state": "DONE"}, {"state": "FAILED"}]).value == "FULFILLING"
    assert (
        summarize([{"state": "DONE"}, {"state": "RETAINED_RESTRICTED"}]).value == "PARTIALLY_DENIED"
    )
    assert summarize([{"state": "DONE"}, {"state": "SCHEDULED"}]).value == "COMPLETED"


def test_placement_and_storage() -> None:
    check_placement(CAT, "SRP-1", "uk-1", ["uk-2"])
    with pytest.raises(ResidencyError, match="RESIDENCY_PROFILE_UNSUPPORTED"):
        check_placement(CAT, "SRP-3", "uk-1", [])
    with pytest.raises(ResidencyError, match="BACKUP_REGION_DENIED"):
        check_placement(CAT, "SRP-1", "uk-1", ["eu-1"])
    with pytest.raises(ResidencyError, match="CELL_NOT_AUTHORITATIVE"):
        check_placement(CAT, "SRP-0", "eu-1-dr", [])
    r = residency()
    check_storage(CAT, r, "DC2", "uk-2")  # same legal zone
    check_storage(CAT, r, "DC0", "eu-1")
    with pytest.raises(ResidencyError, match="DATA_CLASS_PLACEMENT_DENIED"):
        check_storage(CAT, r, "DC2", "eu-1")
    with pytest.raises(ResidencyError, match="DATA_CLASS_PLACEMENT_DENIED"):
        check_storage(CAT, residency("SRP-2", ("GB",)), "DC3", "uk-2")  # sovereign: home cell only


def test_transfers_need_an_exact_approved_rule() -> None:
    r = residency()
    rule = TransferRule(
        "rule-1",
        "DC2",
        "eu-1",
        "SUPPORT",
        "uk_idta",
        "uk-gdpr",
        ("ticket_id",),
        NOW - timedelta(days=1),
        None,
        "ACTIVE",
    )

    def go(**kw: object) -> object:
        args = {
            "data_class": "DC2",
            "destination_cell_id": "eu-1",
            "purpose_code": "SUPPORT",
            "rules": [rule],
            "pack_mechanisms": ("uk_idta",),
            "now": NOW,
            **kw,
        }
        return check_transfer(CAT, r, **args)  # type: ignore[arg-type]

    assert go().allowed  # type: ignore[attr-defined]
    assert not go(purpose_code="ANALYTICS").allowed  # type: ignore[attr-defined]
    assert go(pack_mechanisms=()).code == "RESIDENCY_TRANSFER_DENIED"  # type: ignore[attr-defined]
    assert go(rules=[]).code == "RESIDENCY_TRANSFER_DENIED"  # type: ignore[attr-defined]
    assert go(data_class="KEY_MATERIAL").code == "KEY_BOUNDARY_MISMATCH"  # type: ignore[attr-defined]
    assert go(destination_cell_id="uk-2", rules=[]).allowed  # type: ignore[attr-defined]
    assert minimize({"ticket_id": 1, "email": "x"}, ("ticket_id",)) == {"ticket_id": 1}


def test_sovereign_tenants_restrict_operators() -> None:
    assert operator_jurisdictions(CAT, residency("SRP-1"), []) == ()  # unrestricted
    assert operator_jurisdictions(CAT, residency("SRP-2", ("GB",)), []) == ("GB",)
    assert operator_jurisdictions(CAT, residency("SRP-2", ()), []) == ("GB",)  # home jurisdiction
    assert operator_jurisdictions(CAT, residency("SRP-2", ("GB",)), ["IE"]) == ("--",)  # nobody
