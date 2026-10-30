"""Step-up authentication (ZID-ENG-DEC-009 P0 tests T04, T06, T07, T10, T11 at the rule level): the catalogue is the record's, S2 cannot be
weakened, a proof belongs to one command, time is the server's, and an agent or a federated assertion never stands in for a human passkey."""

from __future__ import annotations

from dataclasses import replace
from datetime import UTC, datetime, timedelta

import pytest
from zid_platform.modules.authentication.domain.step_up import (
    Binding,
    Method,
    StepUpAction,
    StepUpError,
    Tier,
    TierPolicy,
    build,
    canonical_json,
    check_binding,
    classify,
    command_hash,
    describe,
    fresh_enough,
    human_only,
    method_satisfies,
    webauthn_challenge,
)
from zid_platform.modules.authentication.infrastructure.step_up_config import (
    parse,
    step_up_catalogue,
)

C = step_up_catalogue()
NOW = datetime(2026, 11, 2, 12, 0, tzinfo=UTC)
CMD = {
    "tenant_id": "t1",
    "action_id": "initiate-confirm-transfer-of-organization-ownership",
    "resource": "tenant:t1",
    "change": {"operation": "ADD_OWNER", "principal_id": "p2"},
}
OWNER = "initiate-confirm-transfer-of-organization-ownership"


# ---- the catalogue is the record's ---------------------------------------------------------------------------------------


def test_the_catalogue_has_the_records_twenty_four_actions_in_three_tiers() -> None:
    tiers = [a.tier for a in C.actions.values()]
    assert (
        len(C.actions) == 24
        and tiers.count(Tier.S2) == 18
        and tiers.count(Tier.S1) == 5
        and tiers.count(Tier.S0) == 1
    )
    assert C.version == "ZID-ENG-DEC-009-v1.0"
    s1, s2 = C.tiers[Tier.S1], C.tiers[Tier.S2]
    assert s1.max_age == timedelta(minutes=10) and not s1.single_use
    assert (
        s2.max_age == timedelta(minutes=5)
        and s2.single_use
        and s2.action_bound
        and s2.phishing_resistant
    )


@pytest.mark.parametrize(
    ("action", "tier"),
    [
        ("change-account-password-or-primary-email", "S1"),
        ("enroll-remove-replace-passkey-mfa-or-recovery-factor", "S2"),
        ("revoke-own-sessions-or-all-other-sessions", "S1"),
        ("invite-standard-operator-or-viewer", "S0"),
        ("promote-user-to-admin-approver-change-privileged-role-grants", "S2"),
        (OWNER, "S2"),
        ("change-sso-federation-trust-or-scim-privilege-mapping", "S2"),
        ("issue-revoke-routine-low-risk-credentials", "S1"),
        ("create-expand-high-privilege-ai-agent-mandate", "S2"),
        ("export-raw-id-proof-biometric-evidence-ubo-records-or-bulk-pii", "S2"),
        ("export-ordinary-redacted-account-report", "S1"),
        ("change-customer-home-region-cross-region-transfer-policy", "S2"),
        ("activate-break-glass-privileged-support-access", "S2"),
    ],
)
def test_rows_read_as_the_record_prints_them(action: str, tier: str) -> None:
    assert C.actions[action].tier.value == tier


def test_recovery_and_the_two_escalating_actions_are_marked() -> None:
    assert C.actions["resolve-account-recovery-privileged-recovery-completion"].recovery
    assert C.actions["execute-sensitive-approval-rejection"].tier is Tier.S1
    assert C.actions["execute-sensitive-approval-rejection"].escalates_to is Tier.S2
    assert C.actions["export-ordinary-redacted-account-report"].escalates_to is None


def test_a_catalogue_that_weakens_s2_does_not_load() -> None:
    doc = {"version": "v", "tiers": {"S0": {}, "S1": {"max_age_seconds": 600}, "S2": {"max_age_seconds": 300, "single_use": True, "phishing_resistant": True, "action_bound": True}},
           "s2_forbidden_methods": sorted(C.s2_forbidden_methods), "challenge_ttl_seconds": 300, "risk_escalation_states": ["ELEVATED_RISK"],
           "actions": [{"id": "a-b-c", "group": "g", "action": "x", "tier": "S2", "family": "account-security"}]}  # fmt: skip
    assert parse(doc)
    for patch, match in (
        (
            {"tiers": {**doc["tiers"], "S2": {**doc["tiers"]["S2"], "max_age_seconds": 301}}},
            "five minutes",
        ),
        (
            {"tiers": {**doc["tiers"], "S2": {**doc["tiers"]["S2"], "single_use": False}}},
            "single-use",
        ),
        (
            {"tiers": {**doc["tiers"], "S2": {**doc["tiers"]["S2"], "action_bound": False}}},
            "single-use",
        ),
        ({"tiers": {**doc["tiers"], "S1": {"max_age_seconds": 601}}}, "ten minutes"),
        ({"tiers": {**doc["tiers"], "S0": {"max_age_seconds": 60}}}, "S0"),
        (
            {
                "s2_forbidden_methods": [
                    "email_link",
                    "api_token",
                    "device_cookie",
                    "security_question",
                ]
            },
            "forbids",
        ),  # SMS allowed again
        ({"challenge_ttl_seconds": 301}, "challenge"),
        ({"actions": [{**doc["actions"][0], "family": "nowhere"}]}, "family"),
        ({"actions": [{**doc["actions"][0], "tier": "S9"}]}, "unknown tier"),
        ({"actions": [{**doc["actions"][0]}, {**doc["actions"][0]}]}, "duplicate"),
        ({"actions": [{**doc["actions"][0], "escalates_to": "S1"}]}, "higher tier|above S2"),
        ({"actions": []}, "empty"),
    ):
        with pytest.raises(StepUpError, match=match):
            parse({**doc, **patch})


# ---- classification ------------------------------------------------------------------------------------------------------


def test_an_action_nobody_classified_is_refused_never_treated_as_ordinary() -> None:
    with pytest.raises(StepUpError) as e:
        classify(C, "delete-everything")
    assert e.value.code == "STEP_UP_ACTION_UNKNOWN"


def test_risk_raises_a_tier_by_one_and_a_critical_approval_goes_to_s2() -> None:
    invite = "invite-standard-operator-or-viewer"
    assert classify(C, invite).tier is Tier.S0
    assert (
        classify(C, invite, security_state="ELEVATED_RISK").tier is Tier.S1
    )  # newly risky: ordinary becomes sensitive
    assert (
        classify(
            C, "export-ordinary-redacted-account-report", security_state="REAUTH_REQUIRED"
        ).tier
        is Tier.S2
    )
    assert (
        classify(C, OWNER, security_state="STEP_UP_REQUIRED").tier is Tier.S2
    )  # nothing is above S2
    approve = "execute-sensitive-approval-rejection"
    assert classify(C, approve).tier is Tier.S1
    crit = classify(C, approve, critical=True)
    assert crit.tier is Tier.S2 and "CRITICAL_ESCALATION" in crit.reasons
    assert (
        classify(C, invite, critical=True).tier is Tier.S0
    )  # only an action that lists S2 above it escalates
    assert classify(C, OWNER, security_state="NORMAL").reasons == ("ACTION_S2",)


# ---- the command and its digest (T04) ---------------------------------------------------------------------------------------


def test_the_digest_covers_tenant_action_target_and_every_change() -> None:
    base = command_hash(**CMD)
    assert len(base) == 64 and base == command_hash(**CMD)
    reordered = {**CMD, "change": {"principal_id": "p2", "operation": "ADD_OWNER"}}
    assert command_hash(**reordered) == base  # key order is not a different command
    for field, value in (
        ("tenant_id", "t2"),
        ("action_id", "other-action"),
        ("resource", "tenant:t2"),
        ("change", {"operation": "ADD_OWNER", "principal_id": "p3"}),
        ("change", {"operation": "REMOVE_OWNER", "principal_id": "p2"}),
        ("change", {"operation": "ADD_OWNER", "principal_id": "p2", "extra": 1}),
    ):
        assert command_hash(**{**CMD, field: value}) != base, (field, value)


def test_a_payload_with_more_than_one_reading_is_refused() -> None:
    for bad in (
        {"amount": 1.5},
        {"blob": b"x"},
        {1: "a"},
        {"nested": {"deep": 0.1}},
        {"list": [1, 2.0]},
        {"x": object()},
    ):
        with pytest.raises(StepUpError) as e:
            command_hash(**{**CMD, "change": bad})  # type: ignore[arg-type]
        assert e.value.code == "STEP_UP_PAYLOAD_INVALID"
    assert (
        canonical_json({"b": [1, None, True], "a": "é"}) == '{"a":"é","b":[1,null,true]}'.encode()
    )


def test_the_passkey_challenge_is_derived_from_the_digest_so_the_signature_covers_the_command() -> (
    None
):
    d = command_hash(**CMD)
    nonce = b"n" * 32
    a = webauthn_challenge(nonce, d, "challenge-1")
    assert len(a) == 32 and a == webauthn_challenge(nonce, d, "challenge-1")
    assert a != webauthn_challenge(b"m" * 32, d, "challenge-1")  # another nonce
    assert a != webauthn_challenge(nonce, d, "challenge-2")  # another challenge
    assert a != webauthn_challenge(
        nonce, command_hash(**{**CMD, "resource": "tenant:t2"}), "challenge-1"
    )  # another command


def test_the_summary_is_written_by_the_server_and_never_shows_a_secret() -> None:
    s = describe(
        C.actions[OWNER],
        "tenant:t1",
        {
            "operation": "ADD_OWNER",
            "client_secret": "hunter2",
            "api_token": "tok",
            "private_key": "k",
        },
    )
    assert (
        s.startswith("Initiate / confirm transfer of organization ownership on tenant:t1.")
        and "operation = ADD_OWNER" in s
    )
    assert "hunter2" not in s and "tok" not in s.replace("token", "") and s.count("(hidden)") == 3
    assert (
        describe(C.actions[OWNER], "x" * 1000, {})[-1] == "."
        and len(describe(C.actions[OWNER], "x" * 1000, {"k": "v" * 1000})) <= 500
    )


# ---- who and how (T07, T10) -----------------------------------------------------------------------------------------------


def test_t10_an_agent_a_service_or_a_delegated_token_cannot_perform_a_human_step_up() -> None:
    assert human_only(principal_type="HUMAN", delegated_actor=None, workload_bound=False) is None
    for ptype in ("AI_AGENT", "SERVICE", "WORKLOAD", "APPLICATION"):
        assert (
            human_only(principal_type=ptype, delegated_actor=None, workload_bound=False)
            == "STEP_UP_HUMAN_REQUIRED"
        )
    assert (
        human_only(principal_type="HUMAN", delegated_actor="agent-1", workload_bound=False)
        == "STEP_UP_HUMAN_REQUIRED"
    )  # acting for someone
    assert (
        human_only(principal_type="HUMAN", delegated_actor=None, workload_bound=True)
        == "STEP_UP_HUMAN_REQUIRED"
    )  # a certificate-bound token


def test_s2_accepts_only_a_user_verified_phishing_resistant_passkey() -> None:
    ok = {"user_verified": True, "phishing_resistant": True}
    assert method_satisfies(C, Tier.S2, Method.PASSKEY, **ok) is None
    assert (
        method_satisfies(C, Tier.S2, Method.PASSKEY, user_verified=False, phishing_resistant=True)
        == "USER_VERIFICATION_REQUIRED"
    )
    assert (
        method_satisfies(C, Tier.S2, Method.PASSKEY, user_verified=True, phishing_resistant=False)
        == "NOT_PHISHING_RESISTANT"
    )
    for m in (
        Method.EMAIL_LINK,
        Method.SMS,
        Method.SECURITY_QUESTION,
        Method.API_TOKEN,
        Method.DEVICE_COOKIE,
        Method.ENTERPRISE_MFA,
        Method.FEDERATED_ASSERTION,
    ):
        assert method_satisfies(C, Tier.S2, m, **ok) == "S2_METHOD_FORBIDDEN", (
            m
        )  # a federated assertion is not enough: Zoiko runs its own step-up


def test_s1_fallback_mfa_is_off_until_the_owners_approve_it_and_email_or_sms_never_count() -> None:
    ok = {"user_verified": True, "phishing_resistant": True}
    assert method_satisfies(C, Tier.S1, Method.PASSKEY, **ok) is None
    assert method_satisfies(C, Tier.S1, Method.ENTERPRISE_MFA, **ok) == "S1_METHOD_NOT_APPROVED"
    allowed = replace(C, s1_fallback_mfa=True)
    assert method_satisfies(allowed, Tier.S1, Method.ENTERPRISE_MFA, **ok) is None
    for m in (
        Method.EMAIL_LINK,
        Method.SMS,
        Method.SECURITY_QUESTION,
        Method.API_TOKEN,
        Method.DEVICE_COOKIE,
    ):
        assert method_satisfies(allowed, Tier.S1, m, **ok) == "S1_METHOD_NOT_APPROVED"
    assert (
        method_satisfies(C, Tier.S0, Method.SMS, user_verified=False, phishing_resistant=False)
        is None
    )


# ---- time is the server's (T06) ---------------------------------------------------------------------------------------------


def test_t06_s1_expires_at_ten_minutes_and_s2_at_five_and_a_future_proof_is_not_fresh() -> None:
    s1, s2 = C.tiers[Tier.S1], C.tiers[Tier.S2]
    assert fresh_enough(s1, NOW - timedelta(seconds=600), NOW) and not fresh_enough(
        s1, NOW - timedelta(seconds=601), NOW
    )
    assert fresh_enough(s2, NOW - timedelta(seconds=300), NOW) and not fresh_enough(
        s2, NOW - timedelta(seconds=301), NOW
    )
    assert not fresh_enough(
        s2, NOW + timedelta(seconds=1), NOW
    )  # dated after the server's own clock
    assert fresh_enough(C.tiers[Tier.S0], NOW - timedelta(days=30), NOW)
    assert fresh_enough(TierPolicy(None, False, False, False), NOW, NOW)


# ---- a proof belongs to one command (T04, T05) ----------------------------------------------------------------------------


def binding(**over: object) -> Binding:
    base = Binding(
        "t1", "alice", "s1", OWNER, "organization-governance", "tenant:t1", command_hash(**CMD)
    )
    return replace(base, **over)  # type: ignore[arg-type]


def test_t04_an_s2_proof_for_another_tenant_actor_session_action_target_or_payload_is_refused() -> (
    None
):
    proven = binding()
    assert check_binding(C, Tier.S2, binding(), proven) is None
    for field, code in (
        ("tenant_id", "STEP_UP_TENANT_MISMATCH"),
        ("actor_id", "STEP_UP_ACTOR_MISMATCH"),
        ("session_id", "STEP_UP_SESSION_MISMATCH"),
        ("action_id", "STEP_UP_ACTION_MISMATCH"),
        ("resource", "STEP_UP_PAYLOAD_MISMATCH"),
        ("command_hash", "STEP_UP_PAYLOAD_MISMATCH"),
    ):
        other = "x" if field != "command_hash" else "0" * 64
        assert check_binding(C, Tier.S2, binding(**{field: other}), proven) == code, field


def test_an_s1_proof_covers_the_same_tenant_actor_session_and_action_family_only() -> None:
    proven = binding(
        family="account-security",
        action_id="change-account-password-or-primary-email",
        resource="principal:alice",
    )
    same_family = binding(
        family="account-security",
        action_id="revoke-own-sessions-or-all-other-sessions",
        resource="principal:alice",
        command_hash="1" * 64,
    )
    assert check_binding(C, Tier.S1, same_family, proven) is None  # reuse inside the family
    assert (
        check_binding(C, Tier.S1, replace(same_family, family="credentials"), proven)
        == "STEP_UP_ACTION_MISMATCH"
    )
    assert (
        check_binding(C, Tier.S1, replace(same_family, tenant_id="t2"), proven)
        == "STEP_UP_TENANT_MISMATCH"
    )
    assert (
        check_binding(C, Tier.S1, replace(same_family, session_id="s2"), proven)
        == "STEP_UP_SESSION_MISMATCH"
    )


def test_the_action_ids_a_route_names_exist_in_the_catalogue() -> None:
    """Every action a route guards must be classified; an unclassified one is a 500, so this catches the typo before a customer does."""
    import re
    from pathlib import Path

    root = Path(__file__).resolve().parents[2] / "src" / "zid_platform"
    named = set()
    for path in root.rglob("*.py"):
        text = path.read_text(encoding="utf-8")
        for m in re.finditer(r"require_step_up\([^)]*?action=\"([a-z0-9-]+)\"", text, re.DOTALL):
            named.add(m.group(1))
    assert len(named) >= 5, named
    assert named <= set(C.actions), sorted(named - set(C.actions))


def test_build_reports_a_duplicate_action() -> None:
    a = StepUpAction("dup-action", "g", "n", Tier.S1, None, False, "credentials", "", "", ())
    with pytest.raises(StepUpError, match="duplicate"):
        build(
            "v", C.tiers, timedelta(minutes=5), False, C.s2_forbidden_methods, frozenset(), [a, a]
        )
