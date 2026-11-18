"""WF06 monitoring watches and rescreening (CR-113): no storms, no duplicates, honest freshness, delta reopening (QA-019, QA-032, QA-048, QA-050, QA-051)."""

from __future__ import annotations

from datetime import UTC, datetime, timedelta

from zid_platform.modules.screening.domain import monitoring_watch as m

NOW = datetime(2026, 11, 18, 12, 0, tzinfo=UTC)
ALL = frozenset(m.Trigger)


def sub(
    sid="s1", tenant="t1", subject="p1", sources=("OFAC_SDN",), triggers=ALL, active=True, **kw
):
    base = {
        "watch_id": sid,
        "tenant_id": tenant,
        "subject_ref": subject,
        "purpose": "KYB",
        "classes": frozenset({"SANCTIONS"}),
        "sources": frozenset(sources),
        "triggers": frozenset(triggers),
        "cadence": timedelta(days=1),
        "active": active,
    }
    base.update(kw)
    return m.Watch(**base)


def test_a_watch_activates_only_with_signed_gates_two_approvers_and_finance_for_paid() -> None:
    ready = sub(approved_by=("a", "b"))
    assert m.activation_problems(ready, gates_signed=True) == []
    assert m.activation_problems(ready, gates_signed=False) == ["RELEASE_GATES_NOT_SIGNED"]
    assert "TWO_APPROVERS_REQUIRED" in m.activation_problems(
        sub(approved_by=("a", "a")), gates_signed=True
    )
    paid = sub(approved_by=("a", "b"), providers=frozenset({"vendor-a"}))
    assert m.activation_problems(paid, gates_signed=True) == ["FINANCE_AUTHORISATION_MISSING"]
    assert m.activation_problems(
        sub(
            approved_by=("a", "b"),
            classes=frozenset(),
            sources=frozenset(),
            triggers=frozenset(),
            cadence=timedelta(0),
        ),
        gates_signed=True,
    ) == [
        "NO_CLASSES",
        "NO_SOURCES",
        "NO_TRIGGERS",
        "CADENCE_INVALID",
    ]


def test_a_list_release_queues_work_only_for_watches_that_depend_on_that_source() -> None:
    e = m.Event(m.Trigger.LIST_UPDATE, source_id="OFAC_SDN", source_version="v9")
    queue, deferred = m.plan([sub("s1"), sub("s2", sources=("UN_SC",)), sub("s3", active=False)], e)
    assert [w.watch_id for w in queue] == ["s1"] and deferred == []


def test_the_same_release_is_the_same_work_so_a_repeat_queues_nothing() -> None:
    e = m.Event(m.Trigger.LIST_UPDATE, source_id="OFAC_SDN", source_version="v9")
    first, _ = m.plan([sub()], e)
    again, _ = m.plan([sub()], e, already_queued=[w.key for w in first])  # QA-048
    assert len(first) == 1 and again == []
    other, _ = m.plan(
        [sub()], m.Event(m.Trigger.LIST_UPDATE, source_id="OFAC_SDN", source_version="v10")
    )
    assert other[0].key != first[0].key


def test_an_identity_or_ownership_change_rescreens_only_that_subject() -> None:
    e = m.Event(m.Trigger.UBO_GRAPH_CHANGE, tenant_id="t1", subject_ref="p1")
    queue, _ = m.plan(
        [sub("s1", subject="p1"), sub("s2", subject="p2"), sub("s3", tenant="t2", subject="p1")], e
    )
    assert [w.watch_id for w in queue] == ["s1"]  # QA-032: and never another tenant's


def test_a_trigger_a_watch_did_not_ask_for_does_nothing() -> None:
    e = m.Event(m.Trigger.IDENTITY_CHANGE, tenant_id="t1", subject_ref="p1")
    queue, _ = m.plan([sub(triggers={m.Trigger.LIST_UPDATE})], e)
    assert queue == []


def test_a_storm_is_deferred_and_visible_never_dropped() -> None:
    subs = [sub(f"s{i:03}", subject=f"p{i}") for i in range(7)]
    e = m.Event(m.Trigger.LIST_UPDATE, source_id="OFAC_SDN", source_version="v9")
    queue, deferred = m.plan(subs, e, storm_limit=3, queued_for_tenant={"t1": 1})
    assert len(queue) == 2 and len(deferred) == 5  # QA-051
    assert len(queue) + len(deferred) == 7
    assert {w.key for w in queue}.isdisjoint({w.key for w in deferred})


def test_the_schedule_picks_watches_whose_success_is_older_than_the_cadence() -> None:
    fresh = sub(
        "a",
        watermarks={
            "OFAC_SDN": m.Watermark(NOW - timedelta(hours=2), NOW - timedelta(hours=2), "x")
        },
    )
    old = sub(
        "b",
        watermarks={
            "OFAC_SDN": m.Watermark(NOW - timedelta(minutes=5), NOW - timedelta(days=2), "x")
        },
    )
    never = sub("c")
    off = sub("d", triggers={m.Trigger.LIST_UPDATE})
    assert m.due([fresh, old, never, off, sub("e", active=False)], NOW) == ["b", "c"]


def test_an_attempt_is_not_a_success_so_continuous_monitoring_cannot_be_claimed_on_attempts() -> (
    None
):
    wm = m.Watermark()
    wm = m.attempt(wm, now=NOW)
    s = sub(watermarks={"OFAC_SDN": wm})
    assert wm.last_attempt_at == NOW and wm.last_success_at is None
    assert not m.may_claim_continuous(s, NOW)
    s = sub(watermarks={"OFAC_SDN": m.success(wm, now=NOW, snapshot="sha256:aa")})
    assert m.may_claim_continuous(s, NOW + timedelta(hours=23))
    assert not m.may_claim_continuous(s, NOW + timedelta(days=2))
    failed_after = m.attempt(
        m.success(wm, now=NOW, snapshot="sha256:aa"), now=NOW + timedelta(days=2)
    )
    assert failed_after.last_success_at == NOW and failed_after.last_attempt_at == NOW + timedelta(
        days=2
    )


def test_every_source_must_have_a_recent_success_to_claim_it() -> None:
    wm = m.success(m.Watermark(), now=NOW, snapshot="x")
    two = sub(sources=("OFAC_SDN", "UN_SC"), watermarks={"OFAC_SDN": wm})
    assert not m.may_claim_continuous(two, NOW)
    both = sub(sources=("OFAC_SDN", "UN_SC"), watermarks={"OFAC_SDN": wm, "UN_SC": wm})
    assert m.may_claim_continuous(both, NOW)
    assert not m.may_claim_continuous(sub(active=False, watermarks={"OFAC_SDN": wm}), NOW)


def test_a_new_release_is_compared_entry_by_entry() -> None:
    delta = m.diff({"1": "a", "2": "b", "3": "c"}, {"1": "a", "2": "B", "4": "d"})
    assert delta.added == {"4"} and delta.removed == {"3"} and delta.changed == {"2"}
    assert m.diff({"1": "a"}, {"1": "a"}).empty


def test_a_delisting_or_a_changed_entry_reopens_the_cases_that_relied_on_it() -> None:
    delta = m.diff({"1": "a", "2": "b", "3": "c"}, {"1": "a", "2": "B"})
    cases = [
        m.OpenCase("c1", frozenset({"3"})),
        m.OpenCase("c2", frozenset({"2"})),
        m.OpenCase("c3", frozenset({"1"})),
        m.OpenCase("c4", frozenset()),
    ]
    assert m.reopen(delta, cases) == [
        ("c1", "ENTRY_DELISTED"),
        ("c2", "ENTRY_CHANGED"),
    ]  # QA-019, QA-050
    assert (
        m.reopen(m.diff({"1": "a"}, {"1": "a", "9": "z"}), cases) == []
    )  # a new entry rescreens people; it reopens no case
