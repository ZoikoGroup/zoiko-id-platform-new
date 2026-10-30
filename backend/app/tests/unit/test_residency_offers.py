"""Residency offers and the home-cell readiness gate (ZID-EDR-002 acceptance tests): Regional is the default
paid offer, Sovereign and Dedicated are contract-gated, and a cell that has not passed its gate holds the segment."""

from __future__ import annotations

import ast
import uuid
from pathlib import Path
from types import SimpleNamespace
from typing import Any

import pytest
from fastapi import FastAPI, Request
from fastapi.responses import JSONResponse
from fastapi.testclient import TestClient
from zid_errors import ZidError
from zid_platform.api_auth import authenticated_caller
from zid_platform.modules.tenant.api import residency as residency_api
from zid_platform.modules.tenant.api.residency import router
from zid_platform.modules.tenant.domain.offers import (
    GATES,
    Offers,
    cell_state,
    offer_for_profile,
    public_view,
    require_cell_ready,
)
from zid_platform.modules.tenant.domain.residency import PROFILES, Residency, ResidencyError
from zid_platform.modules.tenant.infrastructure.catalog import catalog
from zid_platform.modules.tenant.infrastructure.offers import offers, parse

BACKEND = Path(__file__).resolve().parents[3]
GATED = {g: f"ref-{g}" for g in GATES}


def _offers(readiness: dict[str, object]) -> Offers:
    offers_doc = {
        "version": "t",
        "offers": {
            "SHARED_GLOBAL": {
                "profile": "SRP-0",
                "availability": "NOT_A_SENSITIVE_TIER",
                "sensitive_data": False,
            },
            "REGIONAL": {
                "profile": "SRP-1",
                "availability": "STANDARD_AFTER_GATE",
                "sensitive_data": True,
            },
            "SOVEREIGN": {"profile": "SRP-2", "availability": "CONTRACT_GATED"},
            "DEDICATED": {"profile": "SRP-3", "availability": "CONTRACT_GATED"},
        },
        "home_regions": {"UK": {"cell_id": "uk-1", "wave": 1}},
    }
    return parse(offers_doc, {"version": "t", "cells": readiness})


def test_the_four_offers_map_onto_the_existing_profiles_and_relabel_nothing() -> None:
    o = offers()
    assert {c: x.profile for c, x in o.offers.items()} == {
        "SHARED_GLOBAL": "SRP-0",
        "REGIONAL": "SRP-1",
        "SOVEREIGN": "SRP-2",
        "DEDICATED": "SRP-3",
    }
    assert o.offers["REGIONAL"].availability == "STANDARD_AFTER_GATE"
    assert o.offers["SOVEREIGN"].availability == "CONTRACT_GATED"
    assert o.offers["DEDICATED"].availability == "CONTRACT_GATED"
    assert o.offers["SHARED_GLOBAL"].sensitive_data is False


def test_the_priority_home_regions_are_us_eea_and_uk() -> None:
    assert {r: h.cell_id for r, h in offers().home_regions.items()} == {
        "US": "us-1",
        "EEA": "eu-1",
        "UK": "uk-1",
    }


def test_a_cell_that_has_not_passed_its_gate_holds_the_segment_in_a_deployed_environment() -> None:
    o = offers()
    for cell in ("uk-1", "eu-1", "local-1", "us-1", "not-a-cell"):
        with pytest.raises(ResidencyError) as caught:
            require_cell_ready(o, "SRP-1", cell, environment="production")
        # a refusal that names the cell, and moves the tenant nowhere
        assert caught.value.code == "CELL_NOT_READY" and cell in str(caught.value)


def test_a_sandbox_cell_counts_in_local_and_test_only_and_an_undeployed_cell_never() -> None:
    o = offers()
    for env in ("local", "test"):
        assert require_cell_ready(o, "SRP-1", "uk-1", environment=env) == "SANDBOX"
        with pytest.raises(ResidencyError):
            require_cell_ready(o, "SRP-1", "us-1", environment=env)  # not deployed anywhere
    assert cell_state(o, "not-a-cell", environment="local") == "HELD"


def test_shared_global_takes_no_sensitive_data_and_needs_no_cell_gate() -> None:
    assert (
        require_cell_ready(offers(), "SRP-0", "us-1", environment="production") == "NOT_APPLICABLE"
    )


def test_ready_needs_all_five_gates() -> None:
    for missing in GATES:
        gates = {g: r for g, r in GATED.items() if g != missing}
        with pytest.raises(ResidencyError) as caught:
            _offers({"uk-1": {"status": "READY", "gates": gates}})
        assert caught.value.code == "CELL_GATES_MISSING" and missing in str(caught.value)
    o = _offers({"uk-1": {"status": "READY", "gates": GATED}})
    assert require_cell_ready(o, "SRP-1", "uk-1", environment="production") == "READY"


def test_an_unknown_status_or_profile_is_refused_at_load() -> None:
    with pytest.raises(ResidencyError):
        _offers({"uk-1": {"status": "LIVE"}})
    with pytest.raises(ResidencyError):
        parse({"offers": {"X": {"profile": "SRP-9", "availability": "CONTRACT_GATED"}}}, {})


def test_isolation_mode_is_dedicated_only_for_srp_3() -> None:
    def residency(profile: str) -> Residency:
        return Residency("t", profile, "uk-1", (), (), (), "PLATFORM_MANAGED", "p", 1, 1)

    assert [residency(p).isolation_mode for p in PROFILES] == [
        "SHARED",
        "SHARED",
        "SHARED",
        "DEDICATED",
    ]


def test_the_shipped_cells_are_the_catalogue_cells() -> None:
    cat = catalog()
    for cell_id, readiness in offers().readiness.items():
        assert cell_id in cat.cells, cell_id
        if readiness.status == "READY":
            assert all(readiness.gates.get(g) for g in GATES)
    for region in offers().home_regions.values():  # every priority home cell is catalogued
        assert region.cell_id in cat.cells and not cat.cells[region.cell_id].recovery_only
    # the U.S. cell is a catalogued target that nobody can be placed on until its gates are recorded
    assert offers().readiness["us-1"].status == "NOT_READY"
    us = cat.cells["us-1"]
    assert (us.country, us.legal_zone, us.approved_recovery_cells) == ("US", "US", ("us-1-dr",))
    assert cat.cells["us-1-dr"].recovery_only and cat.cells["us-1-dr"].legal_zone == "US"


def test_the_public_view_shows_only_what_may_be_said() -> None:
    rows = {r["offer"]: r for r in public_view(offers(), environment="production")}
    assert set(rows) == {"SHARED_GLOBAL", "REGIONAL", "SOVEREIGN", "DEDICATED"}
    assert {x["state"] for x in rows["REGIONAL"]["regions"]} == {"HELD"}  # nothing has passed
    assert "regions" not in rows["SOVEREIGN"] and "regions" not in rows["DEDICATED"]
    text = repr(rows)
    assert "ref-" not in text and "gates" not in text and "readiness" not in text
    local = {
        r["region"]: r["state"]
        for r in next(
            x for x in public_view(offers(), environment="local") if x["offer"] == "REGIONAL"
        )["regions"]
    }
    assert local == {"US": "HELD", "EEA": "SANDBOX", "UK": "SANDBOX"}
    assert offer_for_profile(offers(), "SRP-1").code == "REGIONAL"


def test_the_offers_are_public_over_http() -> None:
    app = FastAPI()
    app.include_router(router)
    app.state.environment = "production"
    body = TestClient(app).get("/v1/residency-offers").json()
    assert [o["offer"] for o in body["offers"]] == [
        "SHARED_GLOBAL",
        "REGIONAL",
        "SOVEREIGN",
        "DEDICATED",
    ]
    assert body["offers"][1]["customer_promise"].startswith("Choose an available hosting region")


def test_placing_a_tenant_asks_the_readiness_gate() -> None:
    path = BACKEND / "app/src/zid_platform/modules/tenant/api/residency.py"
    text = path.read_text(encoding="utf-8")
    for node in ast.walk(ast.parse(text)):
        if isinstance(node, ast.AsyncFunctionDef) and node.name == "put_residency":
            assert "require_cell_ready" in (ast.get_source_segment(text, node) or "")
            return
    raise AssertionError("put_residency not found")


# ZID-EDR-002 section 7: the claims Sales must not make are never in the product or its configuration
SCANNED = (
    ("app/src", {".py"}),
    ("config", {".yaml", ".yml", ".json", ".md"}),
    ("../frontend/apps", {".ts", ".tsx", ".md", ".mdx", ".json"}),
    ("../frontend/packages", {".ts", ".tsx", ".md", ".mdx", ".json"}),
)
EXEMPT = {"residency-offers.yaml", "test_residency_offers.py"}  # list the claims, to forbid them


def test_no_forbidden_residency_claim_appears_in_the_product() -> None:
    claims = [c.lower() for c in offers().forbidden_claims]
    assert claims  # the list itself must exist
    found: list[str] = []
    scanned = 0
    for root, suffixes in SCANNED:
        base = (BACKEND / root).resolve()
        if not base.exists():
            continue
        for path in base.rglob("*"):
            if (
                not path.is_file()
                or path.suffix not in suffixes
                or path.name in EXEMPT
                or {"node_modules", ".next", "dist", "__pycache__"} & set(path.parts)
            ):
                continue
            scanned += 1
            text = path.read_text(encoding="utf-8", errors="ignore").lower()
            found += [f"{path.name}: {c}" for c in claims if c in text]
    assert scanned > 200, f"the scan looked at only {scanned} files"  # it really read the product
    assert not found, found


# ---- the placement change over HTTP (EDR-002 section 2: a cell that fails readiness holds the segment) -----------


def _placement_client(
    monkeypatch: pytest.MonkeyPatch, environment: str
) -> tuple[TestClient, dict[str, Any]]:
    """The real route and gate, with the caller, the second-admin approval and the store stood in for."""
    seen: dict[str, Any] = {"approvals": 0, "placed": []}

    async def session(request: Request, caller: object, fn: Any) -> Any:
        return await fn(None)

    async def approval(*args: object, **kwargs: object) -> None:
        seen["approvals"] += 1

    async def set_residency(conn: object, tenant_id: str, **kwargs: Any) -> Residency:
        seen["placed"].append((kwargs["profile"], kwargs["home_cell_id"]))
        return Residency(
            tenant_id,
            kwargs["profile"],
            kwargs["home_cell_id"],
            (),
            (),
            (),
            "PLATFORM_MANAGED",
            "p",
            1,
            1,
        )

    monkeypatch.setattr(residency_api, "_session", session)
    monkeypatch.setattr(residency_api, "approved_by_second_admin", approval)
    monkeypatch.setattr(residency_api.service, "set_residency", set_residency)
    app = FastAPI()

    @app.exception_handler(ZidError)
    async def _zid(_: Request, e: ZidError) -> JSONResponse:
        return JSONResponse({"error": {"code": e.code}}, status_code=e.http_status)

    app.include_router(router)
    app.state.environment = environment
    app.dependency_overrides[authenticated_caller] = lambda: SimpleNamespace(
        ctx=SimpleNamespace(tenant_id="t1", principal_id="p1")
    )
    return TestClient(app), seen


def _put(http: TestClient, profile: str, cell: str) -> Any:
    return http.put(
        "/v1/tenant/residency",
        json={
            "residency_profile_id": profile,
            "home_cell_id": cell,
            "jurisdiction_pack_id": "eu-gdpr",
            "approval_ref": "legal-123",
            "approval_request_id": str(uuid.uuid4()),
        },
    )


def test_a_regional_placement_on_a_cell_that_has_not_passed_is_refused_over_http(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    http, seen = _placement_client(monkeypatch, "production")
    for cell in ("uk-1", "eu-1", "us-1"):
        r = _put(http, "SRP-1", cell)
        assert r.status_code == 409 and r.json()["error"]["code"] == "CELL_NOT_READY", cell
    # nothing was placed and no approval was spent: the second administrator's approval stays usable
    assert seen == {"approvals": 0, "placed": []}


def test_the_same_placement_works_where_the_cell_is_a_sandbox_and_the_us_cell_still_waits(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    http, seen = _placement_client(monkeypatch, "local")
    assert _put(http, "SRP-1", "uk-1").status_code == 200
    assert _put(http, "SRP-1", "us-1").status_code == 409  # a target, not a deployed cell
    assert seen["placed"] == [("SRP-1", "uk-1")]


def test_shared_global_placement_needs_no_ready_cell(monkeypatch: pytest.MonkeyPatch) -> None:
    http, seen = _placement_client(monkeypatch, "production")
    r = _put(http, "SRP-0", "uk-1")
    assert r.status_code == 200 and r.json()["residency"]["isolation_mode"] == "SHARED"
    assert seen["placed"] == [("SRP-0", "uk-1")]
