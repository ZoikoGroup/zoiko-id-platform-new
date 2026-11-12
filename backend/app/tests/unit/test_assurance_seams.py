"""The Provider Lab, parity, registry drift and the pinned vector loader (ZID-UAT-005 WF-04; CR-074 slice S7). No outside source is contacted."""

from __future__ import annotations

import hashlib
from datetime import UTC, date, datetime
from pathlib import Path

import pytest
import yaml
from zid_platform.modules.assurance.application import provider_lab, vectors
from zid_platform.modules.assurance.domain import provider_watch as pw

NOW = datetime(2026, 11, 12, 12, 0, tzinfo=UTC)
TODAY = NOW.date()


def test_every_documented_fault_is_held_by_the_platforms_own_guards() -> None:
    rows = provider_lab.run_all(NOW)
    faults = {r["fault"] for r in rows}
    assert {
        "BAD_HMAC", "REPLAY", "DUPLICATE", "STALE_TIMESTAMP", "MALFORMED_HEADERS", "WRONG_MARKET", "STALE_SUPPLIER_AUTHORIZATION",
        "SUSPENDED_ENTITLEMENT", "FAILOVER_THAT_LOWERS_ASSURANCE", "TIMEOUT", "HTTP_429", "HTTP_502",
    } <= faults  # fmt: skip
    broken = [r for r in rows if not r["held"]]
    assert broken == [], broken


def test_the_lab_has_a_control_that_would_fail_if_the_guards_refused_everything() -> None:
    control = next(r for r in provider_lab.run_all(NOW) if r["fault"] == "CONTROL_VALID_DELIVERY")
    assert control["observed"] == "ACCEPTED" and control["held"] is True


def test_a_timeout_or_error_never_becomes_a_result() -> None:
    for r in provider_lab.run_all(NOW):
        if r["fault"] in ("TIMEOUT", "HTTP_429", "HTTP_502"):
            assert r["observed"] == "HUMAN_REVIEW"


def test_parity_names_every_difference_and_flags_lower_assurance() -> None:
    ref = {"outcome": "MATCH", "document_type": "passport", "country": "GB", "assurance": 4}
    same = pw.parity(ref, dict(ref), keys=["outcome", "document_type", "country"])
    assert same["failover_safe"] is True and same["different"] == []
    off = pw.parity(
        ref,
        {"outcome": "MATCH", "document_type": "id_card", "assurance": 2},
        keys=["outcome", "document_type", "country"],
    )
    assert off["different"] == ["document_type", "country"], (
        "a key missing on one side is a difference"
    )
    assert (
        off["assurance_lower"] is True
        and off["failover_safe"] is False
        and len(off["explain"]) == 2
    )
    assert pw.parity(ref, {**ref, "assurance": 5}, keys=["outcome"])["failover_safe"] is True
    assert (
        pw.parity(ref, {k: v for k, v in ref.items() if k != "assurance"}, keys=["outcome"])[
            "assurance_lower"
        ]
        is True
    )


def svc(**o) -> pw.Service:
    base = {
        "provider_id": "acme",
        "service_code": "rtr",
        "status": "REGISTERED",
        "valid_until": date(2027, 1, 1),
        "scheme_version": "1.1",
    }
    return pw.Service(**{**base, **o})


def snap(*services: pw.Service) -> dict:
    return {s.key: s for s in services}


def test_registry_drift_is_found_and_the_exact_route_is_closed() -> None:
    before = snap(svc(), svc(provider_id="zeta"))
    after = snap(svc(status="SUSPENDED"), svc(provider_id="newco"))
    changes = pw.diff(before, after, today=TODAY)
    kinds = {(c.kind, c.provider_id) for c in changes}
    assert kinds == {("STATUS_CHANGED", "acme"), ("ADDED", "newco"), ("REMOVED", "zeta")}
    assert sorted(pw.disable_actions(changes, after)) == [
        ("acme", "rtr", "STATUS_CHANGED"),
        ("zeta", "rtr", "REMOVED"),
    ]
    assert "newco:rtr" not in pw.pins_to_expire(changes) and "acme:rtr" in pw.pins_to_expire(
        changes
    )


def test_a_service_past_its_date_or_on_a_new_scheme_is_reported() -> None:
    before = snap(svc())
    expired = snap(svc(valid_until=date(2026, 11, 1)))
    c = pw.diff(before, expired, today=TODAY)
    assert [x.kind for x in c] == ["EXPIRED"] and pw.disable_actions(c, expired) == [
        ("acme", "rtr", "EXPIRED")
    ]
    newer = snap(svc(scheme_version="1.2"))
    c2 = pw.diff(before, newer, today=TODAY)
    assert [x.kind for x in c2] == ["SCHEME_VERSION_CHANGED"] and pw.disable_actions(
        c2, newer
    ) == [], "a new scheme closes nothing itself but expires the pins"
    assert pw.pins_to_expire(c2) == ["acme:rtr"]
    assert pw.diff(before, snap(svc()), today=TODAY) == []


def pin(directory: Path, name: str, data: bytes, *, wrong: bool = False) -> dict:
    (directory / name).write_bytes(data)
    return {"path": name, "sha256": "0" * 64 if wrong else hashlib.sha256(data).hexdigest()}


def test_vectors_load_only_when_every_pinned_file_matches_and_nothing_unpinned_is_used(
    tmp_path: Path,
) -> None:
    files = [pin(tmp_path, "a.json", b'{"v":1}'), pin(tmp_path, "b.xml", b"<x/>")]
    (tmp_path / "MANIFEST.yaml").write_text(
        yaml.safe_dump({"source": "test set", "files": files}), encoding="utf-8"
    )
    (tmp_path / "stray.txt").write_bytes(b"not pinned")
    got = vectors.load(tmp_path)
    assert (
        set(got["vectors"]) == {"a.json", "b.xml"}
        and got["unpinned"] == ["stray.txt"]
        and got["source"] == "test set"
    )
    assert b"not pinned" not in b"".join(got["vectors"].values())


def test_a_changed_missing_or_escaping_vector_is_refused(tmp_path: Path) -> None:
    with pytest.raises(vectors.VectorError, match="MANIFEST_MISSING"):
        vectors.load(tmp_path)
    files = [pin(tmp_path, "a.json", b"x", wrong=True)]
    (tmp_path / "MANIFEST.yaml").write_text(yaml.safe_dump({"files": files}), encoding="utf-8")
    with pytest.raises(vectors.VectorError, match="DIGEST_MISMATCH"):
        vectors.load(tmp_path)
    (tmp_path / "MANIFEST.yaml").write_text(
        yaml.safe_dump({"files": [{"path": "gone.json", "sha256": "0" * 64}]}), encoding="utf-8"
    )
    with pytest.raises(vectors.VectorError, match="PINNED_FILE_MISSING"):
        vectors.load(tmp_path)
    (tmp_path / "MANIFEST.yaml").write_text(
        yaml.safe_dump({"files": [{"path": "../outside.json", "sha256": "0" * 64}]}),
        encoding="utf-8",
    )
    with pytest.raises(vectors.VectorError, match="PATH_ESCAPES"):
        vectors.load(tmp_path)
