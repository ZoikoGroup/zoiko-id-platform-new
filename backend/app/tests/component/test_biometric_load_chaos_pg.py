"""Load and chaos for the biometric gates (ZID-DTV-WF-04 QA-042; CR-113), on PostgreSQL.

1. Load: many eligibility answers at once, over a real connection pool, for several countries and purposes. Every answer fails closed (nothing is
   approved), answers for the same inputs agree (same policy hash), and no answer is lost or mixed with another tenant's.
2. Chaos: a gate that times out or fails part-way (the model inventory, the DPIA store) never turns into an available method. Over HTTP the caller
   gets a plain error with no personal data, no stack trace and no internal message, and nothing sensitive reaches the logs.
3. Chaos under load: some calls fail while others run; the others are unaffected, and every event written meanwhile is codes and hashes only.
"""

from __future__ import annotations

import asyncio
import json
import logging
import re
import uuid
from datetime import UTC, datetime
from typing import Any

import pytest
from conftest import ctx, engine
from onboarding_fixture import onboard
from sqlalchemy import text
from test_v2_privacy_pg import ACTIONS, RESOURCES, api_for, runtime  # noqa: F401  (runtime is an autouse fixture)
from zid_db import tenant_transaction
from zid_platform.modules.verification.application import biometric_eligibility as be
from zid_platform.modules.verification.application import privacy_gate
from zid_platform.modules.verification.domain import privacy_overlay as po

MARKER = "jane.doe@example.com"  # what a careless fault message might carry; it must never reach a caller, a log line or an event
SUBJECT = "4f1c2b8e-0000-4000-8000-00000000beef"
PROHIBITED = re.compile(
    r"(?i)(^|_)(e?mail|name|phone|address|passport|biometric|face|selfie|image|photo|media|template|embedding|raw|token|secret)(_|$)"
)


def _overlay(country: str, **kw: Any) -> po.Overlay:
    o = po.Overlay(
        country,
        1,
        frozenset({"EU_WEST"}),
        {},
        kw.get("provider_regions", {}),
        po.Biometrics.ALLOWED,
    )
    d = po.overlay_hash(o)
    return po.Overlay(
        **{**o.__dict__, "approvals": {r: po.Approval(f"{r}-sig", d) for r in po.APPROVER_ROLES}}
    )


OVERLAYS = {c: _overlay(c) for c in ("GB", "FR", "DE")}


async def _one(
    eng: Any, tenant: str, country: str, purpose: str, provider: str | None
) -> be.Eligibility:
    async with tenant_transaction(eng, ctx(tenant)) as c:
        return await be.eligibility(
            c,
            tenant_id=tenant,
            country=country,
            purpose=purpose,
            overlays=OVERLAYS,
            now=datetime.now(UTC),
            provider=provider,
        )


# --------------------------------------------------------------------------------------------------------------------- 1. load


def test_many_answers_at_once_fail_closed_agree_and_stay_with_their_tenant(rt_url: str) -> None:
    a = onboard(rt_url, extra_actions=ACTIONS, extra_resources=RESOURCES)
    b = onboard(rt_url, extra_actions=ACTIONS, extra_resources=RESOURCES)
    calls = [
        (t, country, purpose, provider)
        for t in (a.tenant, b.tenant)
        for country in ("GB", "FR", "DE")
        for purpose in ("IDENTITY_VERIFICATION", "AGE_ASSURANCE")
        for provider in ("veriff", None)
        for _ in range(3)
    ]  # 72 calls

    async def go() -> list[be.Eligibility]:
        async with engine(rt_url) as eng:
            return await asyncio.gather(*(_one(eng, *c) for c in calls))

    started = datetime.now(UTC)
    answers = asyncio.run(go())
    assert len(answers) == len(calls)
    assert (datetime.now(UTC) - started).total_seconds() < 120
    by_input: dict[tuple[Any, ...], set[str]] = {}
    for call, ans in zip(calls, answers, strict=True):
        assert (
            ans.view()["available_methods"] == []
        )  # nothing approved: every method fails closed under load
        assert ans.country == call[1] and ans.purpose == call[2]
        by_input.setdefault(call, set()).add(ans.policy_hash)
    assert all(len(h) == 1 for h in by_input.values()), (
        "the same inputs gave different answers under load"
    )


# --------------------------------------------------------------------------------------------------------------------- 2. chaos over HTTP


class _Records(logging.Handler):
    """Every log record of every logger, with its exception text. CI runs pytest with `-p no:logging`, which removes the caplog fixture."""

    def __init__(self) -> None:
        super().__init__(logging.DEBUG)
        self.lines: list[str] = []

    def emit(self, record: logging.LogRecord) -> None:
        text = record.getMessage()
        if record.exc_info:
            text += logging.Formatter().formatException(record.exc_info)
        self.lines.append(text)


@pytest.mark.parametrize("fault", ["breach_store_timeout", "use_case_registry_error"])
def test_a_failing_gate_is_a_plain_error_that_leaks_nothing(
    rt_url: str,
    fault: str,
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
) -> None:
    import zid_platform.modules.privacy.ports as pp

    o = onboard(rt_url, extra_actions=ACTIONS, extra_resources=RESOURCES)

    async def timeout(*a: Any, **k: Any) -> Any:
        raise TimeoutError(f"breach store timed out while reading {MARKER} subject {SUBJECT}")

    def broken(*a: Any, **k: Any) -> Any:
        raise RuntimeError(f"use case registry unavailable for {MARKER}")

    if fault == "breach_store_timeout":
        monkeypatch.setattr(pp, "capture_paused_breaches", timeout)
    else:
        monkeypatch.setattr(pp, "use_case_problems", broken)
    records = _Records()
    root = logging.getLogger()
    previous_level = root.level
    root.addHandler(records)
    root.setLevel(logging.DEBUG)
    from fastapi.testclient import TestClient

    app = api_for(rt_url, o, privacy_overlays=OVERLAYS).app
    try:
        with TestClient(app, raise_server_exceptions=False) as api:  # what a real caller sees
            r = api.get(
                "/v1/biometric-methods/eligibility",
                params={"country": "GB"},
                headers=o.bearer(o.owner, scope="verification:read"),
            )
    finally:
        root.removeHandler(records)
        root.setLevel(previous_level)
    assert r.status_code >= 500, r.text
    body = r.text
    assert (
        MARKER not in body
        and SUBJECT not in body
        and "Traceback" not in body
        and "store" not in body
        and "registry" not in body
    )
    assert "available_methods" not in body  # no partial answer that could be read as permission
    logged = "\n".join(records.lines)
    logged += capsys.readouterr().out  # the service's own JSON log lines
    assert MARKER not in logged and SUBJECT not in logged, "a fault message reached the logs"


# --------------------------------------------------------------------------------------------------------------------- 3. chaos under load


def test_faults_during_load_touch_only_their_own_calls_and_events_stay_minimal(
    rt_url: str, owner_url: str, monkeypatch: pytest.MonkeyPatch
) -> None:
    import zid_platform.modules.privacy.ports as pp

    o = onboard(rt_url, extra_actions=ACTIONS, extra_resources=RESOURCES)
    real = pp.active_model_version

    async def flaky(conn: Any, ref: str, method: str) -> str | None:
        if ref == "flaky-provider":
            await asyncio.sleep(0.01)
            raise TimeoutError(f"provider inventory timed out for {MARKER}")
        return await real(conn, ref, method)

    monkeypatch.setattr(pp, "active_model_version", flaky)
    providers = ["veriff", "flaky-provider"] * 20

    async def region(
        eng: Any, i: int
    ) -> None:  # a region refusal written concurrently with the load
        async with tenant_transaction(eng, ctx(o.tenant)) as c:
            await privacy_gate.record_region_blocked(
                c, o.tenant, country="GB", provider=f"p{i}", overlay=_overlay("GB", provider_regions={f"p{i}": "US_EAST"}),
                decision=po.RouteDecision(False, ("CROSS_BORDER_ROUTE_DENIED",)),
            )  # fmt: skip

    async def go() -> list[Any]:
        async with engine(rt_url) as eng:
            return await asyncio.gather(
                *(_one(eng, o.tenant, "GB", "IDENTITY_VERIFICATION", p) for p in providers),
                *(region(eng, i) for i in range(10)),
                return_exceptions=True,
            )

    results = asyncio.run(go())
    calls, writes = results[: len(providers)], results[len(providers) :]
    for p, res in zip(providers, calls, strict=True):
        if p == "flaky-provider":
            assert isinstance(res, TimeoutError)  # the fault is the caller's error, never an answer
        else:
            assert isinstance(res, be.Eligibility) and res.view()["available_methods"] == []
            assert all(m.reasons == ("PROVIDER_MODEL_NOT_ACTIVE",) for m in res.methods)
    assert all(w is None for w in writes)

    async def read(c: Any) -> list[tuple[str, str]]:
        rows = (
            await c.execute(
                text(
                    "SELECT event_type, payload::text AS p FROM events.outbox_event WHERE tenant_id = :t"
                ),
                {"t": uuid.UUID(o.tenant)},
            )
        ).all()
        return [(r.event_type, r.p) for r in rows]

    async def owner_read() -> list[tuple[str, str]]:
        async with engine(owner_url) as eng, eng.begin() as c:
            return await read(c)

    events = asyncio.run(owner_read())
    region_events = [e for e in events if e[0].endswith("biometric.region_blocked.v1")]
    assert len(region_events) == 10
    for name, raw in events:
        assert MARKER not in raw and SUBJECT not in raw, name

        def keys(v: Any) -> list[str]:
            if isinstance(v, dict):
                return [*v.keys(), *(k for x in v.values() for k in keys(x))]
            if isinstance(v, list):
                return [k for x in v for k in keys(x)]
            return []

        payload = json.loads(raw)
        bad = [k for k in keys(payload.get("data", payload)) if PROHIBITED.search(k)]
        assert not bad, f"{name}: {bad}"
