"""Telemetry privacy, cardinality, SLO arithmetic and trace propagation (DEC-G5; SRE-001 §3-6;
OPS-03/05/06/07/08/23; SRE-02/03/05/06)."""

from __future__ import annotations

import io
import json
import logging

import pytest
from zid_telemetry import JsonFormatter
from zid_telemetry.metrics import CardinalityError, Registry
from zid_telemetry.redaction import REDACTED, redact, scrub
from zid_telemetry.slo import (
    DAY,
    HOUR,
    MINUTE,
    AlertClass,
    BudgetState,
    ChangeClass,
    WindowedSli,
    alert_class,
    budget_remaining,
    budget_state,
    burn_rate,
    change_decision,
    evaluate,
)
from zid_telemetry.tracing import parse_traceparent, start_trace

JWT = "eyJhbGciOiJFUzI1NiJ9.eyJzdWIiOiIxMjM0NTY3ODkwIn0.c2lnbmF0dXJlLXZhbHVl"  # zid-secret-scan: allow (synthetic fixture)


# ------------------------------------------------------------------- redaction (OPS-07, SRE-05)


@pytest.mark.parametrize(
    "fixture",
    [
        f"token={JWT}",
        "Authorization: Bearer abcdefghijklmnop123456",
        "-----BEGIN PRIVATE KEY----- MIIE...",  # zid-secret-scan: allow (synthetic fixture)
        "user alice@example.com failed",
        "card 4111 1111 1111 1111 declined",
        "password=hunter2-correct-horse",
        "client_secret: s3cr3t-value",
    ],
)
def test_secret_and_pii_fixtures_are_scrubbed(fixture: str) -> None:
    out = scrub(fixture)
    assert REDACTED in out
    for needle in (
        JWT,
        "abcdefghijklmnop123456",
        "alice@example.com",
        "4111 1111",
        "hunter2",
        "s3cr3t",
    ):
        assert needle not in out


def test_opaque_ids_survive_and_unknown_keys_are_dropped() -> None:
    tenant = "01a162e5-60d1-76f1-9e47-4f1fb5c42b00"
    attrs = {
        "zoiko.tenant_id": tenant,
        "http.route": "/v1/mandates/{id}",
        "user.email": "a@b.co",
        "body": "{}",
    }
    assert redact(attrs) == {"zoiko.tenant_id": tenant, "http.route": "/v1/mandates/{id}"}
    assert scrub(
        "request req_01a162e5687472a29de05b484e6cf788 for 12345678-1234-1234-1234-123456789012"
    ) == ("request req_01a162e5687472a29de05b484e6cf788 for 12345678-1234-1234-1234-123456789012")


def test_log_lines_are_scrubbed_and_carry_the_trace() -> None:
    stream = io.StringIO()
    handler = logging.StreamHandler(stream)
    handler.setFormatter(JsonFormatter("svc", "uk-1"))
    log = logging.getLogger("test.telemetry")
    log.handlers[:] = [handler]
    log.propagate = False
    from zid_telemetry.tracing import reset_trace, set_trace

    trace = start_trace(None)
    token = set_trace(trace)
    try:
        log.warning(
            f"refresh failed for {JWT}", extra={"path": "/oauth2/token", "secret_hint": "x"}
        )
    finally:
        reset_trace(token)
    entry = json.loads(stream.getvalue())
    assert JWT not in entry["msg"] and REDACTED in entry["msg"]
    assert entry["trace_id"] == trace.trace_id and entry["path"] == "/oauth2/token"
    assert "secret_hint" not in entry  # not on the allowlist


# --------------------------------------------------------------------------- cardinality (OPS-08)


@pytest.mark.parametrize(
    "label", ["tenant_id", "principal_id", "request_id", "session", "email", "client_id"]
)
def test_identifier_labels_are_refused(label: str) -> None:
    with pytest.raises(CardinalityError):
        Registry().counter("zid_x_total", "x", (label,))


def test_series_budget_folds_into_overflow_and_is_alertable() -> None:
    r = Registry()
    c = r.counter("zid_route_total", "x", ("route",), max_series=3)
    for i in range(10):
        c.inc(route=f"/r{i}")
    assert len(c.values) == 4  # three admitted + the overflow series
    assert c.value(route="__overflow__") == 7
    text = r.exposition()
    assert 'zid_telemetry_cardinality_overflow_total{metric="zid_route_total"} 7' in text
    assert "# TYPE zid_route_total counter" in text


def test_histogram_exposition_and_quantile() -> None:
    r = Registry()
    h = r.histogram("zid_latency_seconds", "x", ("journey",), buckets=(0.05, 0.1, 1.0))
    for v in (0.01, 0.02, 0.07, 0.5):
        h.observe(v, journey="authorization")
    assert h.quantile(0.5, journey="authorization") == 0.05
    assert h.quantile(0.95, journey="authorization") == 1.0
    text = r.exposition()
    assert 'zid_latency_seconds_bucket{journey="authorization",le="0.1"} 3' in text
    assert 'zid_latency_seconds_count{journey="authorization"} 4' in text


# -------------------------------------------------------- SLOs and burn rates (OPS-03/05, SRE-02/03)


def test_burn_rate_and_budget() -> None:
    assert burn_rate(1, 1000, 0.999) == pytest.approx(1.0)
    assert burn_rate(0, 0, 0.999) == 0.0
    assert budget_remaining(1, 2000, 0.999) == pytest.approx(0.5)
    assert budget_remaining(1, 1, 1.0) == 0.0  # zero-budget objective


class Clock:
    def __init__(self) -> None:
        self.t = 1_000_000_000.0

    def __call__(self) -> float:
        return self.t


def fill(sli: WindowedSli, clock: Clock, minutes: int, per_minute: int, bad: int) -> None:
    for _ in range(minutes):
        for i in range(per_minute):
            sli.record(i >= bad)
        clock.t += MINUTE


def test_sustained_fast_burn_pages() -> None:
    clock = Clock()
    sli = WindowedSli(clock=clock)
    fill(sli, clock, 60, 100, 2)  # 2 % errors vs a 0.1 % budget: 20x burn for an hour
    results = evaluate(sli.counts, 0.999)
    assert alert_class(results) is AlertClass.PAGE
    assert any(r.window == "1h/5m" and r.firing for r in results)


def test_transient_spike_that_stopped_does_not_page() -> None:
    clock = Clock()
    sli = WindowedSli(clock=clock)
    fill(sli, clock, 3, 100, 50)  # a 3-minute 50 % spike ...
    fill(sli, clock, 40, 100, 0)  # ... then 40 healthy minutes: every short window has cooled
    results = evaluate(sli.counts, 0.999)
    one_hour = next(r for r in results if r.window == "1h/5m")
    assert one_hour.long_burn >= 14.4 and not one_hour.firing  # long window still hot, short cooled
    assert alert_class(results) is AlertClass.TICKET  # the budget really was spent: ticket, no page


def test_low_traffic_noise_does_not_page() -> None:
    clock = Clock()
    sli = WindowedSli(clock=clock)
    fill(sli, clock, 5, 1, 1)  # five requests, all failing
    assert alert_class(evaluate(sli.counts, 0.999)) is AlertClass.NONE


def test_zero_budget_objective_fires_on_one_event() -> None:
    clock = Clock()
    sli = WindowedSli(clock=clock)
    sli.record(False)
    assert alert_class(evaluate(sli.counts, 1.0)) is AlertClass.PAGE


def test_window_ring_forgets_old_minutes() -> None:
    clock = Clock()
    sli = WindowedSli(horizon_s=HOUR, clock=clock)
    fill(sli, clock, 10, 10, 10)
    clock.t += 2 * HOUR
    assert sli.counts(HOUR) == (0.0, 0.0)
    assert sli.counts(3 * DAY) == (0.0, 0.0)


# --------------------------------------------------------- error-budget release policy (OPS-23)


@pytest.mark.parametrize(
    ("remaining", "state"),
    [
        (0.9, BudgetState.NORMAL),
        (0.4, BudgetState.HEIGHTENED_REVIEW),
        (0.2, BudgetState.RELIABILITY_APPROVAL),
        (0.05, BudgetState.RELIABILITY_FREEZE),
        (0.0, BudgetState.EXHAUSTED),
        (-0.3, BudgetState.EXHAUSTED),
    ],
)
def test_budget_bands(remaining: float, state: BudgetState) -> None:
    assert budget_state(remaining) is state


def test_exhausted_budget_blocks_features_but_not_fixes() -> None:
    blocked = change_decision(BudgetState.EXHAUSTED, ChangeClass.STANDARD)
    assert not blocked.allowed and blocked.reason == "ERROR_BUDGET_EXHAUSTED"
    for change in (ChangeClass.FIX, ChangeClass.SECURITY):
        d = change_decision(BudgetState.EXHAUSTED, change)
        assert d.allowed and d.requires == "incident_commander"
    assert not change_decision(BudgetState.NORMAL, ChangeClass.STANDARD, frozen=True).allowed
    assert (
        change_decision(BudgetState.HEIGHTENED_REVIEW, ChangeClass.RISKY).requires
        == "heightened_review"
    )
    assert change_decision(BudgetState.HEIGHTENED_REVIEW, ChangeClass.STANDARD).requires is None


# -------------------------------------------------------------- trace context (OPS-06, SRE-06)


def test_valid_traceparent_is_continued_and_invalid_is_replaced() -> None:
    inbound = "00-4bf92f3577b34da6a3ce929d0e0e4736-00f067aa0ba902b7-01"
    t = start_trace(inbound)
    assert (
        t.trace_id == "4bf92f3577b34da6a3ce929d0e0e4736" and t.parent_span_id == "00f067aa0ba902b7"
    )
    assert t.span_id != "00f067aa0ba902b7" and t.traceparent().startswith(
        "00-4bf92f3577b34da6a3ce929d0e0e4736-"
    )
    for bad in (
        "garbage",
        "00-" + "0" * 32 + "-00f067aa0ba902b7-01",
        inbound.replace("00-", "ff-", 1),
        None,
    ):
        assert parse_traceparent(bad) is None
        assert start_trace(bad).parent_span_id is None


def test_gauge_holds_the_last_value_and_is_exposed() -> None:
    r = Registry()
    g = r.gauge("zid_outbox_oldest_unpublished_seconds", "x", ("lane",))
    g.set(3.5, lane="priority")
    g.set(1.25, lane="priority")
    assert g.value(lane="priority") == 1.25
    text = r.exposition()
    assert "# TYPE zid_outbox_oldest_unpublished_seconds gauge" in text
    assert 'zid_outbox_oldest_unpublished_seconds{lane="priority"} 1.25' in text
