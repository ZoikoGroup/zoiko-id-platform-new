"""WF-05 postal confirmation code (A08, QA-028; CR-115). Synthetic only."""

from __future__ import annotations

from datetime import UTC, datetime, timedelta

import pytest
from zid_platform.modules.verification.domain import postal_challenge as pc

NOW = datetime(2026, 11, 18, 9, 0, tzinfo=UTC)
KEY = b"k" * 32


def issue() -> tuple[pc.Postal, str]:
    return pc.issue(
        challenge_id="p-1", address_revision=2, key=KEY, ttl=timedelta(days=30), now=NOW
    )


def test_a_code_has_no_look_alikes_is_kept_hashed_and_is_used_once():
    p, code = issue()
    assert len(code) == pc.LENGTH and set(code) <= set(pc.ALPHABET) and not set(code) & set("01OI")
    assert code.encode() not in p.secret_hash and p.state is pc.State.PENDING_DISPATCH
    sent = pc.dispatched(p)
    done, ok = pc.confirm(
        sent,
        code.lower()[:4] + "-" + code.lower()[4:],
        key=KEY,
        address_revision=2,
        now=NOW + timedelta(days=3),
    )
    assert ok and done.state is pc.State.VERIFIED
    assert pc.confirm(done, code, key=KEY, address_revision=2, now=NOW + timedelta(days=3)) == (
        done,
        False,
    )  # QA-028: no replay


def test_an_expired_code_or_a_changed_address_never_verifies():
    p, code = issue()
    late, ok = pc.confirm(p, code, key=KEY, address_revision=2, now=NOW + timedelta(days=31))
    assert not ok and late.state is pc.State.EXPIRED
    moved, ok = pc.confirm(p, code, key=KEY, address_revision=3, now=NOW + timedelta(days=1))
    assert not ok and moved.state is pc.State.REVOKED


def test_five_wrong_codes_revoke_and_the_expiry_is_bounded():
    p, code = issue()
    for _ in range(pc.MAX_ATTEMPTS):
        p, ok = pc.confirm(
            p,
            "AAAAAAAA" if code != "AAAAAAAA" else "BBBBBBBB",
            key=KEY,
            address_revision=2,
            now=NOW,
        )
        assert not ok
    assert p.state is pc.State.REVOKED
    for ttl in (timedelta(days=1), timedelta(days=90)):
        with pytest.raises(pc.PostalError):
            pc.issue(challenge_id="x", address_revision=1, key=KEY, ttl=ttl, now=NOW)
