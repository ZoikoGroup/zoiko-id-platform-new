"""The central step-up route map (ZID-ENG-DEC-009; slice L-9, CR-046 addendum): the shipped map is valid, guards only real step-up actions, leaves the routes
that guard themselves alone, and binds the proof to the route, its parameters and the exact request body."""

from __future__ import annotations

import asyncio
import hashlib
import json
from typing import Any

import pytest
from zid_errors import ZidError
from zid_platform.modules.authentication.api import step_up as su
from zid_platform.modules.authentication.domain.step_up import StepUpError, Tier
from zid_platform.modules.authentication.infrastructure import step_up_routes as sr
from zid_platform.modules.authentication.infrastructure.step_up_config import step_up_catalogue

CAT = step_up_catalogue()


def _parse(*rules: dict[str, Any]) -> tuple[sr.RouteAction, ...]:
    return sr.parse({"rules": list(rules)}, CAT)


# ----------------------------------------------------------------------------------------------------------------- the map


def test_the_shipped_map_loads_and_every_rule_names_an_action_that_needs_a_proof() -> None:
    sr.route_actions.cache_clear()
    rules = sr.route_actions().rules
    assert len(rules) > 25
    for r in rules:
        assert CAT.actions[r.action].tier is not Tier.S0, f"{r.match}: an S0 action needs no proof, so listing it guards nothing"


def test_routes_that_guard_themselves_are_not_listed_twice() -> None:
    m = sr.route_actions()
    for method, path in (
        ("POST", "/federation/integrations"),  # create guards itself
        ("POST", "/provisioning/sources"),
        ("POST", "/authn/authenticators/{authenticator_id}:revoke"),
        ("POST", "/v1/tenant/owners"),
        ("POST", "/v1/tenant/billing-grants"),
        ("POST", "/v1/billing/commitment-requests/{request_id}:approve"),
        ("PUT", "/v1/tenant/residency"),
        ("POST", "/v1/approval-cases/{case_id}/votes"),
    ):
        assert m.classify(method, path) is None, f"{method} {path} already calls require_step_up itself"


def test_the_listed_routes_resolve_to_the_documented_actions() -> None:
    m = sr.route_actions()
    assert m.classify("POST", "/v1/evidence/bulk-exports").action == "export-raw-id-proof-biometric-evidence-ubo-records-or-bulk-pii"
    assert m.classify("POST", "/v1/authority-grants/{grant_id}:revoke").action == "grant-expand-transfer-or-revoke-high-impact-delegated-authority"
    assert m.classify("POST", "/operator/break-glass").action == "activate-break-glass-privileged-support-access"
    assert m.classify("POST", "/sessions:revoke-all").action == "revoke-own-sessions-or-all-other-sessions"
    assert m.classify("PUT", "/v1/settings/authentication")
    assert m.classify("PUT", "/v1/settings/branding") is None, "only the sign-in settings area is a trust policy"
    assert m.classify("GET", "/v1/evidence/bulk-exports") is None


@pytest.mark.parametrize(
    ("rule", "message"),
    [
        ({"match": "POST /a", "action": "no-such-action"}, "not an action of the step-up catalogue"),
        ({"match": "nonsense", "action": "revoke-own-sessions-or-all-other-sessions"}, "METHOD /path"),
    ],
)
def test_a_map_naming_an_unknown_action_or_a_malformed_rule_does_not_load(rule: dict[str, Any], message: str) -> None:
    with pytest.raises(StepUpError, match=message):
        _parse(rule)


def test_a_rule_listed_twice_and_an_empty_map_are_refused() -> None:
    one = {"match": "POST /a", "action": "revoke-own-sessions-or-all-other-sessions"}
    with pytest.raises(StepUpError, match="listed twice"):
        _parse(one, one)
    with pytest.raises(StepUpError, match="empty"):
        sr.parse({"rules": []}, CAT)


# ----------------------------------------------------------------------------------------------------------------- the guard


class _Route:
    def __init__(self, path_format: str) -> None:
        self.path_format = path_format


class _Url:
    path = "/x"


class _Request:
    def __init__(self, method: str, path_format: str, body: bytes = b"", params: dict[str, Any] | None = None, mode: str = "enforce", headers: dict[str, str] | None = None) -> None:
        self.method = method
        self.scope = {"route": _Route(path_format)}
        self.url = _Url()
        self.path_params = params or {}
        self.headers = headers or {}
        self._body = body

        class _State:
            step_up_enforcement = mode

        class _App:
            state = _State()

        self.app = _App()

    async def body(self) -> bytes:
        return self._body


class _Caller:
    class ctx:  # noqa: N801
        tenant_id = "t-1"
        principal_id = "p-1"


class _Tx:
    async def __aenter__(self) -> object:
        return object()

    async def __aexit__(self, *a: object) -> None:
        return None


def _stub(monkeypatch: pytest.MonkeyPatch) -> list[dict[str, Any]]:
    seen: list[dict[str, Any]] = []

    async def fake(request: Any, conn: Any, caller: Any, **kw: Any) -> None:
        seen.append(kw)

    monkeypatch.setattr(su, "require_step_up", fake)
    monkeypatch.setattr(su, "tenant_transaction", lambda *a, **k: _Tx())
    monkeypatch.setattr(su, "request_engine", lambda r: object())
    return seen


def test_the_proof_is_bound_to_the_route_the_parameters_and_the_exact_body(monkeypatch: pytest.MonkeyPatch) -> None:
    seen = _stub(monkeypatch)
    body = b'{"reason":"x"}'
    asyncio.run(su.route_step_up_guard(_Request("POST", "/v1/authority-grants/{grant_id}:revoke", body, {"grant_id": "g-1"}), _Caller()))
    (kw,) = seen
    assert kw["action"] == "grant-expand-transfer-or-revoke-high-impact-delegated-authority"
    assert kw["resource"] == "tenant:t-1" and kw["bind_route"] is True
    assert kw["change"] == {
        "operation": kw["action"],
        "route": "POST /v1/authority-grants/{grant_id}:revoke",
        "params": {"grant_id": "g-1"},
        "body_sha256": hashlib.sha256(body).hexdigest(),
    }


def test_another_body_is_another_change(monkeypatch: pytest.MonkeyPatch) -> None:
    seen = _stub(monkeypatch)
    for body in (b'{"a":1}', b'{"a":2}'):
        asyncio.run(su.route_step_up_guard(_Request("POST", "/v1/mandates", body), _Caller()))
    assert seen[0]["change"]["body_sha256"] != seen[1]["change"]["body_sha256"]


def test_unlisted_routes_and_a_switched_off_platform_are_not_judged(monkeypatch: pytest.MonkeyPatch) -> None:
    seen = _stub(monkeypatch)
    asyncio.run(su.route_step_up_guard(_Request("GET", "/v1/evidence/receipts"), _Caller()))
    asyncio.run(su.route_step_up_guard(_Request("POST", "/v1/mandates", mode="off"), _Caller()))
    assert seen == []


def test_the_refusal_names_the_action_the_resource_and_the_change_so_a_client_can_retry(monkeypatch: pytest.MonkeyPatch) -> None:
    class _Who:
        security_state = "NORMAL"

    async def record_refusal(*a: Any, **k: Any) -> None:
        return None

    monkeypatch.setattr(su, "who_of", lambda caller: _Who())
    monkeypatch.setattr(su, "tenant_transaction", lambda *a, **k: _Tx())
    monkeypatch.setattr(su, "request_engine", lambda r: object())
    monkeypatch.setattr(su.service, "record_refusal", record_refusal)
    with pytest.raises(ZidError) as e:
        asyncio.run(su.route_step_up_guard(_Request("POST", "/v1/mandates", b"{}"), _Caller()))
    assert e.value.code == "STEP_UP_REQUIRED" and e.value.http_status == 403
    fields = {d.field: d.issue for d in e.value.details}
    assert fields["tier"] == "S2" and fields["binding"] == "route" and fields["resource"] == "tenant:t-1"
    assert json.loads(fields["change"])["route"] == "POST /v1/mandates"
    assert fields["action"] == "create-expand-high-privilege-ai-agent-mandate"


def test_shadow_logs_and_lets_the_request_through(monkeypatch: pytest.MonkeyPatch, caplog: pytest.LogCaptureFixture) -> None:
    class _Who:
        security_state = "NORMAL"

    monkeypatch.setattr(su, "who_of", lambda caller: _Who())
    monkeypatch.setattr(su, "tenant_transaction", lambda *a, **k: _Tx())
    monkeypatch.setattr(su, "request_engine", lambda r: object())
    with caplog.at_level("WARNING"):
        asyncio.run(su.route_step_up_guard(_Request("POST", "/v1/mandates", b"{}", mode="shadow"), _Caller()))
    assert any("step-up shadow" in r.message for r in caplog.records)
