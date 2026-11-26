"""The pure rules for the two-person approval of a production credential (CR-162)."""

from __future__ import annotations

from datetime import UTC, datetime, timedelta
from typing import Any

import pytest
from zid_platform.modules.verify_api.domain import approvals as a
from zid_platform.modules.verify_api.domain.model import PartnerError

NOW = datetime(2026, 11, 25, 10, 0, tzinfo=UTC)
ME, APP, CRED = "p-me", "app-1", "cred-1"
KEY = {"kty": "OKP", "crv": "Ed25519", "x": "abc", "kid": "k", "alg": "EdDSA", "use": "sig"}


def code(fn: Any, *args: Any, **kw: Any) -> str:
    with pytest.raises(PartnerError) as e:
        fn(*args, **kw)
    return e.value.code


def row(**over: Any) -> dict[str, Any]:
    base = {
        "status": "APPROVED",
        "expires_at": NOW + timedelta(hours=1),
        "requested_by": ME,
        "application_id": APP,
        "action": "ISSUE",
        "credential_id": None,
        "key_digest": None,
    }
    return {**base, **over}


def usable(r: dict[str, Any], **over: Any) -> None:
    kw: dict[str, Any] = {
        "principal": ME,
        "application_id": APP,
        "action": a.Action.ISSUE,
        "credential_id": None,
        "digest": None,
        "now": NOW,
    }
    a.check_usable(r, **{**kw, **over})


def test_an_action_has_the_right_shape() -> None:
    assert a.check_action("issue") is a.Action.ISSUE
    assert code(a.check_action, "delete") == "VALIDATION_FAILED"
    a.check_shape(a.Action.ISSUE, credential_id=None)
    a.check_shape(a.Action.ROTATE, credential_id="c")
    assert code(a.check_shape, a.Action.ROTATE, credential_id=None) == "VALIDATION_FAILED"
    assert code(a.check_shape, a.Action.ISSUE, credential_id="c") == "VALIDATION_FAILED"


def test_the_person_who_asked_never_decides_and_a_decision_needs_a_reason_when_it_refuses() -> None:
    a.check_decider("one", "two")
    assert code(a.check_decider, "one", "one") == "APPROVAL_SELF_DECISION"
    assert code(a.check_note, " ", required=True) == "NOTE_REQUIRED"
    assert a.check_note(None, required=False) == ""
    assert code(a.check_note, "x" * 1001, required=False) == "VALIDATION_FAILED"


def test_a_key_is_fingerprinted_the_same_way_every_time_and_a_different_key_differs() -> None:
    assert a.key_digest(None) is None
    d = a.key_digest(KEY)
    assert d is not None and len(d) == 32
    assert d == a.key_digest(
        dict(reversed(list(KEY.items())))
    )  # the order it was written in does not matter
    assert d != a.key_digest({**KEY, "x": "abd"})


def test_an_approval_can_be_spent_only_by_the_one_who_asked_once_approved_in_time_on_exactly_what_it_names() -> (
    None
):
    usable(row())
    for bad in (
        row(status="PENDING"),
        row(status="CONSUMED"),
        row(status="REJECTED"),
        row(status="CANCELED"),
        row(expires_at=NOW),  # expired the moment it is reached
        row(expires_at=NOW - timedelta(seconds=1)),
        row(requested_by="someone-else"),
        row(application_id="app-2"),
        row(action="ROTATE"),
        row(credential_id=CRED),
        row(key_digest=b"\x00" * 32),
    ):
        assert code(usable, bad) == "APPROVAL_REQUIRED", bad
    # a rotation approval names its credential and nothing else
    rot = row(action="ROTATE", credential_id=CRED)
    usable(rot, action=a.Action.ROTATE, credential_id=CRED)
    assert code(usable, rot, action=a.Action.ROTATE, credential_id="cred-2") == "APPROVAL_REQUIRED"
    # a key approval is for that key only, and a secret credential cannot be made with it
    keyed = row(key_digest=a.key_digest(KEY))
    usable(keyed, digest=a.key_digest(KEY))
    assert code(usable, keyed, digest=a.key_digest({**KEY, "x": "other"})) == "APPROVAL_REQUIRED"
    assert code(usable, keyed, digest=None) == "APPROVAL_REQUIRED"
