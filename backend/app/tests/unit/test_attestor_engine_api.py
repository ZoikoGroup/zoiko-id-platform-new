"""The attestor reads containers from the Docker Engine API when it runs in the platform image (VM deployment, deploy/gcp-vm).

The image has no docker command, so the attestor asks the engine's unix socket (mounted read-only) the two read questions the command would
have answered. These tests pin that it asks nothing else, reads the same facts, and fails closed when the engine says no."""

from __future__ import annotations

import httpx
import pytest
from zid_platform.attestor.inspectors import DockerInspector, InspectionError, engine_api_runner

GOOD = "sha256:" + "ab" * 32
OTHER = "sha256:" + "cd" * 32


def _engine(calls: list[tuple[str, str]], *, running: bool = True, status: int = 200) -> httpx.MockTransport:
    def handler(req: httpx.Request) -> httpx.Response:
        calls.append((req.method, req.url.path))
        if status != 200:
            return httpx.Response(status, json={"message": "No such container"})
        if req.url.path == "/containers/payments-agent/json":
            return httpx.Response(
                200,
                json={
                    "Name": "/payments-agent",
                    "Image": GOOD,
                    "Config": {"Labels": {"team": "payments"}},
                    "State": {"Running": running},
                },
            )
        if req.url.path == f"/images/{GOOD}/json":
            return httpx.Response(200, json={"RepoDigests": [f"registry.example/pay@{OTHER}"]})
        return httpx.Response(404, json={"message": "unexpected"})

    return httpx.MockTransport(handler)


def test_facts_through_the_engine_api_equal_facts_through_the_command() -> None:
    calls: list[tuple[str, str]] = []
    f = DockerInspector(run=engine_api_runner("/var/run/docker.sock", transport=_engine(calls))).inspect(
        "payments-agent"
    )
    assert f.running and f.digests == frozenset({GOOD, OTHER})
    assert f.selectors == {"docker.container": "payments-agent", "docker.label.team": "payments"}
    assert calls == [("GET", "/containers/payments-agent/json"), ("GET", f"/images/{GOOD}/json")]


def test_a_stopped_container_is_reported_as_not_running() -> None:
    run = engine_api_runner("/s", transport=_engine([], running=False))
    assert not DockerInspector(run=run).inspect("payments-agent").running


@pytest.mark.parametrize(
    "argv",
    [
        ["docker", "run", "alpine"],
        ["docker", "rm", "-f", "payments-agent"],
        ["docker", "exec", "payments-agent", "sh"],
        ["docker", "inspect"],
        ["docker", "inspect", "a", "b"],
        ["sh", "-c", "id"],
    ],
)
def test_nothing_but_the_two_read_calls_can_be_made(argv: list[str]) -> None:
    calls: list[tuple[str, str]] = []
    with pytest.raises(InspectionError):
        engine_api_runner("/s", transport=_engine(calls))(argv)
    assert calls == []  # refused before anything reached the engine


def test_an_engine_that_answers_no_is_an_inspection_error_never_a_pass() -> None:
    run = engine_api_runner("/s", transport=_engine([], status=404))
    with pytest.raises(InspectionError, match="404"):
        DockerInspector(run=run).inspect("ghost")


def test_an_unreachable_engine_is_an_inspection_error() -> None:
    def refuse(req: httpx.Request) -> httpx.Response:
        raise httpx.ConnectError("no socket")

    run = engine_api_runner("/s", transport=httpx.MockTransport(refuse))
    with pytest.raises(InspectionError, match="unreachable"):
        run(["docker", "inspect", "payments-agent"])

