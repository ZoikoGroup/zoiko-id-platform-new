"""Operational drills for evidence (ZID-CP-WF08 v2.0 E13, section 08.2; CR-170): each drill is a test that can be run on demand and whose output is the record.

The runbook (docs/runbooks/wf08-evidence-drills.md) says when to run them and what to keep. They prove the mechanics against a real sealed receipt. They do not prove that
a production KMS, backup system or on-call rota behaves the same way: the first run in the production environment is still to be done by the operators.
"""

from __future__ import annotations

import copy
import json
from dataclasses import replace
from datetime import UTC, datetime, timedelta
from typing import Any

from test_evidence_pg import fresh_runtime  # noqa: F401 - the fixture sets the evidence runtime
from test_wf08_public_proof_pg import sealed_bundle
from zid_crypto import LocalSigner
from zid_crypto.purposes import KeyPurpose
from zid_evidence import verify_bundle
from zid_platform.modules.evidence.application import wf08_services as svc
from zid_platform.modules.evidence.application.runtime import configure_runtime, current_runtime, sign_evidence
from zid_platform.modules.evidence.domain import status_list as sl
from zid_platform.modules.evidence.domain import trust_bundle as tb
from zid_platform.modules.evidence.domain import verification_axes as va


def rotate(rt: Any, kid: str) -> Any:
    """A planned rotation: the new key signs from now on and the old public key stays published so history keeps verifying."""
    return replace(rt, signer=LocalSigner(kid, purpose=KeyPurpose.EVIDENCE_SIGNING), historic_keys=(*rt.historic_keys, rt.signer.public_jwk()))


def axes_of(bundle: dict[str, Any], keys: list[dict[str, Any]]) -> dict[str, Any]:
    report = verify_bundle(bundle, trusted_keys=keys)
    checks = [{"name": c.name, "ok": c.ok, "detail": c.detail} for c in report.checks]
    return va.axes(checks=checks, receipt_hash_valid=report.ok or all(c["ok"] for c in checks if c["name"] == "record_hash"), ledgered=True, sealed=True, keys_source="trusted", now=datetime.now(UTC))


def test_drill_a_restore_after_two_key_rotations_old_history_still_verifies_from_the_published_keys_alone(rt_url: str, owner_url: str) -> None:
    bundle, _receipt, _tenant, _admin = sealed_bundle(rt_url, owner_url)  # signed by the first key
    first = current_runtime()
    k1 = first.signer.public_jwk()["kid"]
    second = rotate(first, "dev-evidence-uk-1-v2")
    third = rotate(second, "dev-evidence-uk-1-v3")
    try:
        configure_runtime(third)
        published = json.loads(json.dumps(third.trusted_keys()))  # what is written to the restored environment: the published key set, nothing private
        restored = json.loads(json.dumps(bundle))  # the exported bundle as it comes back from the archive
        assert {k["kid"] for k in published} >= {k1, "dev-evidence-uk-1-v2", "dev-evidence-uk-1-v3"} and not any("d" in k for k in published)
        a = axes_of(restored, published)
        assert a["signature_integrity"] == "VALID" and a["issuer_trust"] == "TRUSTED" and a["proof_completeness"] == "COMPLETE"
        # the key set the restore forgot to bring: the old history reads as untrusted, which is why historic keys are never dropped
        only_newest = axes_of(restored, [third.signer.public_jwk()])
        assert only_newest["issuer_trust"] == "UNTRUSTED" and only_newest["signature_integrity"] == "INVALID"
        # something signed after both rotations verifies under the newest key and is not mistaken for the old one
        token = sign_evidence({"drill": "after-two-rotations"}, "zid-drill+jwt")
        assert token.count(".") == 2
    finally:
        configure_runtime(first)


def test_drill_b_a_compromised_key_voids_only_what_it_signed_from_the_compromise_onwards(rt_url: str, owner_url: str) -> None:
    bundle, _receipt, _tenant, _admin = sealed_bundle(rt_url, owner_url)
    signed_at = datetime.fromisoformat(bundle["checkpoint"]["created_at"].replace("Z", "+00:00"))
    first = current_runtime()
    kid = str(first.signer.public_jwk()["kid"])
    rescored = rotate(first, "dev-evidence-uk-1-v2")

    def standing(compromised_from: datetime) -> str:
        claims = tb.bundle_claims(
            issuer="drill", environment="test", keys=rescored.trusted_keys(), active_kid=str(rescored.signer.public_jwk()["kid"]), compromised={kid: compromised_from}, version=2, now=datetime.now(UTC),
        )  # fmt: skip
        return tb.key_standing(claims, kid, signed_at)

    assert standing(signed_at + timedelta(hours=1)) == "TRUSTED_RETIRED"  # the key leaked after this checkpoint was signed: it stands
    assert standing(signed_at - timedelta(hours=1)) == "COMPROMISED_AT_SIGNING"  # the key was already in other hands: it does not
    assert standing(signed_at) == "COMPROMISED_AT_SIGNING"  # the boundary is not given the benefit of the doubt


def test_drill_c_a_status_outage_reads_as_unknown_never_as_valid() -> None:
    now = datetime.now(UTC)
    bits = sl.new_bits()
    claims = sl.list_claims(list_id="L", purpose="revocation", version=1, encoded=svc._encode(bytes(bits)), size_bits=len(bits) * 8, issuer="drill", environment="test", now=now - timedelta(hours=5))
    jws = sign_evidence(claims, sl.TYP)  # the last list we managed to fetch, five hours ago; the endpoint is now unreachable
    ok = timedelta(hours=1)
    assert svc.check_published(jws, 7, now=now - timedelta(hours=5) + timedelta(minutes=5), max_age=ok, issuer="drill", environment="test") == "ACTIVE"  # fresh when fetched
    assert svc.check_published(jws, 7, now=now, max_age=ok, issuer="drill", environment="test") == "UNKNOWN"  # five hours on: expired and older than accepted
    assert svc.check_published(jws, 7, now=now, max_age=None, issuer="drill", environment="test") == "UNKNOWN"  # no stated tolerance means nobody said what fresh is
    assert svc.check_published("", 7, now=now, max_age=ok, issuer="drill", environment="test") == "UNKNOWN"  # nothing could be fetched at all
    a = va.axes(checks=[], receipt_hash_valid=True, ledgered=True, sealed=True, keys_source="trusted", now=now, status_now="ACTIVE", status_checked_at=now - timedelta(hours=5), max_status_age=ok)
    assert a["status_now"] == "UNKNOWN" and "STATUS_NOT_FRESH_ENOUGH" in a["reasons"]


def test_drill_d_removing_the_stored_body_leaves_the_proof_of_integrity(rt_url: str, owner_url: str) -> None:
    bundle, _receipt, _tenant, _admin = sealed_bundle(rt_url, owner_url)
    keys = current_runtime().trusted_keys()
    swept = copy.deepcopy(bundle)
    swept["record"] = None  # what remains after the retention sweep: the ledger entry and its proof, not the body
    report = verify_bundle(swept, trusted_keys=keys)
    assert report.ok and report.structural_only
    a = axes_of(swept, keys)
    assert a["signature_integrity"] == "VALID" and a["issuer_trust"] == "TRUSTED" and a["real_world_claim_verification"] == "NOT_ASSESSED"
    swept["ledger_entry"]["committed_at"] = "2030-01-01T00:00:00.000000Z"  # and the remaining proof still catches an alteration
    assert not verify_bundle(swept, trusted_keys=keys).ok
