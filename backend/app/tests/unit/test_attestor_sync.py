"""The attestor's approved digests come from approved builds, not from hand-copied strings (CR-064).

The planner works on plain data: it fills one company's registration entries from the digests the platform found approved for them,
leaves every other company's entries alone, and says so when an entry has nothing approved behind it."""

from __future__ import annotations

import json
from pathlib import Path

from zid_platform.attestor import registration, sync

T1 = "11111111-1111-4111-8111-111111111111"
T2 = "22222222-2222-4222-8222-222222222222"
A, B, C = ("sha256:" + x * 64 for x in "abc")


def sid(tenant: str, n: int) -> str:
    return f"spiffe://uk-1.prod.id.zoiko/t/{tenant}/ns/pay/sa/w{n}"


def entry(tenant: str, n: int, digests: list[str]) -> dict[str, object]:
    return {
        "spiffe_id": sid(tenant, n),
        "environment_id": "prod",
        "selectors": {"docker.container": f"w{n}"},
        "approved_digests": digests,
    }


def test_an_entry_gets_exactly_the_digests_of_its_approved_bindings() -> None:
    raw = {"workloads": [entry(T1, 1, [A]), entry(T1, 2, [])]}
    plan = sync.plan(raw, {sid(T1, 1): frozenset({A, B}), sid(T1, 2): frozenset({C})}, tenant_id=T1)
    got = {w["spiffe_id"]: w["approved_digests"] for w in plan.registrations["workloads"]}
    assert got == {sid(T1, 1): [A, B], sid(T1, 2): [C]}
    assert [(c.spiffe_id, c.added, c.removed) for c in plan.changes] == [
        (sid(T1, 1), (B,), ()),
        (sid(T1, 2), (C,), ()),
    ]
    assert plan.warnings == ()


def test_a_digest_that_is_no_longer_approved_is_removed() -> None:
    raw = {"workloads": [entry(T1, 1, [A, B])]}
    plan = sync.plan(raw, {sid(T1, 1): frozenset({B})}, tenant_id=T1)
    assert plan.registrations["workloads"][0]["approved_digests"] == [B]
    assert plan.changes[0].removed == (A,) and plan.changes[0].added == ()


def test_nothing_changes_when_the_file_already_matches() -> None:
    raw = {"workloads": [entry(T1, 1, [B, A])]}  # order does not matter
    plan = sync.plan(raw, {sid(T1, 1): frozenset({A, B})}, tenant_id=T1)
    assert not plan.changed and plan.registrations == raw


def test_an_entry_with_nothing_approved_behind_it_is_left_alone_and_reported() -> None:
    raw = {"workloads": [entry(T1, 1, [A])]}
    plan = sync.plan(raw, {}, tenant_id=T1)
    assert plan.registrations["workloads"][0]["approved_digests"] == [A], "never silently emptied"
    assert not plan.changed
    assert (
        len(plan.warnings) == 1
        and sid(T1, 1) in plan.warnings[0]
        and "approved" in plan.warnings[0]
    )


def test_another_companys_entries_are_never_touched() -> None:
    raw = {"workloads": [entry(T1, 1, [A]), entry(T2, 1, [C])]}
    plan = sync.plan(raw, {sid(T1, 1): frozenset({B}), sid(T2, 1): frozenset({A})}, tenant_id=T1)
    assert plan.registrations["workloads"][1] == entry(T2, 1, [C])
    assert plan.registrations["workloads"][0]["approved_digests"] == [B]


def test_selectors_and_other_fields_are_kept_and_the_input_is_not_mutated() -> None:
    raw = {"workloads": [{**entry(T1, 1, [A]), "max_level": 2}]}
    before = json.dumps(raw, sort_keys=True)
    plan = sync.plan(raw, {sid(T1, 1): frozenset({B})}, tenant_id=T1)
    out = plan.registrations["workloads"][0]
    assert out["selectors"] == {"docker.container": "w1"} and out["max_level"] == 2
    assert json.dumps(raw, sort_keys=True) == before


def test_the_result_is_a_valid_registration_file_for_the_attestor(tmp_path: Path) -> None:
    raw = {"workloads": [entry(T1, 1, [A])]}
    plan = sync.plan(raw, {sid(T1, 1): frozenset({B, C})}, tenant_id=T1)
    f = tmp_path / "workloads.json"
    f.write_text(json.dumps(plan.registrations), encoding="utf-8")
    assert registration.load(f)[0].approved_digests == frozenset({B, C})


def test_the_tenant_is_read_from_the_spiffe_id() -> None:
    assert sync.tenant_of(sid(T1, 1)) == T1
    assert sync.tenant_of("spiffe://uk-1.prod.id.zoiko/ns/pay/sa/w1") is None
    assert sync.tenant_of(sid(T1.upper(), 1)) == T1
