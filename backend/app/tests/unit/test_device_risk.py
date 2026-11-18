"""WF-05 device risk (DTV-22; QA-030 to QA-035; CR-116). Synthetic only."""

from __future__ import annotations

from datetime import UTC, datetime, timedelta

import pytest
from zid_platform.modules.verification.domain import device_risk as dr

NOW = datetime(2026, 11, 18, 9, 0, tzinfo=UTC)
KEY = b"k" * 32
A, P, S = dr.Attestation, dr.Platform, dr.Signal


def test_qa031_concerning_signals_are_review_with_a_step_up_and_never_fail():
    for sig in (S.EMULATOR, S.ROOTED_OR_JAILBROKEN, S.AUTOMATION):
        e = dr.evaluate(P.ANDROID, A.VALID, [sig])
        assert (
            e.verdict == "REVIEW"
            and sig.value in e.reasons
            and e.recommendation == "STEP_UP_OR_REVIEW"
        )
    assert dr.evaluate(P.IOS, A.INVALID, []).reasons == ("APP_INTEGRITY_FAILED",)
    assert all(dr.evaluate(p, a, list(S)).verdict != "FAIL" for p in P for a in A)


def test_qa033_a_vpn_alone_is_only_noted():
    e = dr.evaluate(P.WEB, A.NOT_REQUESTED, [S.VPN_OR_PROXY])
    assert e.verdict == "PASS" and e.reasons == ("VPN_OR_PROXY_NOTED",)


def test_a_mobile_device_without_a_valid_attestation_is_indeterminate_and_web_needs_none():
    e = dr.evaluate(P.ANDROID, A.UNAVAILABLE, [])
    assert e.verdict == "INDETERMINATE" and "DEVICE_SIGNAL_INDETERMINATE" in e.reasons
    assert dr.evaluate(P.WEB, A.NOT_REQUESTED, []).verdict == "PASS"
    assert dr.evaluate(P.IOS, A.VALID, []).label == "No concerning signal"


def test_qa034_pseudonyms_differ_across_tenants_and_rotate_by_period():
    one = dr.pseudonym(KEY, "t-1", "device-abc-123", now=NOW, rotation_days=30)
    assert one.startswith("dp1.") and "device-abc-123" not in one
    assert one != dr.pseudonym(KEY, "t-2", "device-abc-123", now=NOW, rotation_days=30)
    assert one != dr.pseudonym(
        KEY, "t-1", "device-abc-123", now=NOW + timedelta(days=31), rotation_days=30
    )
    for bad in ({"raw": "short"}, {"rotation_days": 0}):
        with pytest.raises(dr.DeviceError):
            dr.pseudonym(
                KEY,
                "t-1",
                bad.get("raw", "device-abc-123"),
                now=NOW,
                rotation_days=bad.get("rotation_days", 30),
            )  # type: ignore[arg-type]


def test_qa032_a_nonce_is_single_use_short_lived_and_bound_to_the_session():
    dr.check_nonce(expires_at=NOW + dr.NONCE_TTL, consumed=False, session_ok=True, now=NOW)
    for kw, code in (
        ({"consumed": True}, "NONCE_REPLAYED"),
        ({"now": NOW + timedelta(minutes=6)}, "NONCE_EXPIRED"),
        ({"session_ok": False}, "NONCE_SESSION_MISMATCH"),
    ):
        args = {
            "expires_at": NOW + dr.NONCE_TTL,
            "consumed": False,
            "session_ok": True,
            "now": NOW,
            **kw,
        }
        with pytest.raises(dr.DeviceError) as e:
            dr.check_nonce(**args)  # type: ignore[arg-type]
        assert e.value.code == code
    assert dr.nonce_hash(KEY, dr.new_nonce()) != dr.nonce_hash(KEY, dr.new_nonce())
