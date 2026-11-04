"""Progressive verification (ZID-EDR-006; slice L-6, CR-056). Each test names the acceptance rows it supports.

The tiers are operational risk tiers. A basic account needs only the email address; the document and selfie are asked for only to reach R1; an address
or a phone only for a stated reason; and a phone code, an email confirmation, single sign-on, a selfie alone or domain control is never proof of who a
person is."""

from __future__ import annotations

from copy import deepcopy
from datetime import UTC, datetime, timedelta
from typing import Any

import pytest
import yaml
from zid_platform.modules.verification.domain import tiers as t
from zid_platform.modules.verification.infrastructure.tier_policy import CONFIG, tier_policy

POLICY = tier_policy()
NOW = datetime(2026, 11, 4, 10, 0, tzinfo=UTC)


def _doc() -> dict[str, Any]:
    return deepcopy(
        yaml.safe_load((CONFIG / "verification" / "tier-policy.yaml").read_text(encoding="utf-8"))
    )


# ------------------------------------------------------------------------------------------------------------------ the tiers


def test_a_basic_account_needs_the_email_and_nothing_else() -> None:
    """EDR06-01: no ID, selfie, address or phone to register."""
    assert POLICY.needs(t.Tier.R0) == ("EMAIL",)
    r = t.tier_of(t.Facts(email_proved=True), POLICY)
    assert r.tier is t.Tier.R0 and r.label == "Not identity verified"
    assert t.required_checks(t.Tier.R0, POLICY)[0] == t.CheckRequirement(
        "EMAIL", True, "NEEDED_FOR_R0"
    )
    assert not [
        c for c in t.required_checks(t.Tier.R0, POLICY) if c.required and c.check != "EMAIL"
    ]


def test_without_a_confirmed_email_there_is_no_tier_yet() -> None:
    r = t.tier_of(t.Facts(), POLICY)
    assert (
        r.tier is t.Tier.NONE
        and r.next_tier is t.Tier.R0
        and r.missing == ("EMAIL",)
        and r.reasons == ("EMAIL_NOT_PROVED",)
    )


def test_a_verified_person_exists_only_when_the_proofing_policy_succeeded() -> None:
    """EDR06-02: nothing but a proofing result makes R1; a strong sign-in or a company check cannot stand in for it."""
    assert (
        t.tier_of(
            t.Facts(
                email_proved=True,
                strong_authentication=True,
                company_verified=True,
                mandate_evidence=True,
            ),
            POLICY,
        ).tier
        is t.Tier.R0
    )
    assert t.tier_of(t.Facts(email_proved=True, proofed=True), POLICY).tier is t.Tier.R1


def test_proofing_alone_grants_no_authority() -> None:
    """EDR06-03: a proofed person is R1, never an owner, a director or an approver: R2 needs a separate company check and mandate evidence."""
    r = t.tier_of(t.Facts(email_proved=True, proofed=True, strong_authentication=True), POLICY)
    assert (
        r.tier is t.Tier.R1
        and r.next_tier is t.Tier.R2
        and r.missing == ("COMPANY_CHECK", "MANDATE_EVIDENCE")
    )
    full = t.Facts(
        email_proved=True,
        proofed=True,
        strong_authentication=True,
        company_verified=True,
        mandate_evidence=True,
    )
    assert t.tier_of(full, POLICY).tier is t.Tier.R2


def test_the_critical_tier_needs_a_fresh_sign_in_and_an_independent_approval() -> None:
    base = {
        "email_proved": True,
        "proofed": True,
        "strong_authentication": True,
        "company_verified": True,
        "mandate_evidence": True,
    }
    assert t.tier_of(t.Facts(**base, fresh_strong_sign_in=True), POLICY).tier is t.Tier.R2
    assert t.tier_of(t.Facts(**base, independent_approval=True), POLICY).tier is t.Tier.R2
    top = t.tier_of(t.Facts(**base, fresh_strong_sign_in=True, independent_approval=True), POLICY)
    assert top.tier is t.Tier.R3 and top.next_tier is None and top.missing == ()


def test_a_higher_tier_is_never_reached_by_skipping_a_lower_one() -> None:
    r = t.tier_of(
        t.Facts(
            proofed=True, company_verified=True, mandate_evidence=True, strong_authentication=True
        ),
        POLICY,
    )  # no email proof
    assert r.tier is t.Tier.NONE


# --------------------------------------------------------------------------------------------------------- what is asked, and why


def test_an_address_is_required_only_for_a_stated_reason() -> None:
    """EDR06-08."""
    none = {c.check: c for c in t.required_checks(t.Tier.R1, POLICY)}
    assert none["ADDRESS"].required is False and none["ADDRESS"].reason is None
    for need in (t.Need.STATUTORY, t.Need.REGULATED, t.Need.CONTRACT, t.Need.DOCUMENTED_RISK):
        c = {x.check: x for x in t.required_checks(t.Tier.R1, POLICY, needs=frozenset({need}))}[
            "ADDRESS"
        ]
        assert c.required and c.reason == need.value
    with pytest.raises(t.TierError) as e:
        t.check_optional_request("ADDRESS", None, POLICY)
    assert e.value.code == "ADDRESS_NEEDS_REASON"
    with pytest.raises(t.TierError):
        t.check_optional_request(
            "ADDRESS", t.Need.WORKFLOW_POSSESSION, POLICY
        )  # a phone reason is not an address reason
    t.check_optional_request("ADDRESS", t.Need.CONTRACT, POLICY)


def test_an_address_the_customer_typed_is_never_shown_as_verified() -> None:
    """EDR06-08."""
    assert (
        t.address_label(entered=True, verified=False, policy=POLICY)
        == "Entered by you, not verified"
    )
    assert t.address_label(entered=True, verified=True, policy=POLICY) == "Verified"
    assert t.address_label(entered=False, verified=False, policy=POLICY) == "Not provided"


def test_a_phone_is_asked_for_only_when_a_workflow_depends_on_possession_and_proves_nobody() -> (
    None
):
    """EDR06-09."""
    assert {c.check: c.required for c in t.required_checks(t.Tier.R1, POLICY)}["PHONE"] is False
    assert {
        c.check: c.required
        for c in t.required_checks(t.Tier.R1, POLICY, needs=frozenset({t.Need.WORKFLOW_POSSESSION}))
    }["PHONE"] is True
    assert POLICY.optional["PHONE"]["never_proves_identity"] is True
    with pytest.raises(t.TierError):
        t.check_optional_request("PHONE", t.Need.CONTRACT, POLICY)


def test_the_default_remote_route_is_a_document_and_a_live_selfie() -> None:
    names = [c.check for c in t.required_checks(t.Tier.R1, POLICY) if c.required]
    assert names == ["EMAIL", "GOVERNMENT_ID", "SELFIE_LIVENESS"]


def test_unknown_needs_checks_and_targets_are_refused() -> None:
    with pytest.raises(t.TierError):
        t.check_optional_request("RETINA", t.Need.CONTRACT, POLICY)
    with pytest.raises(t.TierError):
        t.required_checks(t.Tier.NONE, POLICY)
    with pytest.raises(t.TierError):
        t.required_checks(t.Tier.R1, POLICY, alternative="NO_SUCH_PROCESS")


# ------------------------------------------------------------------------------------------------------------ no false assurance


@pytest.mark.parametrize(
    "signal",
    [
        "PHONE_OTP",
        "SMS",
        "EMAIL_CONFIRMATION",
        "SSO",
        "SELFIE_ALONE",
        "DOMAIN_CONTROL",
        "phone_otp",
        "SOMETHING_ELSE",
    ],
)
def test_what_is_not_proof_is_never_counted_as_proof(signal: str) -> None:
    """EDR06-09 and the no-false-assurance rule."""
    assert t.counts_as_identity_proof(signal, POLICY) is False


def test_only_a_proofing_result_assisted_proofing_or_an_accepted_credential_is_proof() -> None:
    for signal in ("PROOFING_RESULT", "ASSISTED_PROOFING_RESULT", "ACCEPTED_EQUIVALENT_CREDENTIAL"):
        assert t.counts_as_identity_proof(signal, POLICY) is True


def test_a_phone_code_can_never_authorize_a_critical_change_on_its_own() -> None:
    """EDR06-09."""
    for signal in ("PHONE_OTP", "SMS", "sms"):
        assert t.may_authorize_critical_change(signal, POLICY) is False
    assert t.may_authorize_critical_change("PASSKEY", POLICY) is True


def test_a_policy_that_lets_a_non_proof_count_or_asks_for_more_at_registration_does_not_load() -> (
    None
):
    doc = _doc()
    doc["never_identity_proof"] = [x for x in doc["never_identity_proof"] if x != "SSO"]
    with pytest.raises(t.TierError, match="never identity proof"):
        t.parse(doc)
    doc = _doc()
    doc["tiers"]["R0"]["needs"] = ["EMAIL", "PHONE"]
    with pytest.raises(t.TierError, match="R0 needs the email address and nothing else"):
        t.parse(doc)
    doc = _doc()
    doc["optional_checks"]["ADDRESS"]["required_for"] = []
    with pytest.raises(t.TierError, match="never required"):
        t.parse(doc)
    doc = _doc()
    doc["optional_checks"]["ADDRESS"]["required_for"] = ["ALWAYS"]
    with pytest.raises(t.TierError, match="not a reason"):
        t.parse(doc)
    doc = _doc()
    doc["tiers"]["R2"]["needs"] = ["EMAIL"]
    with pytest.raises(t.TierError, match="must need at least"):
        t.parse(doc)


# ------------------------------------------------------------------------------------------------------------------- alternatives


def test_an_alternative_replaces_the_remote_check_without_lowering_the_tier() -> None:
    """EDR06-06: a person with no camera or phone has a named process; completing it gives the same tier, and until then they stay where they are."""
    alt = [
        c
        for c in t.required_checks(t.Tier.R1, POLICY, alternative="ASSISTED_PROOFING")
        if c.required
    ]
    assert [c.check for c in alt] == ["EMAIL", "ASSISTED_PROOFING"]
    assert alt[1].reason == "ALTERNATIVE_TO_REMOTE_CHECK"
    # until it is completed there is no proof, so the tier is what it was: no silent downgrade of what R1 means, and no silent upgrade
    assert t.tier_of(t.Facts(email_proved=True), POLICY).tier is t.Tier.R0
    # once an assisted result is on record it is a proofing result like any other
    assert t.tier_of(t.Facts(email_proved=True, proofed=True), POLICY).tier is t.Tier.R1
    for a in POLICY.alternatives:
        assert "lower" not in a["effect"].lower() and a["status"] == "PROCESS_REQUIRED"


def test_alternatives_are_shown_only_to_a_person_who_cannot_use_the_remote_route() -> None:
    assert t.alternatives_for(set(), POLICY) == ()
    assert {a["id"] for a in t.alternatives_for({"NO_CAMERA"}, POLICY)} == {
        "ASSISTED_PROOFING",
        "EQUIVALENT_CREDENTIAL",
    }
    assert {a["id"] for a in t.alternatives_for({"NO_PHONE"}, POLICY)} == {"ASSISTED_PROOFING"}


def test_a_policy_alternative_that_lowers_the_tier_does_not_load() -> None:
    doc = _doc()
    doc["alternatives"][0]["effect"] = (
        "A quick check that gives a lower-assurance tier (a downgrade)."
    )
    with pytest.raises(t.TierError, match="never a lower tier"):
        t.parse(doc)


# ------------------------------------------------------------------------------------------------------------------------ claims


def test_every_claim_carries_method_source_policy_scope_times_and_revocation() -> None:
    """EDR06-10."""
    row = {
        "assertion_id": "a-1", "claim_type": "legal_name", "verification_method": "DOCUMENT_AND_LIVENESS", "provider_id": "veriff",
        "assurance_level": "IAL-Z2", "profile_name": "uk-onboarding", "profile_version": 3, "purposes": ["onboarding"],
        "performed_at": NOW - timedelta(days=2), "valid_until": NOW + timedelta(days=300), "revoked_at": None, "status": "VALID",
    }  # fmt: skip
    c = t.claim_view(row, POLICY, NOW)
    assert set(POLICY.claim_fields) <= {
        "method",
        "source",
        "policy",
        "scope",
        "issued_at",
        "valid_until",
        "revocation",
    }
    for field in POLICY.claim_fields:
        assert c[field] not in (None, "", []), field
    assert (
        c["method"] == "DOCUMENT_AND_LIVENESS"
        and c["source"] == "veriff"
        and c["scope"] == ["onboarding"]
    )
    assert c["policy"] == {"level": "IAL-Z2", "profile": "uk-onboarding", "version": 3}
    assert c["revocation"]["state"] == "VALID" and c["revocation"]["assertion_id"] == "a-1"


def test_a_revoked_or_expired_claim_says_so() -> None:
    base = {
        "assertion_id": "a", "claim_type": "birthdate", "verification_method": "m", "provider_id": "p", "assurance_level": "IAL-Z2",
        "performed_at": NOW - timedelta(days=9), "status": "VALID",
    }  # fmt: skip
    assert (
        t.claim_view({**base, "valid_until": NOW - timedelta(days=1)}, POLICY, NOW)["revocation"][
            "state"
        ]
        == "EXPIRED"
    )
    gone = t.claim_view(
        {**base, "valid_until": NOW + timedelta(days=9), "revoked_at": NOW - timedelta(hours=1)},
        POLICY,
        NOW,
    )
    assert gone["revocation"]["state"] == "REVOKED" and gone["revocation"]["revoked_at"]


def test_the_states_of_a_check_are_distinct() -> None:
    assert len(set(POLICY.states)) == len(POLICY.states) >= 10
    assert {"PENDING", "ACTION_REQUIRED", "IN_REVIEW", "UNAVAILABLE", "EXPIRED", "REVOKED"} <= set(
        POLICY.states
    )
