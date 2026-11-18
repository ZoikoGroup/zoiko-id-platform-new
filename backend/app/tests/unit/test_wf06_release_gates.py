"""WF06 release gates G0 to G3 (CR-113): named roles, distinct people, expiry, open defects (ENG-20, QA-053)."""

from __future__ import annotations

from dataclasses import replace
from datetime import UTC, datetime, timedelta

import pytest
from zid_platform.modules.screening.domain import release_gates as rg

NOW = datetime(2026, 11, 18, 12, 0, tzinfo=UTC)
SCOPE = rg.Scope("GB", rg.Module.SANCTIONS)


def signoff(gate="G0", role="LEGAL", signer="s-legal", **kw):
    base = {
        "scope": SCOPE,
        "gate": gate,
        "role": role,
        "signer": signer,
        "evidence_ref": "ev_1",
        "signed_at": NOW,
        "expires_at": NOW + timedelta(days=180),
    }
    base.update(kw)
    return rg.Signoff(**base)


def full(**kw) -> list[rg.Signoff]:
    out = []
    n = 0
    for gate, roles in rg.GATES.items():
        for role in sorted(roles):
            n += 1
            out.append(signoff(gate, role, f"person-{n}", **kw))
    return out


def code(fn) -> str:
    with pytest.raises(rg.GateError) as e:
        fn()
    return e.value.code


def test_all_four_gates_with_every_named_role_make_a_scope_go() -> None:
    s = rg.status(SCOPE, full(), open_p0=0, now=NOW)
    assert s.go and s.blockers == () and all(m == () for m in s.gates.values())


def test_a_missing_role_on_any_gate_blocks_and_is_named() -> None:
    partial = [x for x in full() if not (x.gate == "G2" and x.role == "QA")]
    s = rg.status(SCOPE, partial, open_p0=0, now=NOW)
    assert not s.go and s.gates["G2"] == ("QA",) and s.blockers == ("G2_MISSING:QA",)
    none = rg.status(SCOPE, [], open_p0=0, now=NOW)
    assert not none.go and len(none.blockers) == 4


def test_an_open_p0_defect_or_a_hold_blocks_whatever_is_signed() -> None:
    s = rg.status(SCOPE, full(), open_p0=2, now=NOW)  # QA-053
    assert not s.go and s.blockers == ("OPEN_P0_DEFECTS:2",)
    held = rg.status(SCOPE, full(), open_p0=0, now=NOW, hold=["KILL_SWITCH"])
    assert not held.go and held.blockers == ("HELD:KILL_SWITCH",)


def test_an_expired_or_rescinded_signoff_no_longer_counts() -> None:
    expired = rg.status(SCOPE, full(), open_p0=0, now=NOW + timedelta(days=181))
    assert not expired.go and len(expired.blockers) == 4
    rescinded = [
        replace(x, rescinded_at=NOW) if (x.gate, x.role) == ("G1", "SECURITY") else x
        for x in full()
    ]
    s = rg.status(SCOPE, rescinded, open_p0=0, now=NOW + timedelta(hours=1))
    assert not s.go and s.gates["G1"] == ("SECURITY",)
    future = rg.status(SCOPE, full(), open_p0=0, now=NOW - timedelta(days=1))
    assert not future.go  # not yet signed


def test_a_signoff_for_another_market_or_module_does_not_count() -> None:
    other = [replace(x, scope=rg.Scope("DE", rg.Module.SANCTIONS)) for x in full()]
    assert not rg.status(SCOPE, other, open_p0=0, now=NOW).go
    media = [replace(x, scope=rg.Scope("GB", rg.Module.ADVERSE_MEDIA)) for x in full()]
    assert not rg.status(SCOPE, media, open_p0=0, now=NOW).go


def test_a_signoff_needs_a_known_gate_a_role_that_signs_it_evidence_and_a_sane_validity() -> None:
    assert code(lambda: rg.check_signoff([], signoff(gate="G9"), now=NOW)) == "GATE_UNKNOWN"
    assert (
        code(lambda: rg.check_signoff([], signoff(gate="G1", role="LEGAL"), now=NOW))
        == "ROLE_NOT_FOR_GATE"
    )
    assert (
        code(lambda: rg.check_signoff([], signoff(evidence_ref=" "), now=NOW))
        == "EVIDENCE_REQUIRED"
    )
    assert (
        code(lambda: rg.check_signoff([], signoff(expires_at=NOW), now=NOW)) == "VALIDITY_INVALID"
    )
    assert (
        code(lambda: rg.check_signoff([], signoff(expires_at=NOW + timedelta(days=366)), now=NOW))
        == "VALIDITY_INVALID"
    )
    rg.check_signoff([], signoff(), now=NOW)


def test_a_role_signs_a_gate_once_and_one_person_signs_in_one_role_only() -> None:
    first = signoff("G0", "LEGAL", "alice")
    assert (
        code(lambda: rg.check_signoff([first], signoff("G0", "LEGAL", "bob"), now=NOW))
        == "ROLE_ALREADY_SIGNED"
    )
    assert (
        code(lambda: rg.check_signoff([first], signoff("G0", "DPO", "alice"), now=NOW))
        == "SIGNER_HOLDS_ANOTHER_ROLE"
    )
    assert (
        code(lambda: rg.check_signoff([first], signoff("G3", "DPO", "alice"), now=NOW))
        == "SIGNER_HOLDS_ANOTHER_ROLE"
    )
    rg.check_signoff(
        [first], signoff("G3", "LEGAL", "alice"), now=NOW
    )  # the same person, in the same role, on another gate
    rg.check_signoff([first], signoff("G0", "DPO", "bob"), now=NOW)


def test_a_lapsed_signoff_frees_the_role_to_be_signed_again() -> None:
    old = signoff("G0", "LEGAL", "alice", expires_at=NOW + timedelta(days=1))
    rg.check_signoff(
        [old],
        signoff(
            "G0",
            "LEGAL",
            "bob",
            signed_at=NOW + timedelta(days=2),
            expires_at=NOW + timedelta(days=90),
        ),
        now=NOW + timedelta(days=2),
    )
