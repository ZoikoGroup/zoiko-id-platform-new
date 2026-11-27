"""Release cell, evidence ledger, independent sign-off, claims interlock and token (ZID-ENG-REL-002; REL-01, 02, 04, 05, 12, 13)."""

from __future__ import annotations

import importlib.util
import sys
from datetime import UTC, datetime, timedelta
from pathlib import Path

import pytest

TOOLS = Path(__file__).resolve().parents[4] / "tools" / "scripts"
sys.path.insert(0, str(TOOLS))
SPEC = importlib.util.spec_from_file_location("release_cell", TOOLS / "release_cell.py")
rc = importlib.util.module_from_spec(SPEC)  # type: ignore[arg-type]
sys.modules["release_cell"] = rc
SPEC.loader.exec_module(rc)  # type: ignore[union-attr]
import trace_workbench as tw

NOW = datetime(2026, 11, 25, 12, 0, tzinfo=UTC)
D = "a" * 64


def cell(**kw: object) -> dict[str, object]:
    base: dict[str, object] = {
        "product_id": "verify_api", "release_id": "R1", "code_sha": "abc123", "artifact_digest": "sha256:" + D, "schema_revision": "0018", "config_hash": "c" * 64,
        "policy_versions": {"workflow": "3"}, "providers": ["p2", "p1"], "market": "US", "region": "us-1", "tenant_cohort": "design-partner", "window_start": "2026-12-01", "window_end": "2027-03-01",
    }  # fmt: skip
    base.update(kw)
    return base


def manifest() -> tw.Manifest:
    return tw.Manifest(release_id="R1", market="US", tenant_class="design-partner", provider="p1", method="doc", build_digest="sha256:" + D, approved_markets=("US",), dtv_missing=0, partner_e2e_signed_webhook=True,
                       signatures={"release_manager": "rita", "qa": "quinn", "security": "sam", "privacy_legal": "lee"}, implementer="ivan", rollback_rehearsed=True)  # fmt: skip


def signoffs(fp: str) -> list[object]:
    roles = {
        "assessor_security": "sam",
        "assessor_qa": "quinn",
        "assessor_privacy": "lee",
        "release_manager": "rita",
        "executive": "eve",
    }
    return [rc.Signoff(r, p, fp, NOW.isoformat()) for r, p in roles.items()]


GOOD_CLAIMS = {
    "as_of": NOW.isoformat(),
    "unsupported_live_claims": 0,
    "release_gate": {"open": True, "reasons": []},
}


def ledger_for(fp: str) -> list[dict[str, object]]:
    return rc.append(
        [],
        artifact_id="pen-test",
        kind="report",
        digest=D,
        cell_fingerprint=fp,
        recorded_by="quinn",
        at=NOW,
    )


def problems(c: dict[str, object], **kw: object) -> list[str]:
    fp = rc.fingerprint(cell())
    args: dict[str, object] = {
        "cell": c,
        "ledger": ledger_for(fp),
        "signoffs": signoffs(fp),
        "monitoring": GOOD_CLAIMS,
        "manifest": manifest(),
        "implementers": ["ivan"],
        "now": NOW,
    }
    args.update(kw)
    return rc.release_problems(**args)  # type: ignore[arg-type]


def test_the_fingerprint_is_stable_order_free_for_providers_and_changes_with_any_dimension() -> (
    None
):
    fp = rc.fingerprint(cell())
    assert fp == rc.fingerprint(cell(providers=["p1", "p2"]))
    for field, value in (
        ("code_sha", "zzz"),
        ("config_hash", "d" * 64),
        ("market", "UK"),
        ("region", "eu-1"),
        ("tenant_cohort", "general"),
        ("providers", ["p1"]),
        ("policy_versions", {"workflow": "4"}),
        ("window_end", "2027-04-01"),
    ):
        assert rc.fingerprint(cell(**{field: value})) != fp, field


def test_a_cell_with_an_unknown_dimension_has_no_fingerprint_and_counts_as_drifted() -> None:
    with pytest.raises(ValueError, match="provider"):
        rc.fingerprint(cell(providers=[]))
    assert rc.drifted("x", cell(market=""))
    assert not rc.drifted(rc.fingerprint(cell()), cell())


def test_the_config_hash_covers_every_file_and_its_content() -> None:
    a = rc.config_hash({"a.yaml": b"1", "b.yaml": b"2"})
    assert a == rc.config_hash({"b.yaml": b"2", "a.yaml": b"1"})
    assert a != rc.config_hash({"a.yaml": b"1", "b.yaml": b"3"}) and a != rc.config_hash(
        {"a.yaml": b"1"}
    )


def test_the_ledger_is_append_only_and_any_edit_removal_or_reorder_is_found() -> None:
    fp = rc.fingerprint(cell())
    led = ledger_for(fp)
    led = rc.append(
        led,
        artifact_id="dpia",
        kind="assessment",
        digest="b" * 64,
        cell_fingerprint=fp,
        recorded_by="lee",
        at=NOW,
    )
    led = rc.append(
        led,
        artifact_id="sca",
        kind="scan",
        digest="c" * 64,
        cell_fingerprint=fp,
        recorded_by="quinn",
        at=NOW,
    )
    assert rc.verify_chain(led) == []
    edited = [dict(e) for e in led]
    edited[1] = {**edited[1], "artifact": {**edited[1]["artifact"], "digest": "f" * 64}}
    assert rc.verify_chain(edited)
    assert rc.verify_chain([led[0], led[2]]) and rc.verify_chain([led[1], led[0], led[2]])


def test_an_artifact_id_cannot_be_overwritten_with_other_bytes_and_a_repeat_is_idempotent() -> None:
    fp = rc.fingerprint(cell())
    led = ledger_for(fp)
    with pytest.raises(ValueError, match="different digest"):
        rc.append(
            led,
            artifact_id="pen-test",
            kind="report",
            digest="b" * 64,
            cell_fingerprint=fp,
            recorded_by="x",
            at=NOW,
        )
    assert (
        len(
            rc.append(
                led,
                artifact_id="pen-test",
                kind="report",
                digest=D,
                cell_fingerprint=fp,
                recorded_by="x",
                at=NOW,
            )
        )
        == 1
    )
    with pytest.raises(ValueError):
        rc.append(
            led,
            artifact_id="n",
            kind="r",
            digest="short",
            cell_fingerprint=fp,
            recorded_by="x",
            at=NOW,
        )


def test_evidence_for_another_cell_does_not_count() -> None:
    other = rc.fingerprint(cell(market="UK"))
    assert rc.evidence_for(ledger_for(other), rc.fingerprint(cell())) == []
    assert "no evidence is recorded for this exact cell" in problems(
        cell(), ledger=ledger_for(other)
    )


def test_a_fully_signed_evidenced_clean_cell_has_no_problems() -> None:
    assert problems(cell()) == []


def test_signoffs_for_an_older_cell_do_not_count_the_implementer_cannot_sign_and_every_role_is_needed() -> (
    None
):
    fp = rc.fingerprint(cell())
    old = rc.fingerprint(cell(code_sha="old"))
    assert any("different cell" in p for p in problems(cell(), signoffs=signoffs(old)))
    swapped = [
        rc.Signoff("assessor_qa", "ivan", fp, "t") if s.role == "assessor_qa" else s
        for s in signoffs(fp)
    ]
    assert any("cannot sign" in p for p in problems(cell(), signoffs=swapped))
    assert any(
        "more than one role" in p
        for p in problems(cell(), signoffs=[*signoffs(fp), rc.Signoff("executive", "sam", fp, "t")])
    )
    assert any(
        "assessor_privacy has not signed" in p
        for p in problems(
            cell(), signoffs=[s for s in signoffs(fp) if s.role != "assessor_privacy"]
        )
    )


def test_unsupported_public_claims_a_closed_trust_gate_a_stale_or_missing_report_each_block_a_release() -> (
    None
):
    bad = {
        **GOOD_CLAIMS,
        "unsupported_live_claims": 2,
        "release_gate": {"open": False, "reasons": ["2 live claim(s) have no current support."]},
    }
    assert any("no current support" in p for p in problems(cell(), monitoring=bad))
    assert any("closed" in p for p in problems(cell(), monitoring=bad))
    assert any(
        "older than 24 hours" in p
        for p in problems(
            cell(), monitoring={**GOOD_CLAIMS, "as_of": (NOW - timedelta(days=2)).isoformat()}
        )
    )
    assert any("unknown" in p for p in problems(cell(), monitoring=None))


def test_a_blocked_workbench_gate_blocks_the_release_and_a_tampered_ledger_does() -> None:
    m = manifest()
    m.cross_tenant_failures = 1
    assert any(p.startswith("G1:") for p in problems(cell(), manifest=m))
    led = ledger_for(rc.fingerprint(cell()))
    led[0] = {**led[0], "recorded_by": "mallory"}
    assert any("evidence ledger" in p for p in problems(cell(), ledger=led))


def keypair() -> tuple[bytes, bytes]:
    from cryptography.hazmat.primitives import serialization
    from cryptography.hazmat.primitives.asymmetric import ed25519

    k = ed25519.Ed25519PrivateKey.generate()
    return (
        k.private_bytes(
            serialization.Encoding.PEM,
            serialization.PrivateFormat.PKCS8,
            serialization.NoEncryption(),
        ),
        k.public_key().public_bytes(
            serialization.Encoding.PEM, serialization.PublicFormat.SubjectPublicKeyInfo
        ),
    )


def test_a_token_is_refused_while_anything_blocks_and_issued_when_nothing_does() -> None:
    priv, _ = keypair()
    fp = rc.fingerprint(cell())
    with pytest.raises(PermissionError, match="not releasable"):
        rc.issue_token(
            problems=["x"],
            cell=cell(),
            ledger=ledger_for(fp),
            signoffs=[],
            private_pem=priv,
            now=NOW,
        )
    tok = rc.issue_token(
        problems=problems(cell()),
        cell=cell(),
        ledger=ledger_for(fp),
        signoffs=signoffs(fp),
        private_pem=priv,
        now=NOW,
    )
    assert tok["token"]["fingerprint"] == fp and tok["token"]["signers"] == [
        "eve",
        "lee",
        "quinn",
        "rita",
        "sam",
    ]


def test_the_deploy_check_refuses_a_forged_expired_drifted_or_re_evidenced_token() -> None:
    priv, pub = keypair()
    _, other_pub = keypair()
    fp = rc.fingerprint(cell())
    led = ledger_for(fp)
    tok = rc.issue_token(
        problems=[], cell=cell(), ledger=led, signoffs=signoffs(fp), private_pem=priv, now=NOW
    )
    assert rc.verify_token(tok, pub, cell(), led, NOW + timedelta(hours=1)) == []
    assert rc.verify_token(tok, other_pub, cell(), led, NOW) == ["the token signature is not valid"]
    assert "the token has expired" in rc.verify_token(
        tok, pub, cell(), led, NOW + timedelta(hours=25)
    )
    assert any(
        "no longer matches" in p
        for p in rc.verify_token(tok, pub, cell(artifact_digest="sha256:" + "9" * 64), led, NOW)
    )
    assert any(
        "another market" in p for p in rc.verify_token(tok, pub, cell(market="UK"), led, NOW)
    )
    more = rc.append(
        led,
        artifact_id="late",
        kind="scan",
        digest="d" * 64,
        cell_fingerprint=fp,
        recorded_by="quinn",
        at=NOW,
    )
    assert "the evidence ledger changed after approval" in rc.verify_token(
        tok, pub, cell(), more, NOW
    )
    forged = {
        **tok,
        "token": {**tok["token"], "expires_at": (NOW + timedelta(days=900)).isoformat()},
    }
    assert rc.verify_token(forged, pub, cell(), led, NOW) == ["the token signature is not valid"]
