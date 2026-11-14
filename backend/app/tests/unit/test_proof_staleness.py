"""T20: a stale proof response says EXPIRED or RECHECK_REQUIRED when it is read; it is never returned as a cached active status."""

from __future__ import annotations

from datetime import UTC, datetime, timedelta

from zid_platform.modules.credential.domain import lifecycle as lc

NOW = datetime(2026, 11, 14, 12, tzinfo=UTC)


def _proof(**over: object) -> dict[str, object]:
    return {
        "decision": "VERIFIED",
        "credential_status": "ACTIVE",
        "source_checked_at": (NOW - timedelta(days=10)).isoformat(),
        "expires_at": (NOW + timedelta(days=100)).isoformat(),
        **over,
    }


def test_a_fresh_proof_inside_its_validity_is_current() -> None:
    assert lc.proof_status(_proof(), now=NOW) is lc.ProofStatus.CURRENT
    assert lc.proof_status(_proof(expires_at=None), now=NOW) is lc.ProofStatus.CURRENT


def test_a_proof_past_its_validity_is_expired_even_though_it_was_stored_as_active() -> None:
    gone = _proof(expires_at=(NOW - timedelta(seconds=1)).isoformat())
    assert gone["credential_status"] == "ACTIVE"
    assert lc.proof_status(gone, now=NOW) is lc.ProofStatus.EXPIRED
    assert lc.proof_status(_proof(expires_at=NOW.isoformat()), now=NOW) is lc.ProofStatus.EXPIRED


def test_a_source_answer_older_than_the_freshness_requires_a_recheck() -> None:
    old = _proof(source_checked_at=(NOW - timedelta(days=366)).isoformat())
    assert lc.proof_status(old, now=NOW) is lc.ProofStatus.RECHECK_REQUIRED
    tight = timedelta(days=5)
    assert lc.proof_status(_proof(), now=NOW, freshness=tight) is lc.ProofStatus.RECHECK_REQUIRED


def test_a_proof_whose_time_cannot_be_read_is_never_current() -> None:
    for bad in (None, "", "not a date"):
        assert (
            lc.proof_status(_proof(source_checked_at=bad), now=NOW)
            is lc.ProofStatus.RECHECK_REQUIRED
        )


def test_an_expiry_outranks_a_recheck() -> None:
    both = _proof(
        source_checked_at=(NOW - timedelta(days=900)).isoformat(),
        expires_at=(NOW - timedelta(days=1)).isoformat(),
    )
    assert lc.proof_status(both, now=NOW) is lc.ProofStatus.EXPIRED
