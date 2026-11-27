"""A verification result that does not collapse into one checkmark (ZID-CP-WF08 v2.0 section 08.1, 04.2; CR-170). Pure domain, no I/O.

A receipt can be intact and signed by a key nobody trusts; trusted and not yet sealed; sealed and carrying a timestamp nobody checked; and all of those while saying nothing about whether the decision was right or what became of an
action. So the answer is a set of separate axes, and there is NO top-level `success`:

    signature_integrity              VALID / INVALID / INDETERMINATE   the receipt, its ledger entry, the checkpoint and the inclusion proof hold together
    issuer_trust                     TRUSTED / UNTRUSTED / UNKNOWN     the signing key is one the verifier pinned, not one the bundle brought along
    proof_completeness               COMPLETE / PARTIAL / MISSING      committed, in the ledger, sealed by a signed checkpoint
    time_proof                       VERIFIED_TIMESTAMP / UNVERIFIED_TIMESTAMP / NONE / INVALID
    status_at_decision / status_now  what was true then, and what is true now, kept apart; `status_now` says how fresh it is
    real_world_claim_verification    NOT_ASSESSED, always: a signature is not a finding that a match, an authority or a payment was right
    execution_status                 what became of the action, from its own record, never inferred from an allow

Nothing here upgrades one axis from another.
"""

from __future__ import annotations

from collections.abc import Mapping, Sequence
from datetime import datetime, timedelta
from typing import Any

INTEGRITY_CHECKS = (
    "format",
    "profile",
    "record_identity",
    "record_hash",
    "entry_hash",
    "checkpoint_covers_entry",
    "checkpoint_signature",
    "inclusion_proof",
)
CAVEAT = "A valid signature proves the record is intact and who signed it. It does not prove the decision was correct, that an authority existed in law, or that anything happened in the world."


def status_freshness(checked_at: datetime | None, now: datetime, max_age: timedelta | None) -> str:
    """CURRENT only when a check was made and is within the age the relying party said it can accept. No age policy means the caller did not say what fresh is, which is UNKNOWN, never CURRENT."""
    if checked_at is None or max_age is None:
        return "UNKNOWN"
    return "CURRENT" if now - checked_at <= max_age else "STALE"


def _by_name(checks: Sequence[Mapping[str, Any]]) -> dict[str, Mapping[str, Any]]:
    return {str(c["name"]): c for c in checks}


def axes(
    *, checks: Sequence[Mapping[str, Any]], receipt_hash_valid: bool, ledgered: bool, sealed: bool, keys_source: str, now: datetime, status_at_decision: str | None = None,
    status_now: str | None = None, status_checked_at: datetime | None = None, max_status_age: timedelta | None = None, execution_status: str | None = None,
) -> dict[str, Any]:  # fmt: skip
    """Every axis, each from its own evidence, and the reasons behind them. Never raises: a missing piece of evidence is an axis value, not an error."""
    by = _by_name(checks)
    reasons: list[str] = []

    # --- proof completeness
    if sealed:
        completeness = "COMPLETE"
    elif ledgered:
        completeness, _ = "PARTIAL", reasons.append("NOT_YET_SEALED")  # type: ignore[func-returns-value]
    else:
        completeness, _ = "MISSING", reasons.append("NOT_YET_LEDGERED")  # type: ignore[func-returns-value]

    # --- signature integrity
    failed = [n for n in INTEGRITY_CHECKS if n in by and not by[n]["ok"]]
    if not receipt_hash_valid:
        reasons.append("RECEIPT_HASH_DIFFERS")
    reasons += [f"CHECK_FAILED:{n}" for n in failed]  # every failure is named, not only the first
    if not receipt_hash_valid or failed:
        integrity = "INVALID"
    elif sealed and "checkpoint_signature" in by and "inclusion_proof" in by:
        integrity = "VALID"
    else:
        integrity = (
            "INDETERMINATE"  # nothing wrong was found, but not everything needed was there to check
        )

    # --- issuer trust: the pinned keys, never the keys the bundle brought
    if "checkpoint_signature" not in by:
        trust = "UNKNOWN"
    elif keys_source != "trusted":
        trust, _ = (
            "UNTRUSTED",
            reasons.append("KEYS_FROM_THE_BUNDLE"),  # type: ignore[func-returns-value]
        )  # the bundle vouching for itself
    elif by["checkpoint_signature"]["ok"]:
        trust = "TRUSTED"
    else:
        trust, _ = "UNTRUSTED", reasons.append("SIGNING_KEY_NOT_PINNED_OR_SIGNATURE_BAD")  # type: ignore[func-returns-value]

    # --- time
    ts = by.get("external_timestamp")
    if ts is None:
        time_proof = "NONE"
        reasons.append("NO_EXTERNAL_TIMESTAMP")
    elif not ts["ok"]:
        time_proof = "INVALID"
        reasons.append("TIMESTAMP_REJECTED")
    elif "not verified" in str(ts.get("detail", "")):
        time_proof, _ = (
            "UNVERIFIED_TIMESTAMP",
            reasons.append("TIMESTAMP_NOT_CHECKED_AGAINST_A_TRUST_LIST"),  # type: ignore[func-returns-value]
        )
    else:
        time_proof = "VERIFIED_TIMESTAMP"

    freshness = status_freshness(status_checked_at, now, max_status_age)
    if status_now is not None and freshness != "CURRENT":
        reasons.append(
            "STATUS_NOT_FRESH_ENOUGH" if freshness == "STALE" else "STATUS_AGE_POLICY_NOT_GIVEN"
        )
    return {
        "signature_integrity": integrity,
        "issuer_trust": trust,
        "proof_completeness": completeness,
        "time_proof": time_proof,
        "status_at_decision": status_at_decision or "NOT_ASSESSED",
        "status_now": (status_now if freshness == "CURRENT" else "UNKNOWN") if status_now is not None else "NOT_CHECKED",
        "status_freshness": freshness,
        "freshness_checked_at": status_checked_at.isoformat() if status_checked_at else None,
        "real_world_claim_verification": "NOT_ASSESSED",
        "execution_status": execution_status or "NOT_APPLICABLE",
        "reasons": sorted(set(reasons)),
        "caveat": CAVEAT,
        "success": None,  # deliberately not computed: no profile in this build says which axes together count as success
    }  # fmt: skip


def summary_words(a: Mapping[str, Any]) -> list[str]:
    """One plain line per axis, for a screen. Reads as separate statements, never as an overall pass."""
    return [
        f"Integrity: {str(a['signature_integrity']).lower()}",
        f"Signer: {str(a['issuer_trust']).lower()}",
        f"Proof: {str(a['proof_completeness']).lower()}",
        f"Time: {str(a['time_proof']).lower().replace('_', ' ')}",
        f"Status now: {str(a['status_now']).lower().replace('_', ' ')}",
        "Correctness of the decision: not assessed",
    ]
