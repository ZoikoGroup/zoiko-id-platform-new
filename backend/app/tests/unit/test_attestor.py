"""The workload attestor (CR-042 design): it trusts the runtime and its own registration, never the workload."""

from __future__ import annotations

import json
from collections.abc import Sequence
from datetime import UTC, datetime, timedelta
from pathlib import Path

import httpx
import pytest
from zid_crypto import LocalSigner
from zid_platform.attestor import registration, service
from zid_platform.attestor.inspectors import (
    DockerInspector,
    Facts,
    InspectionError,
    KubernetesInspector,
)
from zid_platform.entrypoints import attestor as cli
from zid_platform.modules.machine.application import attestation as cell

TENANT = "22222222-2222-4222-8222-222222222222"
SID = f"spiffe://uk-1.test.id.zoiko/t/{TENANT}/ns/payments/sa/agent"
GOOD = "sha256:" + "ab" * 32
OTHER = "sha256:" + "cd" * 32
NOW = datetime(2026, 10, 28, 12, 0, tzinfo=UTC)
SIGNER = LocalSigner("attestor-1", "Ed25519")
KEYS = [SIGNER.public_jwk()]


def entry(**over: object) -> registration.Entry:
    base: dict[str, object] = {
        "spiffe_id": SID,
        "environment_id": "prod",
        "selectors": {"docker.container": "payments-agent"},
        "approved_digests": [GOOD],
    }
    return registration.parse({"workloads": [base | over]})[0]


def facts(digests: set[str], *, running: bool = True, name: str = "payments-agent") -> Facts:
    return Facts({"docker.container": name}, frozenset(digests), running)


# ---- registration ----------------------------------------------------------------------------------------


@pytest.mark.parametrize(
    "over",
    [
        {"spiffe_id": "spiffe://x/ns/no-tenant"},
        {"environment_id": "Not Valid"},
        {"selectors": {}},
        {"approved_digests": ["md5:abc"]},
        {"max_level": 4},
    ],
)
def test_a_registration_that_could_vouch_for_the_wrong_thing_is_refused(
    over: dict[str, object],
) -> None:
    with pytest.raises(registration.RegistrationError):
        entry(**over)


def test_an_empty_or_duplicated_registration_is_refused(tmp_path: Path) -> None:
    with pytest.raises(registration.RegistrationError):
        registration.parse({"workloads": []})
    one = {
        "spiffe_id": SID,
        "environment_id": "prod",
        "selectors": {"a": "b"},
        "approved_digests": [GOOD],
    }
    with pytest.raises(registration.RegistrationError, match="twice"):
        registration.parse({"workloads": [one, one]})
    bad = tmp_path / "bad.json"
    bad.write_text("{not json", encoding="utf-8")
    with pytest.raises(registration.RegistrationError):
        registration.load(bad)


# ---- what it will vouch for ------------------------------------------------------------------------------


def test_levels_follow_what_is_observed() -> None:
    e = entry()
    assert service.evaluate(e, facts({GOOD})).level == 2
    assert service.evaluate(e, facts({GOOD, OTHER})).digest == GOOD
    unapproved = service.evaluate(e, facts({OTHER}))
    assert (unapproved.level, unapproved.digest) == (1, OTHER)
    assert service.evaluate(e, facts({GOOD}, running=False)).level == 0
    assert service.evaluate(e, facts({GOOD}, name="something-else")).level == 0
    assert (
        service.evaluate(e, facts(set())).level == 1
    )  # running, selectors right, but no artifact observed


class _Quote:
    def __init__(self, ok: bool) -> None:
        self.ok = ok

    def verify(self, target: str, f: Facts) -> bool:
        return self.ok


def test_level_three_needs_a_hardware_verifier_and_a_registration_that_allows_it() -> None:
    e3 = entry(max_level=3)
    assert (
        service.evaluate(e3, facts({GOOD})).level == 2
    )  # no verifier: stops at 2 however it is registered
    assert service.evaluate(e3, facts({GOOD}), hardware=_Quote(False)).level == 2
    assert service.evaluate(e3, facts({GOOD}), hardware=_Quote(True)).level == 3
    assert (
        service.evaluate(entry(), facts({GOOD}), hardware=_Quote(True)).level == 2
    )  # an entry can cap it
    assert service.evaluate(entry(max_level=1), facts({GOOD})).level == 1


# ---- the statement is what the cell verifies --------------------------------------------------------------


def test_the_cell_accepts_exactly_what_the_attestor_signs() -> None:
    e = entry()
    found = service.evaluate(e, facts({GOOD}))
    token = service.statement(SIGNER, e, found, now=NOW)
    header, claims = cell.signature_checked(token, KEYS)
    s = cell.read_claims(header, claims)
    assert (s.spiffe_id, s.environment_id, s.artifact_digest, s.level, s.verifier) == (
        SID,
        "prod",
        GOOD,
        2,
        "attestor-1",
    )
    assert cell.timing_problem(s, NOW + timedelta(minutes=1)) is None
    assert (
        cell.timing_problem(s, NOW + service.DEFAULT_LIFETIME + timedelta(seconds=1))
        == "ATTESTATION_STALE"
    )
    assert cell.level_problem(s, KEYS) is None


def test_a_statement_never_names_a_digest_that_was_not_observed() -> None:
    e = entry()
    one = service.evaluate(e, facts({OTHER}))
    s = cell.read_claims(*cell.signature_checked(service.statement(SIGNER, e, one, now=NOW), KEYS))
    assert (s.level, s.artifact_digest) == (1, OTHER)
    with pytest.raises(ValueError):
        service.statement(SIGNER, e, service.Finding(0, None, "nothing"), now=NOW)
    with pytest.raises(ValueError):
        service.statement(SIGNER, e, service.Finding(1, None, "no digest"), now=NOW)


class _Raises:
    def inspect(self, target: str) -> Facts:
        raise InspectionError("engine down")


class _Fixed:
    def __init__(self, f: Facts) -> None:
        self.f = f

    def inspect(self, target: str) -> Facts:
        return self.f


def test_a_runtime_that_cannot_be_read_or_does_not_match_earns_nothing() -> None:
    found, token = service.attest_once(SIGNER, entry(), _Raises(), "payments-agent")
    assert token is None and found.level == 0 and "engine down" in found.why
    found, token = service.attest_once(
        SIGNER, entry(), _Fixed(facts({GOOD}, name="intruder")), "intruder"
    )
    assert token is None and found.level == 0
    found, token = service.attest_once(
        SIGNER, entry(), _Fixed(facts({GOOD})), "payments-agent", now=NOW
    )
    assert token is not None and found.level == 2


# ---- the inspectors --------------------------------------------------------------------------------------


def _docker(running: bool = True) -> DockerInspector:
    def run(args: Sequence[str]) -> str:
        if args[1] == "inspect":
            return json.dumps(
                [
                    {
                        "Name": "/payments-agent",
                        "Image": GOOD,
                        "Config": {"Labels": {"team": "payments"}},
                        "State": {"Running": running},
                    }
                ]
            )
        return json.dumps([{"RepoDigests": [f"registry.example/pay@{OTHER}"]}])

    return DockerInspector(run=run)


def test_docker_facts_come_from_the_engine() -> None:
    f = _docker().inspect("payments-agent")
    assert f.running and f.digests == frozenset({GOOD, OTHER})
    assert f.selectors == {"docker.container": "payments-agent", "docker.label.team": "payments"}
    assert not _docker(running=False).inspect("payments-agent").running


def test_a_container_the_engine_cannot_find_is_an_inspection_error() -> None:
    def run(args: Sequence[str]) -> str:
        raise InspectionError("No such object")

    with pytest.raises(InspectionError):
        DockerInspector(run=run).inspect("ghost")

    with pytest.raises(InspectionError):
        DockerInspector(run=lambda a: "[]").inspect("ghost")


def _pod(
    phase: str = "Running", image: str = f"docker-pullable://reg/pay@{GOOD}"
) -> dict[str, object]:
    return {
        "metadata": {"namespace": "payments", "name": "agent-0", "labels": {"app": "agent"}},
        "spec": {"serviceAccountName": "agent"},
        "status": {
            "phase": phase,
            "containerStatuses": [{"imageID": image, "state": {"running": {}}}],
        },
    }


def _k8s(handler: object) -> KubernetesInspector:
    return KubernetesInspector(
        "https://k8s.test",
        "tok",
        transport=httpx.MockTransport(handler),  # type: ignore[arg-type]
    )


def test_kubernetes_facts_come_from_the_cluster_api_with_the_attestors_own_token() -> None:
    seen: list[httpx.Request] = []

    def handler(req: httpx.Request) -> httpx.Response:
        seen.append(req)
        return httpx.Response(200, json=_pod())

    f = _k8s(handler).inspect("payments/agent-0")
    assert seen[0].url.path == "/api/v1/namespaces/payments/pods/agent-0"
    assert seen[0].headers["authorization"] == "Bearer tok"
    assert f.running and f.digests == frozenset({GOOD})
    assert (
        f.selectors["k8s.namespace"] == "payments" and f.selectors["k8s.service_account"] == "agent"
    )


def test_a_pod_that_is_missing_pending_or_unreachable_earns_nothing() -> None:
    with pytest.raises(InspectionError):
        _k8s(lambda r: httpx.Response(404)).inspect("payments/ghost")
    with pytest.raises(InspectionError):
        _k8s(lambda r: httpx.Response(200)).inspect("not-a-target")
    assert (
        not _k8s(lambda r: httpx.Response(200, json=_pod(phase="Pending")))
        .inspect("payments/agent-0")
        .running
    )

    def down(req: httpx.Request) -> httpx.Response:
        raise httpx.ConnectError("no route", request=req)

    with pytest.raises(InspectionError):
        _k8s(down).inspect("payments/agent-0")


# ---- sending, key handling -------------------------------------------------------------------------------


def test_the_cells_answer_is_reported_not_assumed() -> None:
    ok = httpx.Client(
        transport=httpx.MockTransport(lambda r: httpx.Response(200, json={"status": "VALID"}))
    )
    assert service.submit("https://cell.test/x", "tok", client=ok) == (200, "OK")
    bad = httpx.Client(
        transport=httpx.MockTransport(
            lambda r: httpx.Response(422, json={"error": {"code": "ATTESTATION_STALE"}})
        )
    )
    assert service.submit("https://cell.test/x", "tok", client=bad) == (422, "ATTESTATION_STALE")

    def down(req: httpx.Request) -> httpx.Response:
        raise httpx.ConnectError("no route", request=req)

    assert (
        service.submit(
            "https://cell.test/x", "tok", client=httpx.Client(transport=httpx.MockTransport(down))
        )[0]
        == 0
    )


def test_keygen_writes_a_private_key_once_and_prints_only_the_public_half(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    out = tmp_path / "attestor.pem"
    assert cli.main(["keygen", "--out", str(out), "--kid", "attestor-9"]) == 0
    printed = json.loads(capsys.readouterr().out)
    jwk = printed["keys"][0]
    assert jwk["kid"] == "attestor-9" and "d" not in jwk
    assert "PRIVATE KEY" in out.read_text(encoding="utf-8")
    assert (
        cli.main(["keygen", "--out", str(out), "--kid", "attestor-9"]) == 1
    )  # never overwrites a key
    # the signer built from that file signs statements the printed public key verifies
    signer = LocalSigner.from_pem("attestor-9", out.read_text(encoding="utf-8"))
    e = entry()
    token = service.statement(signer, e, service.evaluate(e, facts({GOOD})), now=datetime.now(UTC))
    cell.signature_checked(token, [jwk])
