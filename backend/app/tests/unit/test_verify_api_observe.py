"""What the Verify API reports about itself (CR-160): the counters exist, carry no company or session label, and name how an authentication ended without saying who it was."""

from __future__ import annotations

from zid_platform.modules.verify_api.application import observe
from zid_telemetry.metrics import FORBIDDEN_LABELS


def test_every_counter_is_defined_with_labels_the_platform_allows() -> None:
    for counter in (
        observe.SESSIONS,
        observe.DECISIONS,
        observe.AUTH,
        observe.CASES,
        observe.GO_LIVE,
    ):
        assert counter.name.startswith("zid_verify_api_")
        assert counter.labels, counter.name
        for label in counter.labels:
            assert not FORBIDDEN_LABELS.search(label), f"{counter.name} carries {label}"


def test_an_authentication_is_described_by_how_it_ended_and_never_by_who() -> None:
    assert observe.auth_result(None) == "issued"
    assert observe.auth_result("AUTH_INVALID") == "denied"
    assert observe.auth_result("IP_NOT_ALLOWED") == "ip_denied"
    assert observe.auth_result("RATE_LIMITED") == "rate_limited"
    assert observe.auth_result("SOMETHING_ELSE") == "error"


def test_counters_count_and_keep_environments_apart() -> None:
    before_sandbox = observe.SESSIONS.value(environment="sandbox", event="started")
    before_prod = observe.SESSIONS.value(environment="production", event="started")
    observe.SESSIONS.inc(environment="sandbox", event="started")
    assert observe.SESSIONS.value(environment="sandbox", event="started") == before_sandbox + 1
    assert observe.SESSIONS.value(environment="production", event="started") == before_prod
