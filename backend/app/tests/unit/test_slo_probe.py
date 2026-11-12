"""The SLO probe's arithmetic (ZID-INT-002 section 14.2): the numbers it reports are the ones the targets are judged on."""

from __future__ import annotations

from zid_platform import slo_probe as sp
from zid_platform.slo_probe import Sample


def test_percentile_is_nearest_rank() -> None:
    values = [float(i) for i in range(1, 101)]
    assert sp.percentile(values, 95) == 95.0
    assert sp.percentile(values, 50) == 50.0
    assert sp.percentile([7.0], 99) == 7.0
    assert sp.percentile([], 95) == 0.0


def test_a_client_error_is_the_service_working_and_a_5xx_or_silence_is_not() -> None:
    assert sp.served(200) and sp.served(401) and sp.served(422)
    assert not sp.served(500) and not sp.served(503) and not sp.served(0)


def test_targets_are_met_when_fast_and_available() -> None:
    samples = [Sample("GET /h", 200, 50.0)] * 1999 + [Sample("GET /h", 200, 380.0)]
    out = sp.summarise(samples)
    assert out["met"] is True and out["breaches"] == []
    assert out["measured"]["availability_percent"] == 100.0  # type: ignore[index]


def test_slow_p95_is_a_breach() -> None:
    samples = [Sample("GET /h", 200, 100.0)] * 90 + [Sample("GET /h", 200, 900.0)] * 10
    out = sp.summarise(samples)
    assert out["met"] is False
    assert any("p95" in b for b in out["breaches"])  # type: ignore[union-attr]


def test_one_failure_in_a_thousand_misses_the_availability_target() -> None:
    samples = [Sample("GET /h", 200, 10.0)] * 999 + [Sample("GET /h", 503, 10.0)]
    out = sp.summarise(samples)
    assert out["met"] is False
    assert any("availability" in b for b in out["breaches"])  # type: ignore[union-attr]


def test_failures_do_not_improve_the_latency_figures_and_no_samples_is_not_a_pass() -> None:
    out = sp.summarise([Sample("GET /h", 0, 1.0)] * 5)
    assert out["met"] is False
    assert sp.summarise([])["met"] is False


def test_run_probes_health_alone_without_a_token_and_intake_with_one() -> None:
    seen: list[str] = []

    def send(base, method, path, token, body, timeout):
        seen.append(f"{method} {path}")
        return Sample(f"{method} {path}", 200, 5.0)

    sp.run("http://x", None, 2, send=send)
    assert set(seen) == {"GET /health/ready"}
    seen.clear()
    report = sp.run("http://x", "tok", 2, send=send)
    assert {"POST /v2/age-checks", "GET /v2/coverage?country=GB"} <= set(seen) and report[
        "met"
    ] is True
