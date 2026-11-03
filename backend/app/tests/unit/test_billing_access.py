"""Billing permissions (ZID-COM-CL-002 and ZID-UX-COM-010): the Owner holds the ten keys, nobody else holds one without a grant, a grant
never carries the right to grant, and every refusal is a code. The decision is pure, so the rules are proved here without a database."""

from __future__ import annotations

from datetime import UTC, datetime, timedelta

import pytest
from zid_platform.modules.tenant.domain.billing_access import (
    BUNDLES,
    OWNER_ONLY,
    PERMISSIONS,
    BillingAccessError,
    Bundle,
    Grant,
    check_grant,
    check_revoke,
    decide,
    effective_permissions,
    validate_catalogue,
)

NOW = datetime(2026, 11, 3, 12, 0, tzinfo=UTC)


def grant(bundle: Bundle, **kw: object) -> Grant:
    return Grant("g1", "p-1", bundle, BUNDLES[bundle], **kw)  # type: ignore[arg-type]


def test_the_catalogue_is_the_ten_keys_and_no_bundle_carries_the_right_to_grant() -> None:
    """CL02: ten permission keys; Billing Admin, Billing Viewer and the invoice reader are bundles of them, never the delegation."""
    assert len(PERMISSIONS) == 10 and OWNER_ONLY == {"billing.access.delegate"}
    validate_catalogue()
    assert all(not (keys & OWNER_ONLY) for keys in BUNDLES.values())
    assert BUNDLES[Bundle.BILLING_VIEWER] <= BUNDLES[Bundle.BILLING_ADMIN]
    assert BUNDLES[Bundle.INVOICE_READER] <= BUNDLES[Bundle.BILLING_VIEWER]


def test_an_administrator_has_no_billing_access_by_being_one() -> None:
    """CL02-P01 and WF10-C00-02: Admin, Approver, Operator and Viewer without a grant hold no billing permission."""
    for role in ("ADMIN", "APPROVER", "OPERATOR", "VIEWER", "MEMBER"):
        assert effective_permissions((role,), (), NOW) == frozenset()
        for key in PERMISSIONS:
            assert decide((role,), (), key, NOW).via in ("NO_BILLING_GRANT", "OWNER_ONLY")


def test_the_owner_holds_every_key_and_a_grant_holds_exactly_its_bundle() -> None:
    assert effective_permissions(("OWNER",), (), NOW) == frozenset(PERMISSIONS)
    viewer = grant(Bundle.BILLING_VIEWER)
    assert effective_permissions(("ADMIN",), (viewer,), NOW) == BUNDLES[Bundle.BILLING_VIEWER]
    assert not decide(("ADMIN",), (viewer,), "billing.payment.initiate", NOW).allowed
    assert decide(("ADMIN",), (viewer,), "billing.invoice.read", NOW).grant_id == "g1"


def test_a_delegated_invoice_reader_cannot_reach_a_payment_method_or_checkout() -> None:
    """CL02-P03."""
    reader = grant(Bundle.INVOICE_READER)
    for key in (
        "billing.payment.initiate",
        "billing.payment_method.read_masked",
        "billing.payment_method.manage",
        "billing.profile.manage",
        "billing.subscription.request_change",
    ):
        assert not decide(("VIEWER",), (reader,), key, NOW).allowed


def test_no_grant_carries_the_right_to_grant_even_a_hand_built_one() -> None:
    """A Billing Admin cannot assign another Billing Admin: delegation is the Owner's alone, whatever a row says."""
    admin = grant(Bundle.BILLING_ADMIN)
    assert not decide(("ADMIN",), (admin,), "billing.access.delegate", NOW).allowed
    forged = Grant("g2", "p", Bundle.BILLING_ADMIN, frozenset(PERMISSIONS), None, None)
    assert decide(("ADMIN",), (forged,), "billing.access.delegate", NOW).via == "OWNER_ONLY"


def test_a_revoked_or_expired_grant_gives_nothing_and_an_unknown_key_is_refused() -> None:
    """CL02-P13: revocation denies at once; the decision reads the grant afresh, so there is no cache to outlive it."""
    assert not decide(
        ("ADMIN",), (grant(Bundle.BILLING_ADMIN, revoked_at=NOW),), "billing.invoice.read", NOW
    ).allowed
    assert not decide(
        ("ADMIN",), (grant(Bundle.BILLING_ADMIN, expires_at=NOW),), "billing.invoice.read", NOW
    ).allowed
    assert decide(("ADMIN",), (grant(Bundle.BILLING_ADMIN, expires_at=NOW + timedelta(seconds=1)),), "billing.invoice.read", NOW).allowed  # fmt: skip
    assert decide(("OWNER",), (), "billing.everything", NOW).via == "UNKNOWN_PERMISSION"


def _check(**over: object) -> Bundle:
    args: dict[str, object] = {
        "actor_roles": ("OWNER",),
        "actor_person": "alice",
        "grantee_person": "bob",
        "grantee_is_member": True,
        "bundle": "BILLING_ADMIN",
        "now": NOW,
        "expires_at": None,
    }
    args.update(over)
    return check_grant(**args)  # type: ignore[arg-type]


@pytest.mark.parametrize(
    ("over", "code"),
    [
        ({"actor_roles": ("ADMIN",)}, "BILLING_DELEGATION_OWNER_ONLY"),
        ({"actor_roles": ("ADMIN", "APPROVER")}, "BILLING_DELEGATION_OWNER_ONLY"),
        ({"grantee_person": "alice"}, "BILLING_SELF_GRANT"),
        ({"grantee_is_member": False}, "BILLING_GRANTEE_NOT_MEMBER"),
        ({"bundle": "OWNER"}, "BILLING_BUNDLE_UNKNOWN"),
        ({"bundle": "billing.access.delegate"}, "BILLING_BUNDLE_UNKNOWN"),
        ({"expires_at": NOW}, "BILLING_EXPIRY_PAST"),
    ],
)
def test_who_may_give_billing_access_and_to_whom(over: dict[str, object], code: str) -> None:
    """CL02-P05 (no self-grant, Owner only) and WF10-C00-03, C00-08."""
    with pytest.raises(BillingAccessError) as e:
        _check(**over)
    assert e.value.code == code


def test_an_owner_gives_a_bundle_to_another_member_and_only_an_owner_takes_it_away() -> None:
    assert _check() is Bundle.BILLING_ADMIN
    assert (
        _check(bundle="BILLING_VIEWER", expires_at=NOW + timedelta(days=30))
        is Bundle.BILLING_VIEWER
    )
    check_revoke(actor_roles=("OWNER",))
    with pytest.raises(BillingAccessError):
        check_revoke(actor_roles=("ADMIN",))
