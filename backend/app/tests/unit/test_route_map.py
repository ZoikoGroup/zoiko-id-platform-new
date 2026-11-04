"""The route map (ZID-ENG-DEC-007; slice L-7, CR-044 addendum): every authenticated route of the cell is gated inline, mapped to a matrix action, or
exempt with a reason, so a new route cannot appear without a decision. Also what a bad map refuses and how the first matching rule wins."""

from __future__ import annotations

import asyncio
from typing import Any

import pytest
from zid_platform.api_auth import authenticated_caller
from zid_platform.main import create_app
from zid_platform.modules.tenant.domain.role_matrix import Mode
from zid_platform.modules.tenant.infrastructure import route_map as rm
from zid_platform.modules.tenant.infrastructure.role_matrix import role_matrix
from zid_platform.settings import Settings

MATRIX = role_matrix()


def _rules(*items: dict[str, Any]) -> tuple[rm.Rule, ...]:
    return rm.parse({"rules": list(items)}, MATRIX)


# ----------------------------------------------------------------------------------------------------------------- the matcher


def test_the_first_matching_rule_wins_and_parameters_are_written_empty() -> None:
    rules = rm.RouteMap(
        _rules(
            {"match": "POST /v1/x/{}:revoke", "gate": {"action": "a.disable-remove-ordinary-member", "mode": "X"}},
            {"match": "* /v1/x/*", "exempt": "OWN_ACCOUNT", "why": "own"},
        )
    )
    hit = rules.classify("POST", "/v1/x/{item_id}:revoke")
    assert hit and hit.action == "a.disable-remove-ordinary-member" and hit.mode is Mode.EXECUTE
    other = rules.classify("GET", "/v1/x/{item_id}")
    assert other and other.exempt == "OWN_ACCOUNT" and not other.gates
    assert rules.classify("GET", "/v1/y") is None


def test_a_method_star_matches_every_method_and_a_literal_method_only_itself() -> None:
    rules = rm.RouteMap(_rules({"match": "GET /a", "exempt": "PROTOCOL", "why": "x"}, {"match": "* /b", "exempt": "PROTOCOL", "why": "x"}))
    assert rules.classify("GET", "/a") and rules.classify("POST", "/a") is None
    assert rules.classify("DELETE", "/b")


@pytest.mark.parametrize(
    ("rule", "message"),
    [
        ({"match": "GET /a", "gate": {"action": "a.nope", "mode": "R"}}, "not an action of the role matrix"),
        ({"match": "GET /a", "gate": {"action": "a.view-organization-dashboard", "mode": "Z"}}, "not one of X, S, A, R"),
        ({"match": "GET /a", "gate": {"action": "a.invite-operator-or-viewer", "mode": "A"}}, "could never allow"),
        ({"match": "GET /a", "exempt": "WHATEVER", "why": "x"}, "exemption kind"),
        ({"match": "GET /a", "exempt": "PROTOCOL", "why": " "}, "says why"),
        ({"match": "GET /a"}, "gates or exempts"),
        ({"match": "GET /a", "gate": {"action": "a.view-organization-dashboard", "mode": "R"}, "exempt": "PROTOCOL", "why": "x"}, "gates or exempts"),
        ({"match": "nonsense", "exempt": "PROTOCOL", "why": "x"}, "METHOD /path"),
    ],
)
def test_a_map_that_could_say_less_than_it_appears_to_does_not_load(rule: dict[str, Any], message: str) -> None:
    with pytest.raises(rm.RouteMapError, match=message):
        _rules(rule)


def test_a_rule_listed_twice_and_an_empty_map_are_refused() -> None:
    one = {"match": "GET /a", "exempt": "PROTOCOL", "why": "x"}
    with pytest.raises(rm.RouteMapError, match="listed twice"):
        _rules(one, one)
    with pytest.raises(rm.RouteMapError, match="empty"):
        rm.parse({"rules": []}, MATRIX)


def test_a_reserved_row_may_be_mapped_and_refuses_everyone() -> None:
    (rule,) = _rules({"match": "POST /a", "gate": {"action": "f.release-legal-hold-override-retention", "mode": "X"}})
    assert rule.gates


# ------------------------------------------------------------------------------------------------------------ the shipped map


def test_the_shipped_map_loads_and_every_gate_can_allow_some_role() -> None:
    rm.route_map.cache_clear()
    rules = rm.route_map().rules
    assert len(rules) > 100
    for r in rules:
        assert r.gates != bool(r.exempt)
        if r.gates:
            assert r.action and MATRIX.row(r.action)


def test_the_default_is_report_only_and_only_an_explicit_enforced_enforces() -> None:
    rm.route_matrix_enforced.cache_clear()
    assert rm.route_matrix_enforced() is False  # the shipped setting
    rm.route_matrix_enforced.cache_clear()


def test_a_few_decisions_the_owners_should_see() -> None:
    m = rm.route_map()
    assert m.classify("POST", "/v1/evidence/legal-holds/{hold_id}:release").action == "f.release-legal-hold-override-retention"
    assert m.classify("POST", "/v1/machine/agents/{agent_id}:activate").mode is Mode.APPROVE
    assert m.classify("PUT", "/v1/settings/{area}").action == "c.propose-changes-to-sso-mfa-enforcement"
    assert m.classify("GET", "/v1/privacy/admin/purposes").exempt == "NO_MATRIX_ACTION"
    assert m.classify("POST", "/v1/billing/commitment-requests").exempt == "COMMERCIAL"
    assert m.classify("POST", "/v1/not-a-route") is None


# ------------------------------------------------------------------------------------------------------------------ coverage


def _contexts(app: Any) -> Any:
    for r in app.routes:
        if type(r).__name__ == "_IncludedRouter":
            yield from r.effective_route_contexts()
        elif getattr(r, "methods", None) and getattr(r, "dependant", None) is not None:
            yield r


def _calls(dependant: Any) -> Any:
    for d in dependant.dependencies:
        yield d.call
        yield from _calls(d)


def test_every_authenticated_route_is_gated_inline_mapped_or_exempt_with_a_reason() -> None:
    app = create_app(
        Settings(
            database_url="postgresql+asyncpg://x:y@localhost:1/z",
            token_issuer="http://i",
            token_audience="http://a",
            public_base_url="http://a",
            environment="test",
            cell_id="uk-1",
            role="cell",
        )
    )
    rm.route_map.cache_clear()
    rules = rm.route_map()
    seen = 0
    unclassified: list[str] = []
    for c in _contexts(app):
        calls = list(_calls(c.dependant))
        if authenticated_caller not in calls:
            continue  # public, or authenticated by its own credential (SCIM token, webhook signature, operator capability)
        gated_inline = any(hasattr(x, "matrix_action") for x in calls)
        for method in sorted(c.methods - {"HEAD", "OPTIONS"}):
            seen += 1
            if not gated_inline and rules.classify(method, c.path_format) is None:
                unclassified.append(f"{method} {c.path_format}")
    assert seen > 300, "the coverage test found almost no routes: it is not looking at the cell"
    assert not unclassified, "routes with no matrix decision:\n" + "\n".join(unclassified)


def test_the_guard_is_wired_into_the_authentication_step_of_a_cell() -> None:
    app = create_app(
        Settings(
            database_url="postgresql+asyncpg://x:y@localhost:1/z",
            token_issuer="http://i",
            token_audience="http://a",
            public_base_url="http://a",
            environment="test",
            cell_id="uk-1",
            role="cell",
        )
    )
    assert app.state.route_guard is not None


# ---------------------------------------------------------------------------------------------------------------------- the guard


class _Route:
    def __init__(self, path_format: str) -> None:
        self.path_format = path_format


class _Ctx:
    def __init__(self, tenant: str | None = "t1", principal: str | None = "p1") -> None:
        self.tenant_id, self.principal_id = tenant, principal


class _Caller:
    def __init__(self, **kw: Any) -> None:
        self.ctx = _Ctx(**kw)


class _Request:
    def __init__(self, method: str, path_format: str) -> None:
        self.method = method
        self.scope = {"route": _Route(path_format)}

        class _Url:
            path = path_format

        self.url = _Url()


class _Tx:
    async def __aenter__(self) -> object:
        return object()

    async def __aexit__(self, *a: object) -> None:
        return None


def _guard(monkeypatch: pytest.MonkeyPatch, *, enforced: bool, outcome: Any) -> tuple[Any, list[tuple[str, str]]]:
    """The real guard with the database replaced: `outcome` is a Decision, or an exception to raise."""
    from zid_platform.modules.tenant.api import gate

    calls: list[tuple[str, str]] = []

    async def evaluate(conn: object, tenant: str, actor: str, action: str, mode: Mode, **kw: Any) -> Any:
        calls.append((action, mode.value))
        if isinstance(outcome, Exception):
            raise outcome
        return outcome

    monkeypatch.setattr(gate.access, "evaluate", evaluate)
    monkeypatch.setattr(gate, "tenant_transaction", lambda *a, **k: _Tx())
    monkeypatch.setattr(gate, "request_engine", lambda r: object())
    monkeypatch.setattr(gate.routes, "route_matrix_enforced", lambda: enforced)
    return gate.route_matrix_guard, calls


def _decision(allowed: bool) -> Any:
    from zid_platform.modules.tenant.domain.role_matrix import Decision, Outcome

    return Decision(Outcome.ALLOW if allowed else Outcome.DENY, () if allowed else ("ROLE_DOES_NOT_HOLD_PERMISSION",), "x", "R", "OWNER" if allowed else None, "h")


def test_report_only_records_what_would_be_refused_and_lets_the_request_through(monkeypatch: pytest.MonkeyPatch) -> None:
    guard, calls = _guard(monkeypatch, enforced=False, outcome=_decision(False))
    asyncio.run(guard(_Request("GET", "/v1/evidence/receipts"), _Caller()))  # no exception
    assert calls == [("f.view-authorized-audit-and-ledger-entries", "R")]


def test_enforced_refuses_a_role_the_matrix_does_not_allow(monkeypatch: pytest.MonkeyPatch) -> None:
    from zid_errors import ZidError

    guard, _ = _guard(monkeypatch, enforced=True, outcome=_decision(False))
    with pytest.raises(ZidError) as e:
        asyncio.run(guard(_Request("GET", "/v1/evidence/receipts"), _Caller()))
    assert e.value.http_status == 403 and e.value.code == "ROLE_NOT_PERMITTED"


def test_an_allowed_decision_passes_in_both_modes(monkeypatch: pytest.MonkeyPatch) -> None:
    for enforced in (True, False):
        guard, calls = _guard(monkeypatch, enforced=enforced, outcome=_decision(True))
        asyncio.run(guard(_Request("PUT", "/v1/settings/{area}"), _Caller()))
        assert calls == [("c.propose-changes-to-sso-mfa-enforcement", "S")]


def test_a_failure_to_decide_is_a_refusal_when_enforced_and_a_log_line_when_not(monkeypatch: pytest.MonkeyPatch) -> None:
    from zid_errors import ZidError

    guard, _ = _guard(monkeypatch, enforced=False, outcome=RuntimeError("db down"))
    asyncio.run(guard(_Request("GET", "/v1/evidence/receipts"), _Caller()))
    guard, _ = _guard(monkeypatch, enforced=True, outcome=RuntimeError("db down"))
    with pytest.raises(ZidError) as e:
        asyncio.run(guard(_Request("GET", "/v1/evidence/receipts"), _Caller()))
    assert e.value.http_status == 503 and e.value.code == "ROLE_DECISION_UNAVAILABLE"


def test_exempt_unmapped_and_non_member_calls_are_not_judged(monkeypatch: pytest.MonkeyPatch) -> None:
    guard, calls = _guard(monkeypatch, enforced=True, outcome=_decision(False))
    asyncio.run(guard(_Request("GET", "/v1/billing/overview"), _Caller()))  # exempt: the billing layer decides
    asyncio.run(guard(_Request("GET", "/v1/not-a-route"), _Caller()))  # unmapped: the coverage test keeps this list empty
    asyncio.run(guard(_Request("GET", "/v1/evidence/receipts"), _Caller(principal=None)))  # no member
    asyncio.run(guard(_Request("GET", "/v1/evidence/receipts"), _Caller(tenant=None)))
    assert calls == []
