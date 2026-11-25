"""Public proof checking and the issuer's signed trust bundle (ZID-CP-WF08 v2.0 .05, .06, .11; section 08; CR-170). No sign-in is needed, and none of it can be used to look anything up.

    POST /v1/receipts/verify     check an exported evidence bundle that YOU hold, against the issuer's pinned keys. It reads only what it is given: it never looks a receipt up, stores nothing and answers
                                 the same whether or not Zoiko iD has ever seen the record, so it is no oracle for whether a receipt exists. The answer is a set of separate axes, never one checkmark.
    GET  /v1/trust/issuer        the issuer's signed, versioned, expiring trust bundle: each signing key with its standing and, if compromised, from when. Verify its signature with keys you pinned before.

Rate-limited per caller, and the size of a bundle is capped. Unauthenticated callers can choose no private tier: there is none here.
"""

from __future__ import annotations

import json
import time
from collections import OrderedDict, deque
from datetime import UTC, datetime
from typing import Any

from fastapi import APIRouter, Request, Response
from pydantic import BaseModel, ConfigDict
from zid_crypto import sign_jws
from zid_errors import ZidError
from zid_evidence import verify_bundle

from zid_platform.modules.evidence.application.runtime import current_runtime
from zid_platform.modules.evidence.domain import trust_bundle as tb
from zid_platform.modules.evidence.domain import verification_axes as va

router = APIRouter(tags=["evidence-public-proof"])
MAX_BUNDLE_BYTES = 256 * 1024


class _Window:
    """A request counter per caller over a sliding window. Small and bounded: the oldest callers are forgotten first."""

    def __init__(self, window: float, limit: int, keys: int = 10_000) -> None:
        self._window, self._limit, self._max = window, limit, keys
        self._seen: OrderedDict[str, deque[float]] = OrderedDict()

    def hit(self, key: str) -> int:
        """Records one request. Returns 0 when allowed, else the seconds to wait."""
        now = time.monotonic()
        q = self._seen.pop(key, deque())
        while q and now - q[0] > self._window:
            q.popleft()
        if len(q) >= self._limit:
            self._seen[key] = q
            return max(1, int(self._window - (now - q[0])))
        q.append(now)
        self._seen[key] = q
        while len(self._seen) > self._max:
            self._seen.popitem(last=False)
        return 0


_limiter = _Window(window=60, limit=30)  # 30 requests a minute per caller


class ReceiptVerifyBody(BaseModel):
    model_config = ConfigDict(extra="forbid")
    bundle: dict[str, Any]


def _who(request: Request) -> str:
    return request.client.host if request.client else "unknown"


def _check_rate(request: Request) -> None:
    wait = _limiter.hit(_who(request))
    if wait:
        raise ZidError("RATE_LIMITED", "Too many checks. Try again shortly.", http_status=429, headers={"Retry-After": str(wait)})


@router.post("/v1/receipts/verify")
async def verify_uploaded(body: ReceiptVerifyBody, request: Request, response: Response) -> dict[str, Any]:
    _check_rate(request)
    if len(json.dumps(body.bundle, separators=(",", ":"), default=str)) > MAX_BUNDLE_BYTES:
        raise ZidError("VALIDATION_FAILED", "That bundle is larger than a proof ever is.", http_status=413)
    rt = current_runtime()
    report = verify_bundle(body.bundle, trusted_keys=rt.trusted_keys())  # pinned to the issuer's published keys; never the keys the bundle brought
    checks = [{"name": c.name, "ok": c.ok, "detail": c.detail} for c in report.checks]
    names = {c["name"]: c for c in checks}
    record_checked = "record_hash" in names and "not included" not in str(names["record_hash"]["detail"])
    result = va.axes(
        checks=checks,
        receipt_hash_valid=bool(names.get("record_hash", {}).get("ok")) and record_checked,
        ledgered="entry_hash" in names,
        sealed="checkpoint_signature" in names and "inclusion_proof" in names,
        keys_source=report.keys_source,
        now=datetime.now(UTC),
    )
    response.headers["Cache-Control"] = "no-store"
    return {
        "axes": result,
        "checks": checks,
        "summary": va.summary_words(result),
        "record_included": record_checked,
        "stored": False,
        "limits": "This checked the proof you gave it, offline-style, against the issuer's pinned keys. It did not look anything up, and it says nothing about whether the decision was right.",
    }


@router.get("/v1/trust/issuer")
async def issuer_bundle(request: Request, response: Response) -> dict[str, Any]:
    _check_rate(request)
    rt = current_runtime()
    keys = rt.trusted_keys()
    issuer = str(getattr(getattr(request.app.state, "token_settings", None), "issuer", "zoiko-id"))
    claims = tb.bundle_claims(
        issuer=issuer, environment=str(getattr(request.app.state, "environment", "unknown")), keys=keys, active_kid=str(rt.signer.public_jwk()["kid"]), compromised=rt.compromised,
        version=tb.version_from(keys, rt.compromised), now=datetime.now(UTC),
    )  # fmt: skip
    response.headers["Cache-Control"] = "public, max-age=300"  # a verifier may reuse it for minutes, and the bundle itself says when it expires
    return {"bundle": claims, "signature": sign_jws(rt.signer, claims, typ=tb.BUNDLE_TYP), "typ": tb.BUNDLE_TYP, "verify_with": "keys you pinned earlier (GET /v1/evidence/keys is where they were first published)"}
