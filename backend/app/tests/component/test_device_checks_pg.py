"""WF-05 device observations over HTTP on PostgreSQL (DTV-22; QA-030, QA-031, QA-032, QA-033, QA-034; CR-116).

A case not cleared for DEVICE_RISK writes nothing. A web observation with only a VPN is PASS with a note. An Android observation answers a nonce; the
verifier's emulator signal makes it REVIEW with a step-up; the same nonce cannot be used again. The raw device reference is never stored.
"""

from __future__ import annotations

import asyncio
import uuid
from datetime import UTC, datetime
from typing import Any

import pytest
from conftest import ctx, engine
from fastapi.testclient import TestClient
from sqlalchemy import text
from test_check_cases_pg import _owner, _tenant
from test_check_methods_pg import _signed_overlay, _to_production
from test_policy_gate_http_pg import AUD, ISS
from zid_crypto import KeyRing, LocalSigner
from zid_db import tenant_transaction
from zid_platform.main import create_app
from zid_platform.modules.session.ports import issue_access_token
from zid_platform.modules.verification.application import device_checks as svc
from zid_platform.modules.verification.domain import biometric_methods as bm
from zid_platform.modules.verification.domain import check_methods as cm
from zid_platform.modules.verification.domain import device_risk as dr
from zid_platform.modules.verification.infrastructure import check_method_config
from zid_platform.settings import Settings

DEVICE, EMAIL = cm.CheckMethod.DEVICE_RISK, cm.CheckMethod.EMAIL_CHALLENGE


class Verifier:
    async def verify(self, platform: dr.Platform, token: str, nonce: str) -> svc.Verified:
        return svc.Verified(dr.Attestation.VALID, (dr.Signal.EMULATOR,), "play-integrity@test")


def test_device_observations_need_a_cleared_case_a_fresh_nonce_and_keep_no_raw_reference(
    rt_url: str, owner_url: str, monkeypatch: pytest.MonkeyPatch
) -> None:
    v = uuid.uuid4().hex[:6]
    adapters = {
        m: cm.Adapter(m, f"zid-{m.value.lower().replace('_', '-')}", v, cm.Route.OPEN)
        for m in (DEVICE, EMAIL)
    }
    approved = {
        c: cm.RetentionRule(c, "APPROVED", 30, "controller", "L-1") for c in cm.ALL_DATA_CLASSES
    }
    monkeypatch.setattr(check_method_config, "adapters", lambda: adapters)
    monkeypatch.setattr(check_method_config, "retention", lambda: approved)
    tenant, admin = _tenant(rt_url)
    for m in (DEVICE, EMAIL):
        scope = bm.Scope(m, adapters[m].engine_id, v, "GB", "ONBOARDING", tenant)

        async def live(c: Any, scope: bm.Scope = scope, m: cm.CheckMethod = m) -> bm.Record:
            return await _to_production(c, scope, f"{m.value.lower().replace('_', '-')}-{v}")

        asyncio.run(_owner(owner_url, live))
    ring = KeyRing(LocalSigner("issuer-1"))
    app = create_app(
        Settings(database_url=rt_url, token_issuer=ISS, token_audience=AUD, public_base_url=AUD, environment="test"), keyring=ring
    )  # fmt: skip
    app.state.privacy_overlays = {"GB": _signed_overlay("GB")}
    app.state.attestation_verifier = Verifier()
    token = issue_access_token(
        ring, issuer=ISS, audience=AUD, subject=admin, tenant_id=tenant, client_id="onboarding-svc", now=datetime.now(UTC),
        scope=frozenset({"verification:execute", "verification:read"}),
    )  # fmt: skip
    h = {"Authorization": f"Bearer {token}"}

    def case(methods: list[str], key: str) -> str:
        body = {
            "subject_ref": "cust-7",
            "purpose": "ONBOARDING",
            "market": "GB",
            "methods": methods,
        }
        return str(
            http.post(
                "/v1/verification-cases", headers={**h, "Idempotency-Key": key}, json=body
            ).json()["case_id"]
        )

    with TestClient(app, base_url=ISS) as http:
        email_only, cid = case(["EMAIL_CHALLENGE"], f"e-{v}"), case(["DEVICE_RISK"], f"d-{v}")
        obs = {
            "session_ref": "s-1",
            "platform": "WEB",
            "device_ref": f"browser-{v}-ref",
            "signals": ["VPN_OR_PROXY"],
        }
        refused = http.post(
            "/v1/device-observations",
            headers={**h, "Idempotency-Key": f"o0-{v}"},
            json={**obs, "case_id": email_only},
        )
        assert refused.status_code == 409  # QA-030

        web = http.post(
            "/v1/device-observations",
            headers={**h, "Idempotency-Key": f"o1-{v}"},
            json={**obs, "case_id": cid},
        )
        assert web.status_code == 201, web.text
        assert web.json()["outcome"] == "PASS" and web.json()["reasons"] == ["VPN_OR_PROXY_NOTED"]

        nonce = http.post(
            f"/v1/verification-cases/{cid}/device-nonces", headers=h, json={"session_ref": "s-1"}
        ).json()["nonce"]
        android = {
            **obs,
            "case_id": cid,
            "platform": "ANDROID",
            "signals": [],
            "attestation_token": "signed-token",
            "nonce": nonce,
        }
        risky = http.post(
            "/v1/device-observations", headers={**h, "Idempotency-Key": f"o2-{v}"}, json=android
        )
        assert risky.status_code == 201 and risky.json()["outcome"] == "REVIEW"
        assert (
            risky.json()["recommendation"] == "STEP_UP_OR_REVIEW"
            and "EMULATOR" in risky.json()["reasons"]
        )
        again = http.post(
            "/v1/device-observations", headers={**h, "Idempotency-Key": f"o3-{v}"}, json=android
        )
        assert again.status_code == 409  # QA-032: a nonce is used once

        ev = http.get(f"/v1/verification-cases/{cid}/evidence", headers=h).json()
        assert [a["type"] for a in ev["assertions"]] == ["DEVICE_RISK_OBSERVED"] and ev[
            "assertions"
        ][0]["outcome"] == "REVIEW"

    async def rows() -> list[Any]:
        async with engine(rt_url) as eng, tenant_transaction(eng, ctx(tenant)) as c:
            return (
                await c.execute(
                    text("SELECT pseudonym, case_id FROM verify.check_device_observation")
                )
            ).all()

    found = asyncio.run(rows())
    assert len(found) == 2 and all(f"browser-{v}" not in r.pseudonym for r in found)
    assert {str(r.case_id) for r in found} == {cid}
