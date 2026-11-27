"""The multi-axis verification result and the signed trust bundle (ZID-CP-WF08 v2.0 .04, .05, .06, .08, section 08.1; CR-170): pure rules."""

from __future__ import annotations

from datetime import UTC, datetime, timedelta
from typing import Any

from zid_platform.modules.evidence.domain import trust_bundle as tb
from zid_platform.modules.evidence.domain import verification_axes as va

NOW = datetime(2026, 11, 25, 10, 0, tzinfo=UTC)
ALL_OK = [{"name": n, "ok": True, "detail": ""} for n in va.INTEGRITY_CHECKS]


def ax(**over: Any) -> dict[str, Any]:
    base: dict[str, Any] = {
        "checks": ALL_OK,
        "receipt_hash_valid": True,
        "ledgered": True,
        "sealed": True,
        "keys_source": "trusted",
        "now": NOW,
    }
    return va.axes(**{**base, **over})


def test_there_is_no_overall_success_and_a_real_world_claim_is_never_assessed() -> None:
    a = ax()
    assert (
        a["success"] is None
        and a["real_world_claim_verification"] == "NOT_ASSESSED"
        and "does not prove the decision was correct" in a["caveat"]
    )
    assert (
        a["signature_integrity"] == "VALID"
        and a["issuer_trust"] == "TRUSTED"
        and a["proof_completeness"] == "COMPLETE"
    )
    # a perfect integrity result still says nothing about time, status or the world
    assert (
        a["time_proof"] == "NONE"
        and a["status_now"] == "NOT_CHECKED"
        and a["status_at_decision"] == "NOT_ASSESSED"
        and a["execution_status"] == "NOT_APPLICABLE"
    )
    assert "NO_EXTERNAL_TIMESTAMP" in a["reasons"]
    words = va.summary_words(a)
    assert words[-1] == "Correctness of the decision: not assessed" and not any(
        "verified" in w.lower() and "not" not in w.lower() for w in words
    )


def test_integrity_trust_and_completeness_are_judged_separately() -> None:
    # intact but signed by a key the bundle brought along: not trusted
    self_asserted = ax(keys_source="bundle")
    assert (
        self_asserted["signature_integrity"] == "VALID"
        and self_asserted["issuer_trust"] == "UNTRUSTED"
        and "KEYS_FROM_THE_BUNDLE" in self_asserted["reasons"]
    )
    # a bad checkpoint signature is both an integrity failure and an untrusted signer
    bad = [{**c, "ok": False} if c["name"] == "checkpoint_signature" else c for c in ALL_OK]
    broken = ax(checks=bad)
    assert (
        broken["signature_integrity"] == "INVALID"
        and broken["issuer_trust"] == "UNTRUSTED"
        and "CHECK_FAILED:checkpoint_signature" in broken["reasons"]
    )
    # a different receipt than the one committed
    differs = ax(
        receipt_hash_valid=False,
        checks=[{**c, "ok": False} if c["name"] == "record_hash" else c for c in ALL_OK],
    )
    assert differs["signature_integrity"] == "INVALID" and {
        "RECEIPT_HASH_DIFFERS",
        "CHECK_FAILED:record_hash",
    } <= set(differs["reasons"])  # both reasons, not just the first
    assert ax(receipt_hash_valid=False)["signature_integrity"] == "INVALID"
    # committed but not yet in the ledger or not yet sealed: partial or missing, and integrity is not claimed
    sealing = ax(checks=[], sealed=False)
    assert (
        sealing["proof_completeness"] == "PARTIAL"
        and sealing["signature_integrity"] == "INDETERMINATE"
        and sealing["issuer_trust"] == "UNKNOWN"
        and "NOT_YET_SEALED" in sealing["reasons"]
    )
    waiting = ax(checks=[], ledgered=False, sealed=False)
    assert waiting["proof_completeness"] == "MISSING" and "NOT_YET_LEDGERED" in waiting["reasons"]
    assert (
        ax(checks=[c for c in ALL_OK if c["name"] != "inclusion_proof"])["signature_integrity"]
        == "INDETERMINATE"
    )  # nothing wrong found, but a needed proof is absent


def test_a_timestamp_nobody_checked_is_not_a_proof_of_time() -> None:
    present = [
        *ALL_OK,
        {
            "name": "external_timestamp",
            "ok": True,
            "detail": "present, not verified (no TSA trust list)",
        },
    ]
    checked = [
        *ALL_OK,
        {"name": "external_timestamp", "ok": True, "detail": "verified against the TSA trust list"},
    ]
    rejected = [*ALL_OK, {"name": "external_timestamp", "ok": False, "detail": "untrusted"}]
    assert (
        ax(checks=present)["time_proof"] == "UNVERIFIED_TIMESTAMP"
        and "TIMESTAMP_NOT_CHECKED_AGAINST_A_TRUST_LIST" in ax(checks=present)["reasons"]
    )
    assert ax(checks=checked)["time_proof"] == "VERIFIED_TIMESTAMP"
    assert (
        ax(checks=rejected)["time_proof"] == "INVALID"
        and "TIMESTAMP_REJECTED" in ax(checks=rejected)["reasons"]
    )


def test_a_status_is_current_only_within_the_age_the_relying_party_accepts_and_stale_is_unknown_not_valid() -> (
    None
):
    hour = timedelta(hours=1)
    assert va.status_freshness(NOW - timedelta(minutes=5), NOW, hour) == "CURRENT"
    assert va.status_freshness(NOW - timedelta(hours=3), NOW, hour) == "STALE"
    assert (
        va.status_freshness(None, NOW, hour) == "UNKNOWN"
        and va.status_freshness(NOW, NOW, None) == "UNKNOWN"
    )  # no age policy means nobody said what fresh is
    fresh = ax(
        status_now="ACTIVE",
        status_checked_at=NOW - timedelta(minutes=5),
        max_status_age=hour,
        status_at_decision="ACTIVE",
    )
    assert (
        fresh["status_now"] == "ACTIVE"
        and fresh["status_freshness"] == "CURRENT"
        and fresh["status_at_decision"] == "ACTIVE"
    )
    stale = ax(status_now="ACTIVE", status_checked_at=NOW - timedelta(hours=3), max_status_age=hour)
    assert (
        stale["status_now"] == "UNKNOWN" and "STATUS_NOT_FRESH_ENOUGH" in stale["reasons"]
    )  # an old snapshot never reads as "valid now"
    no_policy = ax(status_now="ACTIVE", status_checked_at=NOW)
    assert (
        no_policy["status_now"] == "UNKNOWN"
        and "STATUS_AGE_POLICY_NOT_GIVEN" in no_policy["reasons"]
    )
    revoked_since = ax(
        status_at_decision="ACTIVE",
        status_now="REVOKED",
        status_checked_at=NOW,
        max_status_age=hour,
    )
    assert (
        revoked_since["status_at_decision"] == "ACTIVE" and revoked_since["status_now"] == "REVOKED"
    )  # a later revocation never rewrites the historical state


# ------------------------------------------------------------------------------- the trust bundle


def jwk(kid: str) -> dict[str, Any]:
    return {"kid": kid, "kty": "OKP", "crv": "Ed25519", "x": "x", "alg": "EdDSA"}


def claims(**over: Any) -> dict[str, Any]:
    base = tb.bundle_claims(
        issuer="zoiko-cell-uk-1", environment="production", keys=[jwk("k1"), jwk("k2"), jwk("k3")], active_kid="k3", compromised={"k1": NOW - timedelta(days=2)}, version=31, now=NOW,
    )  # fmt: skip
    return {**base, **over}


def test_a_trust_bundle_lists_each_key_with_its_standing_and_never_offers_a_pq_claim() -> None:
    c = claims()
    states = {k["kid"]: k["state"] for k in c["keys"]}
    assert (
        states == {"k1": "COMPROMISED", "k2": "RETIRED", "k3": "ACTIVE"}
        and next(k for k in c["keys"] if k["kid"] == "k1")["compromised_from"]
    )
    assert (
        c["pq_hybrid"] == "NOT_OFFERED"
        and "not a qualified or notarized" in c["limits"]
        and c["receipt_schemas"] == ["zoiko.receipt.v1"]
    )
    assert (
        datetime.fromisoformat(c["expires_at"]) - datetime.fromisoformat(c["issued_at"]) == tb.TTL
    )
    assert (
        datetime.fromisoformat(
            tb.bundle_claims(
                issuer="i",
                environment="e",
                keys=[jwk("a")],
                active_kid="a",
                compromised={},
                version=1,
                now=NOW,
                ttl=timedelta(days=90),
            )["expires_at"]
        )
        - NOW
        == tb.MAX_TTL
    )  # a long life is clamped
    assert (
        tb.version_from([jwk("a")], {})
        < tb.version_from([jwk("a"), jwk("b")], {})
        < tb.version_from([jwk("a"), jwk("b")], {"a": NOW})
    )  # rotation and compromise both raise it


def test_a_verifier_refuses_a_stale_substituted_rolled_back_or_other_environment_bundle() -> None:
    def problems(c: dict[str, Any], **kw: Any) -> list[str]:
        base = {
            "expected_issuer": "zoiko-cell-uk-1",
            "expected_environment": "production",
            "now": NOW + timedelta(hours=1),
        }
        return tb.bundle_problems(c, **{**base, **kw})

    assert problems(claims()) == []
    assert problems(claims(), now=NOW + timedelta(days=2)) == [
        "TRUST_BUNDLE_EXPIRED"
    ]  # a stale bundle could keep a stolen key trusted
    assert problems(claims(issuer="someone-else")) == ["TRUST_BUNDLE_WRONG_ISSUER"]  # substituted
    assert problems(claims(environment="sandbox")) == [
        "TRUST_BUNDLE_WRONG_ENVIRONMENT"
    ]  # sandbox never trusts production
    assert (
        problems(claims(), last_seen_version=40) == ["TRUST_BUNDLE_ROLLED_BACK"]
        and problems(claims(), last_seen_version=31) == []
    )
    assert problems(claims(schema="zoiko.trust-bundle.v9")) == [
        "TRUST_BUNDLE_SCHEMA_UNSUPPORTED"
    ]  # an old verifier refuses a mandatory change
    assert (
        problems(claims(), required_kid="k9") == ["TRUST_BUNDLE_LACKS_REQUIRED_KEY"]
        and problems(claims(), required_kid="k2") == []
    )
    assert "TRUST_BUNDLE_LIFETIME_TOO_LONG" in problems(
        claims(expires_at=(NOW + timedelta(days=30)).isoformat())
    )
    assert "TRUST_BUNDLE_TIMES_INVALID" in problems(claims(expires_at="soon"))
    assert "TRUST_BUNDLE_VERSION_INVALID" in problems(
        claims(version="31")
    ) and "TRUST_BUNDLE_VERSION_INVALID" in problems(claims(version=True))


def test_a_key_compromise_only_voids_signatures_made_from_the_compromise_onwards() -> None:
    c = claims()
    before, after = NOW - timedelta(days=5), NOW - timedelta(days=1)
    assert (
        tb.key_standing(c, "k1", before) == "TRUSTED_RETIRED"
    )  # history that predates the compromise keeps verifying
    assert tb.key_standing(c, "k1", after) == "COMPROMISED_AT_SIGNING"
    assert (
        tb.key_standing(c, "k2", after) == "TRUSTED_RETIRED"
        and tb.key_standing(c, "k3", after) == "TRUSTED"
        and tb.key_standing(c, "k9", after) == "UNKNOWN_KEY"
    )
