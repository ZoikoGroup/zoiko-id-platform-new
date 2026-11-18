"""The background worker's step list (DEC-D5, DEC-D6): every scheduled job the platform relies on is wired, in an order that keeps one
failing step from stopping the rest. This pins the wiring only; each job's behavior is tested with its own module (screening passes,
company refresh, evidence, privacy, and so on)."""

from __future__ import annotations

from zid_platform.entrypoints.worker import steps
from zid_platform.settings import Settings


def names() -> list[str]:
    return [name for name, _ in steps(Settings(environment="test"))]


def test_every_scheduled_job_is_wired_once() -> None:
    wired = names()
    assert len(wired) == len(set(wired)), "a job listed twice would run twice per pass"
    for job in (
        "evidence", "operator_access", "privacy", "registry", "domains", "connector_credentials", "stale_connectors", "sandbox_identities",
        "offboarding", "deletion", "directory", "webhooks", "master_lists", "sanctions_lists", "screening", "company_refresh",
    ):  # fmt: skip
        assert job in wired, job


def test_the_company_refresh_runs_after_the_registry_and_screening_jobs_it_depends_on_for_fresh_inputs() -> (
    None
):
    wired = names()
    assert wired.index("company_refresh") > wired.index("registry")
    assert wired.index("company_refresh") > wired.index("screening")


def test_the_company_grade_recheck_runs_after_the_company_refresh_has_read_the_registers_again() -> (
    None
):
    wired = names()
    assert wired.index("company_grade_recheck") == wired.index("company_refresh") + 1


def test_every_step_is_an_async_callable() -> None:
    import inspect

    for name, step in steps(Settings(environment="test")):
        assert inspect.iscoroutinefunction(step), name


def test_the_access_readiness_queue_consumer_runs_after_the_agent_lifecycle_sweep() -> None:
    """CR-091: a queued run is preflighted against agents the lifecycle sweep has just suspended."""
    wired = names()
    assert wired.index("access_readiness") > wired.index("agent_lifecycle")


def test_the_access_readiness_step_does_nothing_while_the_switch_is_off() -> None:
    import asyncio
    from datetime import UTC, datetime

    step = dict(steps(Settings(environment="test")))["access_readiness"]

    class Untouchable:
        def __getattr__(self, name: str) -> object:
            raise AssertionError(f"the connection was used ({name}) while the switch is off")

    assert asyncio.run(step(Untouchable(), datetime.now(UTC))) == "off"  # type: ignore[arg-type]
