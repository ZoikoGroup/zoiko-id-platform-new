"""WF-04 gap closure (CR-113): release attestation (QA-041), erasure retry (QA-023), DPIA policy change (QA-004), per-class encryption keys
(QA-021), breach data classes (QA-034), the region-block event (QA-029) and the event payload scan (QA-035)."""

from __future__ import annotations

import ast
import re
from dataclasses import replace
from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import Any

import pytest
import yaml
from zid_platform.modules.evidence.application import vault
from zid_platform.modules.privacy.application import control, dpia_policy, erasure_retry
from zid_platform.modules.privacy.domain.model import PrivacyError
from zid_platform.modules.verification.application import biometric_eligibility as be
from zid_platform.modules.verification.application import biometric_release as rel
from zid_platform.modules.verification.application import privacy_gate
from zid_platform.modules.verification.domain import biometric_methods as bm
from zid_platform.modules.verification.domain import privacy_overlay as po

NOW = datetime(2026, 11, 18, 12, tzinfo=UTC)
SRC = Path(__file__).resolve().parents[2] / "src" / "zid_platform"
CONTRACTS = Path(__file__).resolve().parents[4] / "contracts" / "asyncapi" / "proposed-events.yaml"


def _att(build: str = "sha256:aaa", sdk: str = "capture-4.2") -> rel.Attestation:
    return rel.Attestation("face-gb", 3, "d" * 64, build, sdk, "ben", NOW, "a" * 64)


# ------------------------------------------------------------------------------------------------ QA-041 release attestation


def test_running_release_reads_the_deployment_and_says_unrecorded_otherwise() -> None:
    assert rel.running({}) == rel.Release("unrecorded", "unrecorded")
    assert rel.running(
        {"ZID_RELEASE_BUILD": " sha256:abc ", "ZID_BIOMETRIC_CAPTURE_SDK": "4.2"}
    ) == rel.Release("sha256:abc", "4.2")
    assert rel.binding_mode({}) == "report"
    assert rel.binding_mode({"ZID_BIOMETRIC_RELEASE_BINDING": "ENFORCE"}) == "enforce"
    assert rel.binding_mode({"ZID_BIOMETRIC_RELEASE_BINDING": "anything-else"}) == "report"


def test_attestation_hash_covers_the_build_the_sdk_and_the_approver() -> None:
    base = {"scope_id": "face-gb", "version": 3, "decision_hash": "d" * 64, "by": "ben"}
    h = rel.attestation_hash(release=rel.Release("b1", "s1"), **base)
    assert h != rel.attestation_hash(release=rel.Release("b2", "s1"), **base)
    assert h != rel.attestation_hash(release=rel.Release("b1", "s2"), **base)
    assert h != rel.attestation_hash(release=rel.Release("b1", "s1"), **{**base, "by": "eve"})
    assert re.fullmatch(r"[0-9a-f]{64}", h)


def test_drift_is_only_for_a_live_scope_and_names_what_changed() -> None:
    running = rel.Release("sha256:bbb", "capture-4.3")
    assert rel.drift(bm.State.LAB_ONLY, None, running) == ()
    assert rel.drift(bm.State.OFF, _att(), running) == ()
    assert rel.drift(bm.State.PILOT_GATED, None, running) == ("RELEASE_NOT_ATTESTED",)
    assert rel.drift(bm.State.PRODUCTION_ELIGIBLE, _att(), running) == (
        "RELEASE_BUILD_CHANGED",
        "RELEASE_SDK_CHANGED",
    )
    assert (
        rel.drift(bm.State.PRODUCTION_ELIGIBLE, _att(), rel.Release("sha256:aaa", "capture-4.2"))
        == ()
    )
    # a deployment that does not record its build cannot be compared, and is not called a change
    assert (
        rel.drift(bm.State.PRODUCTION_ELIGIBLE, _att(), rel.Release("unrecorded", "unrecorded"))
        == ()
    )


def _overlay() -> po.Overlay:
    o = po.Overlay("GB", 1, frozenset({"EU_WEST"}), {}, {}, po.Biometrics.ALLOWED)
    d = po.overlay_hash(o)
    return replace(o, approvals={r: po.Approval(f"{r}-sig", d) for r in po.APPROVER_ROLES})


def _answer(**kw: Any) -> be.MethodAnswer:
    scope = bm.Scope(bm.Method.FACE_MATCH, "acme-face", "2.1.0", "GB", "IDENTITY_VERIFICATION")
    rec = bm.Record(
        "face-gb",
        3,
        scope,
        bm.State.PRODUCTION_ELIGIBLE,
        "D",
        "E",
        "approved for production",
        "T-1",
        "ben",
        "ana",
        "p1",
        NOW,
        NOW,
    )
    state = bm.Availability(True, bm.State.PRODUCTION_ELIGIBLE, (), rec)
    return be.combine(
        bm.Method.FACE_MATCH,
        engine_reasons=[],
        state=state,
        dpia={"ready": True, "blockers": [], "package_id": "pkg"},
        overlay=_overlay(),
        **kw,
    )


def test_release_drift_is_reported_and_refuses_only_in_enforce_mode() -> None:
    reported = _answer(release_drift=("RELEASE_BUILD_CHANGED",), release_attestation="a" * 64)
    assert reported.available and reported.view()["release_drift"] == ["RELEASE_BUILD_CHANGED"]
    assert reported.view()["release_attestation"] == "a" * 64
    enforced = _answer(release_drift=("RELEASE_BUILD_CHANGED",), release_enforced=True)
    assert not enforced.available and "RELEASE_BUILD_CHANGED" in enforced.reasons


def test_only_an_approved_step_up_is_attested() -> None:
    import asyncio

    scope = bm.Scope(bm.Method.FACE_MATCH, "acme-face", "2.1.0", "GB", "IDENTITY_VERIFICATION")
    step_down = bm.Record(
        "face-gb",
        4,
        scope,
        bm.State.PAUSED,
        "D",
        "E",
        "paused after incident",
        "T-2",
        "ben",
        None,
        None,
        NOW,
        NOW,
    )
    with pytest.raises(bm.MethodError) as e:
        asyncio.run(rel.attest(None, step_down, by="ben", now=NOW))  # type: ignore[arg-type]
    assert e.value.code == "METHOD_RELEASE_NOT_SIGNED"


# ------------------------------------------------------------------------------------------------ QA-023 erasure retry


def test_retry_waits_grow_and_then_alert() -> None:
    steps = [erasure_retry.next_step(n) for n in range(len(erasure_retry.RETRY_WAITS) + 2)]
    waits = [w for s, w in steps if s == "RETRY"]
    assert waits == sorted(waits) and len(waits) == len(erasure_retry.RETRY_WAITS)
    assert steps[len(erasure_retry.RETRY_WAITS)] == ("ALERT", None)
    assert steps[-1] == ("ALERT", None)
    assert sum(erasure_retry.RETRY_WAITS, timedelta()) < timedelta(days=30)


# ------------------------------------------------------------------------------------------------ QA-004 DPIA policy change


def test_a_policy_change_after_approval_is_called_changed() -> None:
    a, b = "1" * 64, "2" * 64
    assert dpia_policy.changed("APPROVED", a, b)
    assert not dpia_policy.changed("APPROVED", a, a)
    assert not dpia_policy.changed("APPROVED", None, b)  # approved before the digest existed
    assert not dpia_policy.changed("IN_REVIEW", a, b)
    assert dpia_policy.digest({"x": [1, 2]}) == dpia_policy.digest({"x": [1, 2]})
    assert dpia_policy.digest({"x": [1, 2]}) != dpia_policy.digest({"x": [2, 1]})


# ------------------------------------------------------------------------------------------------ QA-021 per-class keys


def test_each_biometric_class_has_its_own_key_per_subject() -> None:
    subject = bytearray(b"k" * 32)
    keys = {
        c: bytes(vault.class_key(subject, "t1", c))
        for c in ("biometric.live_selfie", "biometric.face_template", "biometric.match_result")
    }
    assert len(set(keys.values())) == 3
    assert all(k != bytes(subject) and len(k) == 32 for k in keys.values())
    assert (
        bytes(vault.class_key(subject, "t1", "biometric.face_template"))
        == keys["biometric.face_template"]
    )  # deterministic
    assert (
        bytes(vault.class_key(subject, "t2", "biometric.face_template"))
        != keys["biometric.face_template"]
    )  # tenant-bound
    assert (
        bytes(vault.class_key(bytearray(b"j" * 32), "t1", "biometric.face_template"))
        != keys["biometric.face_template"]
    )


def test_only_marked_objects_use_a_class_key() -> None:
    subject = bytearray(b"k" * 32)
    assert vault.is_class_keyed("biometric.face_template") and not vault.is_class_keyed(
        "identity.document"
    )
    assert bytes(vault._object_key(subject, "t1", "biometric.face_template", "subject:1")) == bytes(
        subject
    )  # stored before CR-113
    assert bytes(
        vault._object_key(
            subject, "t1", "biometric.face_template", "subject:1" + vault.CLASS_KEY_MARK
        )
    ) == bytes(vault.class_key(subject, "t1", "biometric.face_template"))


def test_a_class_key_does_not_open_another_class() -> None:
    subject = bytearray(b"k" * 32)
    aad = b"aad"
    blob = vault._encrypt(
        vault.class_key(subject, "t1", "biometric.face_template"), aad, b"template-bytes"
    )
    assert (
        vault._decrypt(vault.class_key(subject, "t1", "biometric.face_template"), aad, blob)
        == b"template-bytes"
    )
    with pytest.raises(Exception):  # noqa: B017 - LedgerError, the integrity failure
        vault._decrypt(vault.class_key(subject, "t1", "biometric.match_result"), aad, blob)


# ------------------------------------------------------------------------------------------------ QA-034 breach data classes


def test_breach_data_classes_are_codes() -> None:
    assert control._data_classes(
        ["biometric.face_template", " identity.document ", "biometric.face_template"]
    ) == [
        "biometric.face_template",
        "identity.document",
    ]
    for bad in (
        ["Jane Smith"],
        ["biometric.face template"],
        ["a@b.com"],
        [f"c{i}" for i in range(33)],
    ):
        with pytest.raises(PrivacyError) as e:
            control._data_classes(bad)
        assert e.value.code == "DATA_CLASS_INVALID"


# ------------------------------------------------------------------------------------------------ QA-029 region block


def test_region_reasons_are_the_overlay_region_refusals() -> None:
    o = replace(_overlay(), provider_regions={"veriff": "US_EAST"})
    d = po.route(o, provider="veriff", biometric=True, consent=None)
    assert "CROSS_BORDER_ROUTE_DENIED" in d.reasons and privacy_gate.REGION_REASONS.intersection(
        d.reasons
    )
    unknown = po.route(_overlay(), provider="veriff", biometric=True, consent=None)
    assert "PROVIDER_REGION_UNKNOWN" in unknown.reasons
    ok = po.route(
        replace(_overlay(), provider_regions={"veriff": "EU_WEST"}),
        provider="veriff",
        biometric=True,
        consent=None,
    )
    assert not privacy_gate.REGION_REASONS.intersection(ok.reasons)


# ------------------------------------------------------------------------------------------------ QA-035 payload scan

# The evidence ledger's own minimization rule, plus the biometric words a SIEM scan looks for.
_LEDGER = re.compile(
    r"(?i)(^|_)(e?mail|name|given_name|family_name|full_name|phone|address|document|passport|"
    r"biometric|face|selfie|fingerprint|prompt|password|secret|token|credential_value|raw|payload|"
    r"ssn|national_id|dob|birth)(_|$)"
)
_MEDIA = re.compile(
    r"(?i)(^|_)(image|photo|media|template|embedding|vector|frame|video|portrait|score)(_|$)"
)
_ALLOWED = frozenset({"action", "purpose_id", "outcome"})
# every file that emits a WF-04 biometric or privacy-control event
_EMITTERS = (
    "modules/verification/application/biometric_eligibility.py",
    "modules/verification/application/privacy_gate.py",
    "modules/verification/application/biometric_release.py",
    "modules/verification/application/biometric_markets.py",
    "modules/privacy/application/control.py",
    "modules/privacy/application/erasure_retry.py",
    "modules/privacy/application/reauthorization.py",
    "modules/privacy/application/sessions.py",
    "modules/operator_access/api/regulatory_biometric_markets.py",
)


def _payload_keys(tree: ast.AST) -> list[tuple[int, str]]:
    """The literal keys of every dict passed to an event or audit call (emit, _event, _audit, act details)."""
    out: list[tuple[int, str]] = []
    for node in ast.walk(tree):
        if not isinstance(node, ast.Call):
            continue
        name = (
            node.func.attr if isinstance(node.func, ast.Attribute) else getattr(node.func, "id", "")
        )
        if name not in {"emit", "_event", "_audit", "record_audit"}:
            continue
        for arg in [*node.args, *(k.value for k in node.keywords)]:
            for sub in ast.walk(arg):
                if isinstance(sub, ast.Dict):
                    out += [
                        (sub.lineno, k.value)
                        for k in sub.keys
                        if isinstance(k, ast.Constant) and isinstance(k.value, str)
                    ]
    return out


@pytest.mark.parametrize("rel_path", _EMITTERS)
def test_no_biometric_event_or_audit_payload_names_media_or_personal_data(
    rel_path: str,
) -> None:  # QA-035
    tree = ast.parse((SRC / rel_path).read_text(encoding="utf-8"))
    bad = [
        (line, key)
        for line, key in _payload_keys(tree)
        if key not in _ALLOWED and (_LEDGER.search(key) or _MEDIA.search(key))
    ]
    assert not bad, f"{rel_path}: payload keys that a SIEM scan would flag: {bad}"


def test_the_event_catalogue_promises_codes_only_for_biometric_events() -> None:  # QA-035
    events = (yaml.safe_load(CONTRACTS.read_text(encoding="utf-8")) or {}).get("events") or {}
    biometric = {
        k: v for k, v in events.items() if "biometric" in k or k.startswith("privacy.erasure")
    }
    assert {
        "verification.biometric.region_blocked",
        "verification.biometric.policy_blocked",
        "privacy.erasure.deletion_requested",
        "privacy.erasure.deletion_failed",
    } <= set(biometric)
    for name, e in biometric.items():
        meaning = str(e.get("meaning", "")).lower()
        assert "only" in meaning or "never" in meaning, f"{name} does not say what it leaves out"
