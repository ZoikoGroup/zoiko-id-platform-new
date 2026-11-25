"""The Command Center's rules (ZID-CPS-WF-02 sections 05, 07, 09; Q03, Q04, Q06, Q08, Q09, Q12, Q14, Q18, Q23, Q30, Q31, Q36)."""

from __future__ import annotations

from datetime import UTC, datetime, timedelta

from zid_platform.modules.verify_analytics.application.ingest import ALLOWED, short_name, wanted
from zid_platform.modules.verify_analytics.application.service import cell
from zid_platform.modules.verify_analytics.domain import access as ac
from zid_platform.modules.verify_analytics.domain import metrics as mx
from zid_platform.modules.verify_analytics.domain.definitions import CATALOGUE

NOW = datetime(2026, 11, 25, 12, tzinfo=UTC)
T0 = NOW - timedelta(days=3)


def f(ref: str, t: str, minutes: int, **attrs: object) -> dict[str, object]:
    return {"session_ref": ref, "event_type": f"verification.session.{t}", "occurred_at": T0 + timedelta(minutes=minutes),
            "environment": "production", "market": "GB", "attrs": attrs}  # fmt: skip


def created(ref: str) -> dict[str, object]:
    return f(ref, "created", 0, expires_at=(T0 + timedelta(hours=24)).isoformat())


def approved_journey(ref: str, *, reviewer: bool = False, first: str = "ACCEPTED") -> list[dict[str, object]]:
    return [
        created(ref), f(ref, "notice_presented", 1), f(ref, "method_selected", 2, method="WEB_AUTO"), f(ref, "capture_started", 3),
        f(ref, "capture_attempt_completed", 4, attempt_no=1, result=first), f(ref, "submitted", 5),
        f(ref, "finalized", 6, state="FINAL_APPROVED", outcome="APPROVED", decision_version=3, decided_by="REVIEWER" if reviewer else "AUTOMATED"),
    ]  # fmt: skip


def test_the_catalogue_names_m01_to_m16_each_with_a_version_owner_and_basis() -> None:
    assert sorted(CATALOGUE) == [f"M{i:02d}" for i in range(1, 17)]
    for m in CATALOGUE.values():
        assert m.version and m.owner and m.numerator and m.sources
    assert {m.metric_id for m in CATALOGUE.values() if m.finance} == {"M13", "M14"}


def test_a_journey_counts_once_however_many_events_and_attempts_it_has() -> None:
    js = mx.journeys(approved_journey("a") + [f("a", "capture_attempt_completed", 4, attempt_no=2, result="ACCEPTED")])
    assert len(js) == 1 and js[0].attempts == [(1, "ACCEPTED"), (2, "ACCEPTED")]
    m = mx.session_metrics(js, now=NOW, k=1, quality="OBSERVED")
    assert m["M01"]["value"] == 1 and m["M03"]["numerator"] == 1 and m["M04"]["value"] == 1.0


def test_automated_rate_counts_any_reviewer_as_human_and_first_attempt_uses_attempt_one() -> None:
    js = mx.journeys(approved_journey("a") + approved_journey("b", reviewer=True) + approved_journey("c", first="RETAKE_NEEDED"))
    m = mx.session_metrics(js, now=NOW, k=1, quality="OBSERVED")
    assert (m["M05"]["numerator"], m["M05"]["denominator"]) == (2, 3)  # Q14
    assert (m["M06"]["numerator"], m["M06"]["denominator"]) == (2, 3)  # Q06: attempt 1 decides, not a later retry
    assert mx.first_attempt_causes(js, k=1) == [{"cause": "RETAKE_NEEDED", "count": 1, "denominator": 1}]


def test_a_superseded_decision_keeps_its_history_and_the_newest_version_is_effective() -> None:
    js = mx.journeys(approved_journey("a") + [f("a", "finalized", 9, state="FINAL_DECLINED", outcome="REJECTED", decision_version=5, decided_by="REVIEWER")])
    assert js[0].final["state"] == "FINAL_DECLINED" and len(js[0].decisions) == 2
    assert mx.session_metrics(js, now=NOW, k=1, quality="OBSERVED")["M10"]["series"]["overturned"] == 1


def test_unmatured_sessions_are_not_counted_as_lost_and_missing_events_are_unknown_not_abandonment() -> None:
    fresh = [f("new", "created", 0, expires_at=(NOW + timedelta(hours=1)).isoformat())]
    gap = [created("gap"), f("gap", "submitted", 5)]  # a submission with no capture start: an instrumentation gap
    gone = [created("gone"), f("gone", "notice_presented", 1)]
    js = mx.journeys(fresh + gap + gone)
    fun = mx.funnel(js, now=NOW, k=1, cohort="created")
    assert fun["steps"][0]["count"] == 2  # Q09: the unmatured session is excluded
    assert {r["reason"]: r["count"] for r in fun["drop_off"]} == {"UNKNOWN": 1, "EXPIRED": 1}  # Q08


def test_small_groups_are_withheld_and_a_lone_withheld_row_takes_the_next_smallest_with_it() -> None:
    rows = [{"market": "GB", "denominator": 50, "value": 0.5}, {"market": "FR", "denominator": 30, "value": 0.4}, {"market": "IE", "denominator": 3, "value": 1.0}]
    out = mx.protect_rows(rows, k=20)
    assert [r.get("suppressed", False) for r in out] == [False, True, True]  # Q36: IE alone would be revealed by the total
    assert out[2]["value"] is None and out[2]["denominator"] is None
    assert mx.result("M03", 2, 5, k=20, quality="OBSERVED")["suppressed"] is True
    assert mx.result("M03", 0, 0, k=20, quality="OBSERVED")["quality"] == "NO_DATA"


def test_review_sla_breach_uses_the_due_time_and_open_reviews_past_due_count() -> None:
    due = (T0 + timedelta(hours=1)).isoformat()
    late = [created("l"), f("l", "submitted", 5), f("l", "review_required", 6, due_at=due), f("l", "finalized", 120, state="FINAL_APPROVED", decision_version=4)]
    ok = [created("o"), f("o", "submitted", 5), f("o", "review_required", 6, due_at=due), f("o", "finalized", 30, state="FINAL_APPROVED", decision_version=4)]
    stuck = [created("s"), f("s", "submitted", 5), f("s", "review_required", 6, due_at=due)]
    m = mx.session_metrics(mx.journeys(late + ok + stuck), now=NOW, k=1, quality="OBSERVED")
    assert (m["M09"]["numerator"], m["M09"]["denominator"]) == (2, 3)


def test_a_webhook_event_counts_once_and_only_a_2xx_is_delivered() -> None:
    at = T0
    rows = [
        {"event_type": "webhook.delivery.failed", "delivery_id": "d1", "final": False, "attempt": 1, "occurred_at": at},
        {"event_type": "webhook.delivery.succeeded", "delivery_id": "d1", "attempt": 2, "delay_ms": 900, "occurred_at": at + timedelta(seconds=5)},
        {"event_type": "webhook.delivery.failed", "delivery_id": "d2", "final": False, "attempt": 1, "occurred_at": at},  # still retrying: not final
        {"event_type": "webhook.delivery.exhausted", "delivery_id": "d3", "attempt": 8, "occurred_at": at},
    ]
    r = mx.webhook_delivery(rows, k=1, quality="OBSERVED")
    assert (r["numerator"], r["denominator"]) == (1, 2) and r["dead_lettered"] == 1 and r["retried"] == 1  # Q30, Q31


def test_provider_errors_exclude_policy_denials() -> None:
    checks = [{"provider": "imaging", "outcome": "OK", "latency_ms": 100}, {"provider": "imaging", "outcome": "TECHNICAL_ERROR", "latency_ms": 900},
              {"provider": "imaging", "outcome": "POLICY_DENIED"}]  # fmt: skip
    r = mx.provider_quality(checks, k=1)[0]
    assert (r["numerator"], r["denominator"], r["policy_denials"]) == (1, 2, 1)  # Q23


def test_a_source_past_the_policy_makes_figures_provisional_and_none_is_no_data() -> None:
    wm = [{"source": "verification", "last_occurred_at": NOW, "last_ingested_at": NOW - timedelta(hours=2), "accepted": 5, "late": 0, "rejected": 0}]
    assert mx.freshness(wm, now=NOW, stale_after=timedelta(minutes=15))[0] == "PROVISIONAL"  # Q12
    assert mx.freshness([], now=NOW, stale_after=timedelta(minutes=15))[0] == "NO_DATA"


def test_analytics_bundles_never_carry_delegation_and_only_an_owner_grants() -> None:
    assert ac.allowed(("OWNER",), frozenset(), "analytics.access.delegate")
    assert not ac.allowed(("ADMIN",), frozenset(), "analytics.read")  # an Administrator has nothing by being one
    assert ac.allowed(("MEMBER",), ac.BUNDLES["AUDITOR"], "analytics.export")
    assert all("analytics.access.delegate" not in keys for keys in ac.BUNDLES.values())
    for kw in ({"actor_roles": ("ADMIN",)}, {"grantee": "me"}, {"grantee_is_member": False}, {"bundle": "SUPERUSER"}):
        args = {"actor_roles": ("OWNER",), "actor": "me", "grantee": "you", "grantee_is_member": True, "bundle": "ANALYTICS_VIEWER", "now": NOW, "expires_at": None, **kw}
        try:
            ac.check_grant(**args)  # type: ignore[arg-type]
        except ac.AccessError:
            continue
        raise AssertionError(kw)


def test_only_allow_listed_events_and_fields_are_ingested() -> None:
    assert short_name("com.zoiko.id.verification.session.created.v1") == "verification.session.created"
    assert short_name("verification.session.created") is None  # unversioned: refused
    assert wanted("com.zoiko.id.webhook.delivery.succeeded.v1") and not wanted("com.zoiko.id.tenant.member.added.v1")
    banned = {"name", "surname", "document_number", "birth_date", "image", "media", "selfie", "email"}
    assert not any(banned & set(fields) for fields in ALLOWED.values())


def test_an_exported_cell_is_never_a_formula() -> None:
    assert cell("=1+1") == "'=1+1" and cell("@SUM(A1)") == "'@SUM(A1)" and cell("GB") == "GB" and cell(None) == ""
