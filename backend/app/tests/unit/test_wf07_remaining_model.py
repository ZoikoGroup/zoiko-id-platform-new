"""Issuer-scope stops, risk signals and agent credentials (ZID-CP-WF07 v2.0 .09, .10, A07-13, A07-14; CR-169): the pure rules."""

from __future__ import annotations

from datetime import UTC, datetime, timedelta
from typing import Any

import pytest
from zid_platform.modules.machine.domain import agent_credential as ac
from zid_platform.modules.machine.domain import revocation as rv
from zid_platform.modules.machine.domain import risk_signals as rs
from zid_platform.modules.machine.domain.agents import MachineError

NOW = datetime(2026, 11, 25, 10, 0, tzinfo=UTC)


def code(fn: Any, *a: Any, **k: Any) -> str:
    with pytest.raises(MachineError) as e:
        fn(*a, **k)
    return e.value.code


def test_a_stop_can_name_an_outside_issuer_or_one_of_its_keys() -> None:
    assert rv.check_shape("ISSUER", "https://idp.example.com/t1") == (
        "ISSUER",
        "https://idp.example.com/t1",
        None,
    )
    assert rv.check_shape("ISSUER", "https://idp.example.com/t1#key-9") == (
        "ISSUER",
        "https://idp.example.com/t1",
        "key-9",
    )
    for bad in ("http://idp.example.com", "idp.example.com", "https://idp.example.com#", "", None):
        assert code(rv.check_shape, "ISSUER", bad) == "REVOCATION_INVALID", bad
    assert "ISSUER" in rv.SCOPES


def test_risk_signals_are_places_to_look_never_a_score_a_verdict_or_a_decision() -> None:
    old, fresh = NOW - timedelta(hours=3), NOW - timedelta(minutes=5)
    actions = [
        {
            "action_id": "a1",
            "state": "UNKNOWN",
            "updated_at": old,
            "agent_principal_id": "ag",
            "tool_id": "t",
        },
        {
            "action_id": "a2",
            "state": "DISPATCHED",
            "updated_at": fresh,
            "agent_principal_id": "ag",
            "tool_id": "t",
        },
        {
            "action_id": "a3",
            "state": "EXECUTED",
            "updated_at": old,
            "agent_principal_id": "ag",
            "tool_id": "t",
        },
    ]
    got = rs.unresolved(actions, NOW)
    assert [s["subject"] for s in got] == ["action:a1"]  # not a recent one, not a finished one
    b = [
        {
            "binding_id": "b1",
            "status": "ACTIVE",
            "last_failure": "IDP_ISSUER_MISMATCH",
            "last_ok_at": None,
            "agent_principal_id": "ag",
            "issuer": "i",
        },
        {
            "binding_id": "b2",
            "status": "ACTIVE",
            "last_failure": None,
            "last_ok_at": NOW - timedelta(days=3),
            "agent_principal_id": "ag",
            "issuer": "i",
        },
        {
            "binding_id": "b3",
            "status": "ACTIVE",
            "last_failure": None,
            "last_ok_at": fresh,
            "agent_principal_id": "ag",
            "issuer": "i",
        },
        {
            "binding_id": "b4",
            "status": "REVOKED",
            "last_failure": "X",
            "last_ok_at": None,
            "agent_principal_id": "ag",
            "issuer": "i",
        },
    ]
    kinds = {s["subject"]: s["kind"] for s in rs.providers(b, NOW, timedelta(hours=24))}
    assert kinds == {"binding:b1": "PROVIDER_CHECK_FAILED", "binding:b2": "PROVIDER_NOT_HEARD_FROM"}
    stops = rs.open_stops(
        [
            {
                "event_id": "e1",
                "epoch": 1,
                "scope": "TENANT",
                "targets": {"agent_ids": ["x", "y"]},
                "closed_at": None,
                "effective_at": old,
            },
            {
                "event_id": "e2",
                "epoch": 2,
                "scope": "AGENT",
                "targets": {},
                "closed_at": NOW,
                "effective_at": old,
            },
        ]
    )
    assert [s["subject"] for s in stops] == ["stop:e1"] and stops[0]["fact"]["agents"] == 2
    allsig = [*got, *rs.providers(b, NOW, timedelta(hours=24)), *stops, *rs.orphaned(["ag9"])]
    rep = rs.report(allsig, NOW)
    assert (
        rep["score"] is None
        and rep["counts"]["total"] == 5
        and rep["counts"]["attention"] == 3
        and rep["signals"][0]["severity"] == "attention"
    )
    assert (
        all(s["is_a_finding"] is False for s in rep["signals"])
        and "not proof" in rep["note"]
        and any("Denied tool calls" in c for c in rep["cannot_see"])
    )
    quiet = rs.report([], NOW)
    assert quiet["counts"]["total"] == 0 and "No signals is not proof" in quiet["note"]


def claims_for(**over: Any) -> dict[str, Any]:
    base = {
        "issuer": "https://id.zoiko.test", "credential_id": "cred-1", "agent_id": "agent-7", "organization_id": "org-1", "mandate_id": "m-1", "mandate_hash": "sha256:" + "a" * 64, "evidence_class": "E2", "jkt": "jkt-1",
        "audience": None, "status_url": "https://id.zoiko.test/v1/machine/agent-credentials:verify", "issued_at": NOW, "expires_at": NOW + timedelta(days=7),
    }  # fmt: skip
    return ac.credential_claims(**{**base, **over})


def test_a_credential_carries_only_identifiers_and_says_authority_was_not_evaluated() -> None:
    c = claims_for()
    assert (
        c["sub"] == "urn:zoiko:agent:agent-7"
        and c["cnf"] == {"jkt": "jkt-1"}
        and c["vc"]["authority_evaluated"] is False
    )
    assert c["vc"]["type"] == ["VerifiableCredential", "ZoikoAgentCredential"] and c["vc"][
        "@context"
    ] == ["https://www.w3.org/ns/credentials/v2"]
    assert c["vc"]["credentialSubject"]["mandate"] == {"id": "m-1", "hash": "sha256:" + "a" * 64}
    assert (
        "aud" not in c and claims_for(audience="https://rp.example")["aud"] == "https://rp.example"
    )
    text = str(c).lower()
    assert not any(
        w in text
        for w in ("name", "email", "passport", "selfie", "kyb", "permission", "scope", "spend")
    )  # nothing personal and nothing that reads as a permission
    assert (
        "mandate" not in claims_for(mandate_id=None, mandate_hash=None)["vc"]["credentialSubject"]
    )


def test_a_key_must_be_a_public_key_and_a_lifetime_is_short() -> None:
    pub = {"kid": "k1", "kty": "EC", "crv": "P-256", "x": "x", "y": "y"}
    assert ac.check_public_key(pub) == pub
    for bad in (
        None,
        "k",
        {"kty": "EC"},
        {**pub, "d": "s"},
        {**pub, "jku": "https://x"},
        {**pub, "kid": ""},
    ):
        assert code(ac.check_public_key, bad) == "AGENT_CREDENTIAL_INVALID", bad
    assert ac.check_ttl(None) == ac.DEFAULT_LIFETIME and ac.check_ttl(24) == timedelta(hours=24)
    assert (
        code(ac.check_ttl, 721) == "AGENT_CREDENTIAL_INVALID"
        and ac.check_evidence_class(None) == "E2"
        and code(ac.check_evidence_class, "E9") == "AGENT_CREDENTIAL_INVALID"
    )


def test_a_good_signature_is_not_enough_the_record_the_agent_and_the_key_must_all_hold() -> None:
    c = claims_for()
    rec = {"status": "ACTIVE", "agent_principal_id": "agent-7", "jkt": "jkt-1"}
    assert ac.credential_problems(c, record=rec, now=NOW + timedelta(hours=1)) == []
    assert ac.credential_problems(c, record=rec, now=NOW + timedelta(days=8)) == [
        "CREDENTIAL_EXPIRED"
    ]
    assert ac.credential_problems(c, record=None, now=NOW) == [
        "CREDENTIAL_UNKNOWN"
    ]  # signed by this issuer, unknown to it
    assert ac.credential_problems(c, record={**rec, "status": "REVOKED"}, now=NOW) == [
        "CREDENTIAL_REVOKED"
    ]
    assert ac.credential_problems(c, record={**rec, "agent_principal_id": "other"}, now=NOW) == [
        "CREDENTIAL_SUBJECT_MISMATCH"
    ]
    assert ac.credential_problems(c, record={**rec, "jkt": "other"}, now=NOW) == [
        "CREDENTIAL_KEY_MISMATCH"
    ]
    assert ac.agent_problems(status="ACTIVE", frozen=False, sponsor_active=True) == []
    assert ac.agent_problems(status="SUSPENDED", frozen=True, sponsor_active=False) == [
        "AGENT_SUSPENDED",
        "AGENT_FROZEN",
        "SPONSOR_NOT_ACTIVE",
    ]


def test_a_presentation_proves_possession_only_for_this_relying_party_this_nonce_and_this_moment() -> (
    None
):
    ts = int(NOW.timestamp())
    p = {"aud": "rp-1", "nonce": "n" * 12, "cred": "cred-1", "iat": ts - 5, "jti": "j"}

    def probs(**over: Any) -> list[str]:
        return ac.presentation_problems(
            {**p, **over}, credential_id="cred-1", relying_party="rp-1", nonce="n" * 12, now=NOW
        )

    assert probs() == []
    assert probs(aud="rp-2") == ["PRESENTATION_AUDIENCE_MISMATCH"]  # relayed to another party
    assert probs(nonce="x" * 12) == ["PRESENTATION_NONCE_MISMATCH"] and probs(cred="cred-2") == [
        "PRESENTATION_FOR_ANOTHER_CREDENTIAL"
    ]
    assert (
        probs(iat=ts - 3600) == ["PRESENTATION_TOO_OLD"]
        and probs(iat=ts + 3600) == ["PRESENTATION_FROM_THE_FUTURE"]
        and probs(jti=None) == ["PRESENTATION_NO_ID"]
    )


def test_the_verifier_never_says_an_agent_may_spend() -> None:
    good = ac.indicators(credential_ok=True, agent_ok=True, possession=True)
    assert (
        good["authority_evaluated"] is False
        and good["may_spend"] is None
        and ac.verdict(good) == "IDENTITY_VERIFIED_AUTHORITY_NOT_EVALUATED"
    )
    offline = ac.indicators(credential_ok=True, agent_ok=True, possession=None)
    assert (
        ac.verdict(offline) == "VALID_BUT_POSSESSION_NOT_SHOWN"
        and offline["key_possession"] is None
    )  # a valid credential nobody proved they hold
    assert (
        ac.verdict(ac.indicators(credential_ok=True, agent_ok=True, possession=False))
        == "NOT_VALID"
    )
    assert (
        ac.verdict(ac.indicators(credential_ok=False, agent_ok=True, possession=True))
        == "NOT_VALID"
    )
    assert (
        ac.verdict(ac.indicators(credential_ok=True, agent_ok=False, possession=True))
        == "NOT_VALID"
    )  # a stopped agent's credential is not good
    assert ac.reasons_unique(["A", "B"], ["B", "C"], []) == ["A", "B", "C"]
