"""Country capability registry (ZID-EDR-001 acceptance tests): fail closed, per country, per capability."""

from __future__ import annotations

from datetime import UTC, datetime

import pytest
from zid_platform.modules.verification.domain.countries import (
    APPROVALS,
    Availability,
    Capability,
    CountryError,
    availability,
    public_view,
    require,
)
from zid_platform.modules.verification.infrastructure.country_registry import parse, registry

NOW = datetime(2026, 11, 1, tzinfo=UTC)
APPROVED = {a: f"ref-{a}" for a in APPROVALS}


def _doc(**gb_caps: object) -> dict[str, object]:
    return {
        "version": "t",
        "countries": {
            "GB": {"name": "United Kingdom", "wave": 1, "capabilities": gb_caps},
            "DE": {
                "name": "Germany",
                "wave": 1,
                "capabilities": {
                    "INDIVIDUAL_VERIFICATION": {
                        "status": "ENABLED",
                        "effective_from": "2026-10-01",
                        "approvals": APPROVED,
                    }
                },
            },
        },
    }


def test_unlisted_country_and_unlisted_capability_are_disabled() -> None:
    reg = parse(_doc())
    for country in ("ZZ", "FR", "", "gb1", "G"):
        assert (
            availability(
                reg,
                country,
                Capability.INDIVIDUAL_VERIFICATION,
                now=NOW,
                environment="production",
            )
            is Availability.DISABLED
        )
    # GB is listed but names no capability: still disabled
    assert (
        availability(reg, "GB", Capability.REGISTRATION, now=NOW, environment="production")
        is Availability.DISABLED
    )


def test_unsupported_country_is_refused_never_verified() -> None:
    with pytest.raises(CountryError) as e:
        require(
            parse(_doc()),
            "NG",
            Capability.BUSINESS_VERIFICATION,
            now=NOW,
            environment="local",
        )
    assert e.value.code == "COUNTRY_CAPABILITY_UNAVAILABLE"


def test_enabled_regulated_capability_needs_all_six_approvals_and_a_date() -> None:
    for missing in APPROVALS:
        partial = {k: v for k, v in APPROVED.items() if k != missing}
        with pytest.raises(CountryError) as e:
            parse(
                _doc(
                    REGISTRATION={
                        "status": "ENABLED",
                        "effective_from": "2026-10-01",
                        "approvals": partial,
                    }
                )
            )
        assert e.value.code == "COUNTRY_APPROVALS_MISSING" and missing in str(e.value)
    with pytest.raises(CountryError):
        parse(_doc(REGISTRATION={"status": "ENABLED", "approvals": APPROVED}))


def test_discovery_is_open_without_approvals() -> None:
    reg = parse(_doc(DISCOVERY={"status": "ENABLED", "effective_from": "2026-10-01"}))
    assert (
        availability(reg, "GB", Capability.DISCOVERY, now=NOW, environment="production")
        is Availability.ENABLED
    )


def test_not_yet_effective_is_disabled() -> None:
    reg = parse(_doc())
    before = datetime(2026, 9, 30, tzinfo=UTC)
    assert (
        availability(
            reg, "DE", Capability.INDIVIDUAL_VERIFICATION, now=before, environment="production"
        )
        is Availability.DISABLED
    )
    assert (
        availability(
            reg, "DE", Capability.INDIVIDUAL_VERIFICATION, now=NOW, environment="production"
        )
        is Availability.ENABLED
    )


def test_sandbox_counts_only_in_local_and_test() -> None:
    reg = parse(_doc(REGISTRATION={"status": "SANDBOX", "provider": "simulator"}))
    for env, want in (
        ("local", Availability.SANDBOX),
        ("test", Availability.SANDBOX),
        ("production", Availability.DISABLED),
        ("staging", Availability.DISABLED),
        ("", Availability.DISABLED),
    ):
        assert availability(reg, "GB", Capability.REGISTRATION, now=NOW, environment=env) is want, (
            env
        )


def test_one_country_can_be_disabled_without_touching_another() -> None:
    both = parse(_doc())
    only_de = parse({"version": "t", "countries": {"DE": _doc()["countries"]["DE"]}})  # type: ignore[index]
    cap = Capability.INDIVIDUAL_VERIFICATION
    assert availability(only_de, "DE", cap, now=NOW, environment="production") is availability(
        both, "DE", cap, now=NOW, environment="production"
    )
    assert (
        availability(only_de, "GB", cap, now=NOW, environment="production") is Availability.DISABLED
    )


def test_unknown_capability_or_status_in_the_file_is_refused() -> None:
    with pytest.raises(CountryError):
        parse(_doc(NOT_A_CAPABILITY={"status": "ENABLED"}))
    with pytest.raises(CountryError):
        parse(_doc(REGISTRATION={"status": "MAYBE"}))


def test_shipped_registry_lists_the_edr001_waves_and_claims_no_production_proofing() -> None:
    reg = registry()
    waves: dict[int, set[str]] = {}
    for code, c in reg.countries.items():
        waves.setdefault(c.wave or 0, set()).add(code)
    assert waves[1] == {"US", "GB", "IE", "DE", "FR"}
    assert waves[2] == {"CA", "IN", "AU", "SG"}
    assert waves[3] == {"AE", "SA", "JP", "BR", "MX"}
    assert waves[4] == {"ZA", "NG", "KE"}
    for code in reg.countries:
        for cap in Capability:
            if cap is Capability.DISCOVERY:
                continue
            assert (
                availability(reg, code, cap, now=NOW, environment="production")
                is Availability.DISABLED
            ), f"{code}/{cap.value} must stay disabled until the six approvals are recorded"


def test_public_view_names_no_provider_or_approval() -> None:
    rows = public_view(parse(_doc()), now=NOW, environment="local")
    assert {r["country"] for r in rows} == {"GB", "DE"}
    text = repr(rows)
    assert "simulator" not in text and "approvals" not in text and "provider" not in text
    assert all(set(r["capabilities"]) == {c.value for c in Capability} for r in rows)


# ---- entry points (EDR-001 §3: country permissions at every API, job, webhook and admin action) ----------------

import ast
import asyncio
from pathlib import Path

from fastapi import FastAPI, Request
from fastapi.responses import JSONResponse
from fastapi.testclient import TestClient
from zid_errors import ZidError
from zid_platform.modules.onboarding.api.signup import router as signup_router

SRC = Path(__file__).resolve().parents[2] / "src" / "zid_platform"
# every route or callback that starts a regulated capability, and the capability it must ask about
GATED_ENTRY_POINTS = {
    "modules/onboarding/api/signup.py": {
        "signup": ("REGISTRATION", "BUSINESS_VERIFICATION"),
        "registry_lookup": ("BUSINESS_VERIFICATION",),
    },
    "modules/onboarding/api/onboarding.py": {
        "organization": ("BUSINESS_VERIFICATION",),
        "_approved_move": ("ENTERPRISE_USE",),
        "activate_stage": ("ENTERPRISE_USE",),
    },
    "modules/credential/api/credentials.py": {
        "issue_credential": ("CREDENTIAL_ISSUANCE",),
        "activate_configuration": ("CREDENTIAL_ISSUANCE",),
    },
    "modules/verification/api/verification.py": {
        "start": ("_country_gate",),
        "reverify": ("_country_gate",),
        "callback": ("_callback_country_check",),
    },
}


def _function_source(path: str, name: str) -> str:
    text = (SRC / path).read_text(encoding="utf-8")
    for node in ast.walk(ast.parse(text)):
        if isinstance(node, ast.AsyncFunctionDef | ast.FunctionDef) and node.name == name:
            return ast.get_source_segment(text, node) or ""
    raise AssertionError(f"{name} not found in {path}")


def test_every_regulated_entry_point_asks_the_registry():
    for path, functions in GATED_ENTRY_POINTS.items():
        for name, needles in functions.items():
            body = _function_source(path, name)
            for needle in needles:
                assert needle in body, (
                    f"{path}::{name} does not ask the country registry ({needle})"
                )


def _signup_app(environment: str) -> TestClient:
    app = FastAPI()

    @app.exception_handler(ZidError)
    async def _zid(_: Request, e: ZidError) -> JSONResponse:
        return JSONResponse({"error": {"code": e.code}}, status_code=e.http_status)

    app.include_router(signup_router)
    app.state.environment = environment
    app.state.tenant_signup = True
    app.state.tenant_signup_notice_version = "v1"
    app.state.tenant_console = ("http://console.invalid", "token")
    app.state.home_cell = "local-1"
    return TestClient(app)


def test_signup_and_company_lookup_are_refused_where_the_country_is_not_enabled():
    # GB is only SANDBOX in the registry, and a sandbox never counts in a deployed environment
    http = _signup_app("production")
    r = http.get("/v1/signup/registry/GB-COH/00000006")
    assert r.status_code == 422 and r.json()["error"]["code"] == "COUNTRY_CAPABILITY_UNAVAILABLE"
    r = http.post(
        "/v1/signup",
        json={
            "company_name": "Acme Ltd",
            "workspace": "acme",
            "notice_version": "v1",
            "registration": {"namespace": "GB-COH", "identifier": "00000006"},
        },
    )
    assert r.status_code == 422 and r.json()["error"]["code"] == "COUNTRY_CAPABILITY_UNAVAILABLE"


class _Declared:
    def __init__(self, namespace: str) -> None:
        self.register_namespace = namespace


def _request(environment: str) -> Request:
    app = FastAPI()
    app.state.environment = environment
    return Request({"type": "http", "app": app, "headers": [], "method": "GET", "path": "/"})


@pytest.mark.parametrize(
    ("environment", "declared", "outcome"),
    [
        ("production", None, "COUNTRY_NOT_DECLARED"),  # a deployed environment never acts blind
        ("production", "GB-COH", "COUNTRY_CAPABILITY_UNAVAILABLE"),  # GB is sandbox only
        ("local", None, None),  # local and test build tenants directly, with no register
        ("local", "GB-COH", None),  # sandbox counts there
    ],
)
def test_a_tenants_own_action_is_gated_by_the_country_of_its_declared_register(
    monkeypatch: pytest.MonkeyPatch, environment: str, declared: str | None, outcome: str | None
) -> None:
    from zid_platform import country_gate

    async def load(conn: object, tenant_id: str) -> _Declared | None:
        return _Declared(declared) if declared else None

    monkeypatch.setattr(country_gate, "declared_registration", load)
    request = _request(environment)

    def enforce(capability: Capability) -> None:
        asyncio.run(country_gate.enforce_tenant(request, object(), "t1", capability))  # type: ignore[arg-type]

    for capability in (Capability.CREDENTIAL_ISSUANCE, Capability.ENTERPRISE_USE):
        if outcome is None:
            enforce(capability)
        else:
            with pytest.raises(ZidError) as caught:
                enforce(capability)
            assert caught.value.code == outcome


# ---- Unicode, local formats and accessible errors (EDR-001 §5) ------------------------------------------------


def test_unicode_country_names_survive_the_registry_and_the_public_view() -> None:
    doc = {
        "version": "t",
        "countries": {
            "CI": {"name": "Côte d’Ivoire", "wave": 4, "capabilities": {}},
            "JP": {"name": "日本", "wave": 3, "capabilities": {}},
            "AE": {"name": "الإمارات العربية المتحدة", "wave": 3, "capabilities": {}},
        },
    }
    rows = {
        r["country"]: r["name"] for r in public_view(parse(doc), now=NOW, environment="production")
    }
    assert rows == {"CI": "Côte d’Ivoire", "JP": "日本", "AE": "الإمارات العربية المتحدة"}


def test_a_country_is_the_declared_iso_code_never_a_guess_from_formatting() -> None:
    reg = parse(_doc(INDIVIDUAL_VERIFICATION={"status": "SANDBOX"}))
    sandbox = {"now": NOW, "environment": "local"}
    # case is normalised, but nothing else is: a phone prefix, a name or a 3-letter code is not a country
    assert (
        availability(reg, "gb", Capability.INDIVIDUAL_VERIFICATION, **sandbox)
        is Availability.SANDBOX
    )
    for not_a_code in ("+44", "GBR", "G", "", "United Kingdom", "G B", "GB "):
        assert (
            availability(reg, not_a_code, Capability.INDIVIDUAL_VERIFICATION, **sandbox)
            is Availability.DISABLED
        ), not_a_code


def test_a_refusal_is_readable_and_names_no_internal_state() -> None:
    reg = parse(_doc())
    with pytest.raises(CountryError) as caught:
        require(reg, "GB", Capability.BUSINESS_VERIFICATION, now=NOW, environment="production")
    message = str(caught.value)
    assert "GB" in message and "BUSINESS_VERIFICATION" in message
    assert "approval" not in message.lower() and "provider" not in message.lower()
