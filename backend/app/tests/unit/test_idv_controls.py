"""WF03 controls around the decision (CR-110)."""

from __future__ import annotations

from datetime import UTC, datetime, timedelta

from zid_platform.modules.verification.domain.idv_controls import (
    BiometricBasis,
    RetryPolicy,
    TrustSource,
    biometrics_allowed,
    impacted_cases,
    retry_left,
    source_accepted,
)

T = datetime(2026, 11, 17, tzinfo=UTC)
FULL = BiometricBasis("CONSENT", "DPIA-1", "n-2", "PAD-LAB-1")


def test_biometrics_are_off_until_every_control_is_recorded() -> None:
    ok, why = biometrics_allowed(
        BiometricBasis(None, None, None, None),
        self_hosted=True,
        notice_shown_version=None,
        consent_at=None,
        withdrawn_at=None,
    )
    assert not ok and {
        "NO_LAWFUL_BASIS_RECORDED",
        "NO_IMPACT_ASSESSMENT",
        "NO_NOTICE_PUBLISHED",
        "NO_INDEPENDENT_PAD_EVALUATION",
    } <= set(why)
    assert biometrics_allowed(
        FULL, self_hosted=True, notice_shown_version="n-2", consent_at=T, withdrawn_at=None
    ) == (True, ())


def test_consent_and_the_current_notice_are_required_and_withdrawal_ends_it() -> None:
    assert biometrics_allowed(
        FULL, self_hosted=False, notice_shown_version="n-1", consent_at=T, withdrawn_at=None
    )[1] == ("CURRENT_NOTICE_NOT_SHOWN",)
    assert biometrics_allowed(
        FULL, self_hosted=False, notice_shown_version="n-2", consent_at=None, withdrawn_at=None
    )[1] == ("CONSENT_NOT_GIVEN",)
    assert biometrics_allowed(
        FULL,
        self_hosted=False,
        notice_shown_version="n-2",
        consent_at=T,
        withdrawn_at=T + timedelta(hours=1),
    )[1] == ("CONSENT_WITHDRAWN",)
    assert biometrics_allowed(
        FULL,
        self_hosted=False,
        notice_shown_version="n-2",
        consent_at=T + timedelta(hours=2),
        withdrawn_at=T + timedelta(hours=1),
    )[0]


def test_a_provider_carried_pad_needs_no_self_hosted_evaluation() -> None:
    b = BiometricBasis("CONTRACT", "DPIA-1", "n-2", None)
    assert biometrics_allowed(
        b, self_hosted=False, notice_shown_version="n-2", consent_at=None, withdrawn_at=None
    )[0]
    assert biometrics_allowed(
        b, self_hosted=True, notice_shown_version="n-2", consent_at=None, withdrawn_at=None
    )[1] == ("NO_INDEPENDENT_PAD_EVALUATION",)


def test_trust_material_is_accepted_only_from_an_allowlisted_authority() -> None:
    allow = (TrustSource("GB", "HMPO", "ROOT_LIST"),)
    assert source_accepted(allow, "GB", "HMPO", "ROOT_LIST") == (True, "ACCEPTED")
    assert source_accepted(allow, "GB", "A mirror", "ROOT_LIST") == (
        False,
        "AUTHORITY_NOT_ALLOWLISTED",
    )
    assert source_accepted(allow, "FR", "HMPO", "ROOT_LIST") == (
        False,
        "NO_AUTHORITY_RECORDED_FOR_COUNTRY",
    )
    assert source_accepted(allow, "GB", "HMPO", "REVOCATION_LIST") == (
        False,
        "NO_AUTHORITY_RECORDED_FOR_COUNTRY",
    )


def test_a_trust_change_names_exactly_the_cases_it_touches() -> None:
    cases = {"c3": ("GB", "s1"), "c1": ("GB", "s1"), "c2": ("GB", "s2"), "c4": ("FR", "s1")}
    assert impacted_cases(cases, "GB", frozenset({"s1"})) == ("c1", "c3")
    assert impacted_cases(cases, "DE", frozenset({"s1"})) == ()


def test_retries_stop_at_the_markets_ceiling_and_an_unset_ceiling_allows_none() -> None:
    p = RetryPolicy("GB", "KYC", 3, 5)
    assert retry_left(p, kind="NFC", used=2) == (True, "OK")
    assert retry_left(p, kind="NFC", used=3) == (False, "RETRY_CEILING_REACHED")
    assert retry_left(p, kind="CAPTURE", used=4)[0]
    assert retry_left(RetryPolicy("GB", "KYC", 0, 0), kind="NFC", used=0) == (
        False,
        "RETRY_CEILING_NOT_CONFIGURED",
    )
