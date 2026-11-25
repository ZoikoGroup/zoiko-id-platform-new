"""Authority history, export tiers, status list and what-if comparison: the pure rules (ZID-CP-WF08 v2.0 E02, E06, E09, E12; CR-170)."""

from __future__ import annotations

from datetime import UTC, datetime, timedelta
from typing import Any

import pytest
from zid_platform.modules.evidence.domain import authority_snapshot as snap
from zid_platform.modules.evidence.domain import export_tiers as tiers
from zid_platform.modules.evidence.domain import status_list as sl
from zid_platform.modules.policy_authoring.domain import what_if

AT = datetime(2026, 11, 25, 10, 0, tzinfo=UTC)
D = "sha-256:" + "ab" * 32


def edge(**over: Any) -> dict[str, Any]:
    base = {
        "edge_type": "GRANT", "edge_id": "g1", "version": 3, "subject": "agent-1", "state": "ACTIVE", "issuer": "owner-1",
        "obtained_at": AT - timedelta(hours=1), "effective_from": AT - timedelta(days=2), "effective_until": None, "evidence_digest": D,
    }  # fmt: skip
    return {**base, **over}


# ---------------------------------------------------------------------------------------------------------- authority history


def test_a_complete_chain_is_complete_and_keeps_every_edge_with_its_source() -> None:
    s = snap.assess([edge(), edge(edge_type="MANDATE", edge_id="m1", subject="agent-1"), edge(edge_type="SPONSOR", edge_id="p1", subject="agent-1")], AT)
    assert s["verdict"] == "COMPLETE" and s["reasons"] == [] and len(s["edges"]) == 3
    assert {e["edge_type"] for e in s["edges"]} == {"GRANT", "MANDATE", "SPONSOR"} and all(e["evidence_digest"] == D and e["version"] == 3 for e in s["edges"])
    assert "does not prove the authority existed in law" in s["caveat"]


def test_a_missing_required_edge_is_indeterminate_never_assumed() -> None:
    assert snap.assess([], AT)["reasons"] == ["MISSING_EDGE:GRANT"]
    only_mandate = snap.assess([edge(edge_type="MANDATE", edge_id="m1")], AT)
    assert only_mandate["verdict"] == "INDETERMINATE" and "MISSING_EDGE:GRANT" in only_mandate["reasons"]


def test_two_edges_that_disagree_make_the_snapshot_indeterminate() -> None:
    both = snap.assess([edge(), edge(edge_id="g1", version=4)], AT)  # two versions of one grant both in effect
    assert both["verdict"] == "INDETERMINATE" and "CONTRADICTORY_EDGES:GRANT:agent-1" in both["reasons"]
    states = snap.assess([edge(), edge(edge_id="g2", state="SUSPENDED")], AT)
    assert "CONTRADICTORY_EDGES:GRANT:agent-1" in states["reasons"]
    two_mandates = snap.assess([edge(), edge(edge_type="MANDATE", edge_id="m1"), edge(edge_type="MANDATE", edge_id="m2")], AT)
    assert "CONTRADICTORY_EDGES:MANDATE:agent-1" in two_mandates["reasons"]
    two_grants_ok = snap.assess([edge(), edge(edge_id="g2", subject="agent-1")], AT)  # several grants may bear on one actor
    assert two_grants_ok["verdict"] == "COMPLETE"


def test_time_and_evidence_rules_each_have_their_own_reason() -> None:
    ended = snap.assess([edge(effective_until=AT - timedelta(minutes=1))], AT)
    assert "EDGE_NOT_IN_EFFECT:GRANT:g1" in ended["reasons"] and "MISSING_EDGE:GRANT" in ended["reasons"]  # ended before the decision
    future = snap.assess([edge(effective_from=AT + timedelta(days=1))], AT)
    assert "EDGE_NOT_IN_EFFECT:GRANT:g1" in future["reasons"]
    late = snap.assess([edge(obtained_at=AT + timedelta(days=1))], AT)  # reconstructed afterwards, not recorded at the time
    assert "EDGE_OBTAINED_AFTER_DECISION:GRANT:g1" in late["reasons"] and late["verdict"] == "INDETERMINATE"
    assert snap.assess([edge(obtained_at=AT + timedelta(minutes=2))], AT)["verdict"] == "COMPLETE"  # inside the clock tolerance
    assert "EDGE_EVIDENCE_DIGEST_MISSING:GRANT:g1" in snap.assess([edge(evidence_digest=None)], AT)["reasons"]
    assert "EDGE_EVIDENCE_DIGEST_MISSING:GRANT:g1" in snap.assess([edge(evidence_digest="md5:00")], AT)["reasons"]
    assert "EDGE_VERSION_INVALID:GRANT:g1" in snap.assess([edge(version="3")], AT)["reasons"]
    assert "EDGE_NOT_ACTIVE_AT_DECISION:GRANT:g1" in snap.assess([edge(state="REVOKED")], AT)["reasons"]
    assert "EDGE_TYPE_UNKNOWN:ROOT:g1" in snap.assess([edge(edge_type="ROOT")], AT)["reasons"]
    assert "TOO_MANY_EDGES" in snap.assess([edge(edge_id=f"g{i}") for i in range(snap.MAX_EDGES + 1)], AT)["reasons"]
    with pytest.raises(ValueError):
        snap.assess([edge()], datetime(2026, 1, 1))


def test_a_later_revocation_does_not_rewrite_the_history() -> None:
    then = snap.assess([edge()], AT)
    later = snap.assess([edge(effective_until=AT + timedelta(hours=1))], AT)  # revoked an hour after the decision
    assert then["verdict"] == "COMPLETE" and later["verdict"] == "COMPLETE" and later["edges"][0]["effective_until"] is not None


# ------------------------------------------------------------------------------------------------------------- export tiers


def ask(**over: Any) -> list[str]:
    base: dict[str, Any] = {
        "tier": "CUSTOMER_EVIDENCE", "authenticated": True, "recipient": {"name": "Acme Ltd", "kind": "CUSTOMER", "contact": "legal@acme.test"},
        "purpose": "Customer dispute 4412 review", "legal_basis": "CONTRACT", "retention_days": 30, "recipient_country": "GB",
    }  # fmt: skip
    return tiers.request_problems(**{**base, **over})


def test_an_anonymous_caller_can_only_ever_receive_the_public_tier() -> None:
    assert ask(tier="PUBLIC_MINIMAL", authenticated=False, recipient=None, purpose="", legal_basis=None, recipient_country=None) == []
    for t in ("CUSTOMER_EVIDENCE", "AUDITOR_DOSSIER"):
        assert ask(tier=t, authenticated=False) == ["PRIVATE_TIER_NEEDS_SIGN_IN"]
    assert ask(tier="SECRET") == ["TIER_UNKNOWN"]


def test_a_private_tier_names_who_why_on_what_basis_and_for_how_long() -> None:
    assert ask() == []
    assert ask(recipient=None) and "RECIPIENT_NAME_REQUIRED" in ask(recipient=None)
    assert "RECIPIENT_KIND_NOT_ALLOWED_FOR_TIER" in ask(recipient={"name": "KPMG", "kind": "AUDITOR", "contact": "x@y.test"})
    assert "RECIPIENT_CONTACT_REQUIRED" in ask(recipient={"name": "Acme", "kind": "CUSTOMER", "contact": ""})
    assert "PURPOSE_REQUIRED" in ask(purpose="x") and "LEGAL_BASIS_REQUIRED" in ask(legal_basis=None) and "LEGAL_BASIS_REQUIRED" in ask(legal_basis="BECAUSE")
    assert "RETENTION_OUT_OF_RANGE" in ask(retention_days=0) and "RETENTION_OUT_OF_RANGE" in ask(retention_days=91)
    assert "RECIPIENT_COUNTRY_REQUIRED" in ask(recipient_country=None) and "RECIPIENT_COUNTRY_REQUIRED" in ask(recipient_country="Britain")
    aud = {"name": "Big Four LLP", "kind": "AUDITOR", "contact": "a@b.test"}
    assert ask(tier="AUDITOR_DOSSIER", recipient=aud, legal_basis="AUDIT_ENGAGEMENT", retention_days=365) == []
    assert "LEGAL_BASIS_NOT_ALLOWED_FOR_TIER" in ask(tier="AUDITOR_DOSSIER", recipient=aud, legal_basis="CONSENT", retention_days=30)


def test_the_dossier_and_every_border_crossing_need_a_second_person() -> None:
    assert tiers.needs_second_person("AUDITOR_DOSSIER", cross_border=False) and tiers.needs_second_person("CUSTOMER_EVIDENCE", cross_border=True)
    assert not tiers.needs_second_person("CUSTOMER_EVIDENCE", cross_border=False) and not tiers.needs_second_person("PUBLIC_MINIMAL", cross_border=True)
    assert tiers.is_cross_border("gb", "GB") is False and tiers.is_cross_border("GB", "US") and tiers.is_cross_border("GB", None) is False
    body = ["receipt_id", "schema_version", "evidence_class", "decision_type", "outcome", "action", "actor_handle", "input_commitment"]
    public, customer, auditor = (tiers.withheld_for(t, body) for t in ("PUBLIC_MINIMAL", "CUSTOMER_EVIDENCE", "AUDITOR_DOSSIER"))
    assert "action" in public and "receipt_id" not in public and auditor == [] and customer == ["actor_handle", "input_commitment"]
    assert set(public) > set(customer) > set(auditor)  # each tier discloses more than the one below
    assert tiers.withheld_for("CUSTOMER_EVIDENCE", ["outcome"]) == []  # a field the receipt lacks cannot be withheld


# -------------------------------------------------------------------------------------------------------------- status list

CLAIM_KW: dict[str, Any] = {"list_id": "L1", "purpose": "revocation", "version": 1, "issuer": "cell-1", "environment": "production", "now": AT}


def listed(bits: bytearray, **over: Any) -> dict[str, Any]:
    return sl.list_claims(**{**CLAIM_KW, "encoded": "x", "size_bits": len(bits) * 8, **over})


def lookup(c: dict[str, Any], bits: bytearray, i: int, **over: Any) -> str:
    kw: dict[str, Any] = {"now": AT + timedelta(minutes=10), "max_age": timedelta(hours=1), "expected_issuer": "cell-1", "expected_environment": "production"}
    return sl.lookup(c, bytes(bits), i, **{**kw, **over})


def test_a_list_has_a_privacy_floor_and_bits_are_msb_first() -> None:
    with pytest.raises(sl.StatusListError):
        sl.new_bits(1024)
    bits = sl.new_bits()
    assert len(bits) * 8 == sl.MIN_BITS
    sl.set_bit(bits, 0)
    sl.set_bit(bits, 9)
    assert bits[0] == 0x80 and bits[1] == 0x40 and sl.get_bit(bits, 0) and sl.get_bit(bits, 9) and not sl.get_bit(bits, 1)
    sl.set_bit(bits, 0, False)
    assert not sl.get_bit(bits, 0)
    with pytest.raises(sl.StatusListError):
        sl.get_bit(bits, sl.MIN_BITS)


def test_a_set_bit_is_revoked_an_unset_bit_is_active_only_while_the_list_is_fresh() -> None:
    bits = sl.new_bits()
    sl.set_bit(bits, 4242)
    c = listed(bits)
    assert lookup(c, bits, 4242) == "REVOKED" and lookup(c, bits, 7) == "ACTIVE"
    assert lookup(listed(bits, purpose="suspension"), bits, 4242) == "SUSPENDED"
    # stale or unexplained freshness is UNKNOWN, never ACTIVE
    assert lookup(c, bits, 7, now=AT + timedelta(hours=3), max_age=timedelta(hours=6)) == "UNKNOWN"  # past its own expiry
    assert lookup(c, bits, 7, max_age=timedelta(minutes=5)) == "UNKNOWN"  # older than the relying party accepts
    assert lookup(c, bits, 7, max_age=None) == "UNKNOWN"
    assert lookup(c, bits, 7, expected_issuer="other") == "UNKNOWN" and lookup(c, bits, 7, expected_environment="sandbox") == "UNKNOWN"
    assert lookup(c, bits, sl.MIN_BITS + 5) == "UNKNOWN" and lookup({**c, "size_bits": 8}, bits, 7) == "UNKNOWN" and lookup({}, bits, 7) == "UNKNOWN"
    assert datetime.fromisoformat(listed(bits, ttl=timedelta(days=30))["expires_at"]) - AT == sl.MAX_TTL


# ------------------------------------------------------------------------------------------------------------------ what-if


def result(candidate: bool | None, active: bool | None, n: int = 1) -> dict[str, Any]:
    side = lambda v: None if v is None else {"allowed": v, "determining": [], "codes": []}  # noqa: E731
    return {"scenario": {"principal": f"p{n}", "action": "a", "resource": "r"}, "candidate": side(candidate), "active": side(active)}


def test_a_what_if_names_who_gains_and_who_loses_and_never_guesses_when_it_cannot_compare() -> None:
    results = [result(True, True, 1), result(True, False, 2), result(False, True, 3), result(None, True, 4), result(True, None, 5)]
    s = what_if.summarize(results)
    assert s["counts"] == {"UNCHANGED": 1, "NEWLY_ALLOWED": 1, "NEWLY_DENIED": 1, "NOT_COMPARABLE": 2} and s["widens_access"] and s["narrows_access"]
    assert [c["index"] for c in s["changes"]] == [1, 2, 3, 4] and s["scenarios"] == 5
    quiet = what_if.summarize([result(True, True), result(False, False)])
    assert quiet["changes"] == [] and not quiet["widens_access"] and not quiet["narrows_access"]


def test_the_analysis_says_it_is_analysis_and_never_touches_a_receipt() -> None:
    comparison = {"tenant_id": "t", "candidate": {"release_id": "r2"}, "active": {"release_id": "r1"}}
    c = what_if.analysis_claims(comparison=comparison, summary=what_if.summarize([result(True, False)]), requested_by="u1", now_iso=AT.isoformat())
    assert c["analysis"] is True and c["affects_receipts"] is False and "never alters an existing decision receipt" in c["statement"] and "Scenarios outside this list were not examined" in c["statement"]
