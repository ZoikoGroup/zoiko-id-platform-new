"""Public proof checking and the signed trust bundle (ZID-CP-WF08 v2.0 .04 to .06, .08, section 08.1; CR-170), against a real sealed receipt in PostgreSQL.

What these hold: a sealed receipt's evidence bundle checks out as separate axes with no overall success; the same check on a tampered bundle, a bundle signed by a key the verifier never pinned, or garbage fails on its own axis and
looks the same whether or not the record exists (no oracle); the check stores nothing, is size-capped and rate-limited; the issuer's trust bundle is signed with the evidence key, lists rotated and compromised keys with their standing,
and a stale, substituted or rolled-back copy is refused; and the authenticated verification now carries the same axes.
"""

from __future__ import annotations

import asyncio
import copy
import json
import uuid
from dataclasses import replace
from datetime import UTC, datetime, timedelta
from typing import Any

import pytest
from conftest import ctx, engine
from fastapi import FastAPI, Request
from fastapi.responses import JSONResponse
from fastapi.testclient import TestClient
from test_evidence_pg import decide, fresh_runtime, receipt_of, seed_admin  # noqa: F401 - the fixture sets the evidence runtime
from zid_crypto import LocalSigner, verify_jws
from zid_db import tenant_transaction
from zid_errors import ZidError
from zid_platform.modules.evidence.api import public_proof
from zid_platform.modules.evidence.application import replay
from zid_platform.modules.evidence.application.runtime import configure_runtime, current_runtime
from zid_platform.modules.evidence.domain import trust_bundle as tb
from zid_platform.modules.evidence.ports import worker_pass


def uid() -> str:
    return str(uuid.uuid4())


def app() -> FastAPI:
    a = FastAPI()

    @a.exception_handler(ZidError)
    async def _zid_error(request: Request, exc: ZidError) -> JSONResponse:
        return JSONResponse(status_code=exc.http_status, content={"error": {"code": exc.code, "message": exc.message}}, headers=dict(exc.headers) or None)

    a.include_router(public_proof.router)
    a.state.environment = "production"
    return a


def sealed_bundle(rt_url: str, owner_url: str) -> tuple[dict[str, Any], str, str, str]:
    tenant, admin = uid(), uid()
    out: dict[str, Any] = {}

    async def run() -> None:
        async with engine(rt_url) as eng, tenant_transaction(eng, ctx(tenant)) as c:
            await seed_admin(c, tenant, admin, ["evidence.export"])
            d = await decide(c, tenant, admin, "evidence.export")
            out["receipt_id"] = str((await receipt_of(c, d.decision_id)).receipt_id)
        async with engine(owner_url) as eng, eng.begin() as c:
            await worker_pass(c, force_seal=True)
        async with engine(rt_url) as eng, tenant_transaction(eng, ctx(tenant)) as c:
            pkg = await replay.export_package(c, tenant, out["receipt_id"], mode="CUSTOMER_VERIFY", purpose="self-verification", case_ref="case-1", custodian_principal_id=admin)
            out["bundle"] = pkg["bundle"]
            out["verification"] = (await replay.verify_receipt(c, out["receipt_id"])).to_dict()

    asyncio.run(run())
    return out["bundle"], out["receipt_id"], tenant, admin


def test_a_sealed_bundle_checks_out_as_separate_axes_and_every_failure_stays_on_its_own_axis(rt_url: str, owner_url: str) -> None:
    bundle, receipt_id, _tenant, _admin = sealed_bundle(rt_url, owner_url)
    public_proof._limiter = public_proof._Window(window=60, limit=30)
    with TestClient(app()) as http:
        ok = http.post("/v1/receipts/verify", json={"bundle": bundle})
        assert ok.status_code == 200, ok.text
        got = ok.json()
        a = got["axes"]
        assert a["signature_integrity"] == "VALID" and a["issuer_trust"] == "TRUSTED" and a["proof_completeness"] == "COMPLETE"
        assert a["success"] is None and a["real_world_claim_verification"] == "NOT_ASSESSED" and a["time_proof"] == "NONE" and a["status_now"] == "NOT_CHECKED"
        assert got["stored"] is False and got["record_included"] is True and got["summary"][-1] == "Correctness of the decision: not assessed" and ok.headers["cache-control"] == "no-store"
        assert "success" not in {k for k in got} and "ok" not in got  # no top-level pass

        # a changed record: integrity fails on its own axis, the signer is still the trusted one
        altered = copy.deepcopy(bundle)
        altered["record"]["outcome"] = "DENY" if altered["record"].get("outcome") != "DENY" else "ALLOW"
        r = http.post("/v1/receipts/verify", json={"bundle": altered}).json()["axes"]
        assert r["signature_integrity"] == "INVALID" and "CHECK_FAILED:record_hash" in r["reasons"] and r["issuer_trust"] == "TRUSTED"
        # a forged checkpoint signature is both bad integrity and an untrusted signer
        forged = copy.deepcopy(bundle)
        forged["checkpoint"]["signature"] = forged["checkpoint"]["signature"][:-6] + "AAAAAA"
        f = http.post("/v1/receipts/verify", json={"bundle": forged}).json()["axes"]
        assert f["signature_integrity"] == "INVALID" and f["issuer_trust"] == "UNTRUSTED"

        # a key the verifier never pinned: untrusted, never success (the issuer rotated to a key this bundle's signer is not part of)
        rt = current_runtime()
        configure_runtime(replace(rt, signer=LocalSigner("someone-elses-key"), historic_keys=()))
        try:
            u = http.post("/v1/receipts/verify", json={"bundle": bundle}).json()["axes"]
            assert u["issuer_trust"] == "UNTRUSTED" and u["signature_integrity"] == "INVALID"
        finally:
            configure_runtime(rt)

        # garbage and a fabricated record answer the same way: 200 and a failed axis, never a "no such receipt" that would reveal what exists
        junk = http.post("/v1/receipts/verify", json={"bundle": {"format": "nonsense"}})
        fake = copy.deepcopy(bundle)
        fake["record_id"] = uid()
        fake["record"]["receipt_id"] = fake["record_id"]
        fk = http.post("/v1/receipts/verify", json={"bundle": fake})
        assert junk.status_code == 200 and fk.status_code == 200
        assert junk.json()["axes"]["signature_integrity"] in ("INVALID", "INDETERMINATE") and fk.json()["axes"]["signature_integrity"] == "INVALID"
        assert "NOT_FOUND" not in json.dumps([junk.json(), fk.json()])
        # a bundle bigger than any proof is refused, and the check is rate limited per caller
        assert http.post("/v1/receipts/verify", json={"bundle": {"pad": "x" * 300_000}}).status_code == 413
        public_proof._limiter = public_proof._Window(window=60, limit=5)
        codes = [http.post("/v1/receipts/verify", json={"bundle": {"format": "x"}}).status_code for _ in range(7)]
        assert codes[:5] == [200] * 5 and codes[5:] == [429, 429]
    public_proof._limiter = public_proof._Window(window=60, limit=30)
    # the same evidence over the authenticated route carries the axes too, and its `ok` is only the integrity checks
    assert _authenticated_axes(rt_url, owner_url)["success"] is None


def _authenticated_axes(rt_url: str, owner_url: str) -> dict[str, Any]:
    bundle, receipt_id, tenant, _a = sealed_bundle(rt_url, owner_url)

    async def run() -> dict[str, Any]:
        async with engine(rt_url) as eng, tenant_transaction(eng, ctx(tenant)) as c:
            return (await replay.verify_receipt(c, receipt_id)).to_dict()

    return asyncio.run(run())["axes"]  # type: ignore[no-any-return]


def test_the_trust_bundle_is_signed_by_the_evidence_key_lists_rotation_and_compromise_and_a_stale_copy_is_refused(rt_url: str, owner_url: str) -> None:
    public_proof._limiter = public_proof._Window(window=60, limit=30)
    with TestClient(app()) as http:
        r = http.get("/v1/trust/issuer")
        assert r.status_code == 200 and r.headers["cache-control"] == "public, max-age=300"
        body = r.json()
        rt = current_runtime()
        claims = verify_jws(body["signature"], keys=rt.trusted_keys(), typ=tb.BUNDLE_TYP)[1]  # verified with keys pinned beforehand
        assert claims == body["bundle"] and body["typ"] == tb.BUNDLE_TYP
        assert claims["environment"] == "production" and claims["pq_hybrid"] == "NOT_OFFERED" and {k["kid"] for k in claims["keys"]} == {k["kid"] for k in rt.trusted_keys()}
        now = datetime.now(UTC)
        assert tb.bundle_problems(claims, expected_issuer=claims["issuer"], expected_environment="production", now=now) == []
        # stale, substituted and other-environment copies are refused by a careful verifier
        assert tb.bundle_problems(claims, expected_issuer=claims["issuer"], expected_environment="production", now=now + timedelta(days=2)) == ["TRUST_BUNDLE_EXPIRED"]
        assert tb.bundle_problems(claims, expected_issuer="another-issuer", expected_environment="production", now=now) == ["TRUST_BUNDLE_WRONG_ISSUER"]
        assert tb.bundle_problems(claims, expected_issuer=claims["issuer"], expected_environment="sandbox", now=now) == ["TRUST_BUNDLE_WRONG_ENVIRONMENT"]
        first_version = claims["version"]

        # a graceful rotation keeps the old key trusted for history and raises the version
        configure_runtime(replace(rt, signer=LocalSigner("evidence-key-v2"), historic_keys=(*rt.historic_keys, rt.signer.public_jwk())))
        try:
            rotated = http.get("/v1/trust/issuer").json()["bundle"]
            states = {k["kid"]: k["state"] for k in rotated["keys"]}
            assert states["evidence-key-v2"] == "ACTIVE" and states[str(rt.signer.public_jwk()["kid"])] == "RETIRED" and rotated["version"] > first_version
            assert tb.bundle_problems(claims, expected_issuer=claims["issuer"], expected_environment="production", now=now, last_seen_version=rotated["version"]) == ["TRUST_BUNDLE_ROLLED_BACK"]  # the old copy is refused once a newer one was seen
        finally:
            configure_runtime(rt)
        # a compromise is listed with the moment from which that key's signatures prove nothing
        since = now - timedelta(hours=3)
        configure_runtime(rt.after_compromise(LocalSigner("evidence-key-v3"), compromised_at=since))
        try:
            c3 = http.get("/v1/trust/issuer").json()["bundle"]
            bad = next(k for k in c3["keys"] if k["kid"] == str(rt.signer.public_jwk()["kid"]))
            assert bad["state"] == "COMPROMISED" and bad["compromised_from"] and c3["version"] > first_version
            assert tb.key_standing(c3, bad["kid"], since - timedelta(days=1)) == "TRUSTED_RETIRED" and tb.key_standing(c3, bad["kid"], now) == "COMPROMISED_AT_SIGNING"
        finally:
            configure_runtime(rt)
    with pytest.raises(Exception):  # noqa: B017, PT011 - a bundle signed by the evidence key is not accepted as a token-keyring statement
        verify_jws(body["signature"], keys=[LocalSigner("some-token-key").public_jwk()], typ=tb.BUNDLE_TYP)
