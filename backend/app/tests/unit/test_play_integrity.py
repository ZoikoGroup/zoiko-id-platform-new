"""Android Play Integrity for capture envelopes (ZID-CP-WF01 Q18; device_attestation). Synthetic verdicts and a mocked Google: the real service is never called here.

A verdict passes only when every check holds; Google unreachable or nothing configured is None (not established), never a pass; a token Google refuses is False."""

from __future__ import annotations

import asyncio
import json
from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import Any

import httpx
from cryptography.hazmat.primitives import serialization
from cryptography.hazmat.primitives.asymmetric import rsa
from zid_platform.modules.verification.infrastructure import device_attestation as da

NOW = datetime(2026, 11, 24, 12, tzinfo=UTC)
CHALLENGE = b"nonce-1:" + b"a" * 64
KEY = rsa.generate_private_key(public_exponent=65537, key_size=2048).private_bytes(
    serialization.Encoding.PEM, serialization.PrivateFormat.PKCS8, serialization.NoEncryption()
).decode()
CFG = da.PlayIntegrityConfig("com.zoiko.app", frozenset({"CERT-DIGEST"}), "sa@proj.iam.gserviceaccount.com", KEY)


def verdict(**over: Any) -> dict[str, Any]:
    v: dict[str, Any] = {
        "requestDetails": {"requestPackageName": "com.zoiko.app", "requestHash": da.request_hash(CHALLENGE), "timestampMillis": str(int(NOW.timestamp() * 1000))},
        "appIntegrity": {"appRecognitionVerdict": "PLAY_RECOGNIZED", "packageName": "com.zoiko.app", "certificateSha256Digest": ["CERT-DIGEST"]},
        "deviceIntegrity": {"deviceRecognitionVerdict": ["MEETS_DEVICE_INTEGRITY"]},
    }
    for path, value in over.items():
        section, key = path.split("__")
        v[section][key] = value
    return v


def test_a_genuine_verdict_for_this_capture_passes_and_every_check_can_fail_alone() -> None:
    assert da.check_play_verdict(verdict(), cfg=CFG, challenge=CHALLENGE, now=NOW) is True
    for over in (
        {"requestDetails__requestPackageName": "com.other"},
        {"requestDetails__requestHash": da.request_hash(b"another capture")},  # bound to a different capture
        {"requestDetails__timestampMillis": str(int((NOW - timedelta(hours=1)).timestamp() * 1000))},  # stale
        {"appIntegrity__appRecognitionVerdict": "UNRECOGNIZED_VERSION"},  # a modified app
        {"appIntegrity__certificateSha256Digest": ["SOMEONE-ELSES-KEY"]},  # re-signed
        {"deviceIntegrity__deviceRecognitionVerdict": []},  # an emulator or a tampered device
    ):
        assert da.check_play_verdict(verdict(**over), cfg=CFG, challenge=CHALLENGE, now=NOW) is False, over
    assert da.check_play_verdict({"requestDetails": {}}, cfg=CFG, challenge=CHALLENGE, now=NOW) is False


def google(decoded: dict[str, Any] | None, *, status: int = 200, down: bool = False) -> httpx.MockTransport:
    def handle(req: httpx.Request) -> httpx.Response:
        if down:
            raise httpx.ConnectError("unreachable")
        if req.url.host == "oauth2.googleapis.com":
            assert b"jwt-bearer" in req.content
            return httpx.Response(200, json={"access_token": "at", "expires_in": 3600})
        assert req.headers["authorization"] == "Bearer at" and req.url.path == "/v1/com.zoiko.app:decodeIntegrityToken"
        assert json.loads(req.content) == {"integrity_token": "tok"}
        return httpx.Response(status, json={"tokenPayloadExternal": decoded} if decoded is not None else {})

    return httpx.MockTransport(handle)


def run(transport: httpx.MockTransport, report: dict[str, Any] | None = None) -> bool | None:
    da._access.clear()
    return asyncio.run(da.verify_play_integrity({"integrity_token": "tok"} if report is None else report, challenge=CHALLENGE, cfg=CFG, transport=transport, now=NOW))


def test_the_token_is_decoded_by_google_and_judged_here() -> None:
    assert run(google(verdict())) is True
    assert run(google(verdict(deviceIntegrity__deviceRecognitionVerdict=[]))) is False
    assert run(google(None, status=400)) is False  # Google refused the token itself
    assert run(google(verdict()), report={}) is False  # no token at all


def test_unreachable_or_unconfigured_is_not_available_never_a_pass(tmp_path: Path) -> None:
    assert run(google(verdict(), down=True)) is None
    assert run(google(None, status=503)) is None
    assert da.configured_play() is None  # the repository ships it unconfigured
    assert asyncio.run(da.verify_play_integrity({"integrity_token": "tok"}, challenge=CHALLENGE)) is None
    assert da.load_play_config({"android": {"package_name": "com.zoiko.app"}}) is None  # half-filled: no partial check
    sa = tmp_path / "sa.json"
    sa.write_text(json.dumps({"client_email": "sa@x", "private_key": KEY}), encoding="utf-8")
    cfg = da.load_play_config({"android": {"package_name": "com.zoiko.app", "certificate_sha256": ["D"], "service_account_path": str(sa)}})
    assert cfg is not None and cfg.device_verdicts == frozenset({"MEETS_DEVICE_INTEGRITY"}) and cfg.token_uri == "https://oauth2.googleapis.com/token"
