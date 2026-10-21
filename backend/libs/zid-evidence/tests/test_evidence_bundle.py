"""Hash chain, signed checkpoints and offline bundle verification (EVID-05/06/07/18/21)."""

from __future__ import annotations

import copy
import json
from dataclasses import replace
from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import Any

import pytest
from zid_crypto import LocalSigner, sign_jws
from zid_evidence import (
    CHECKPOINT_TYP,
    CheckpointManifest,
    Digest,
    LedgerEntry,
    SignatureError,
    batch_root,
    build_bundle,
    entry_hash,
    format_time,
    hash_json,
    merkle,
    verify_bundle,
    verify_chain,
    verify_checkpoint_signature,
)
from zid_evidence.__main__ import main as cli
from zid_evidence.checkpoint import entry_leaf

ALG = "sha-256"
T0 = datetime(2026, 10, 21, 9, 0, tzinfo=UTC)


def make_chain(n: int, partition: str = "tenant:t1:uk-1") -> list[tuple[dict[str, Any], LedgerEntry, Digest]]:
    out: list[tuple[dict[str, Any], LedgerEntry, Digest]] = []
    previous: Digest | None = None
    for i in range(1, n + 1):
        record = {"receipt_id": f"r{i}", "outcome": "ALLOW", "amount": i * 1.5}
        entry = LedgerEntry(
            partition_id=partition,
            sequence_no=i,
            record_id=f"r{i}",
            record_type="decision_receipt",
            record_hash=hash_json(record, alg=ALG),
            previous_entry_hash=previous,
            committed_at=format_time(T0 + timedelta(seconds=i)),
            evidence_class="E3",
        )
        previous = entry_hash(entry, alg=ALG)
        out.append((record, entry, previous))
    return out


def checkpoint(
    chain: list[tuple[dict[str, Any], LedgerEntry, Digest]], signer: LocalSigner
) -> tuple[CheckpointManifest, str]:
    manifest = CheckpointManifest(
        checkpoint_id="cp-1",
        partition_id=chain[0][1].partition_id,
        first_sequence=1,
        last_sequence=len(chain),
        merkle_root=batch_root([h for _, _, h in chain], alg=ALG),
        previous_checkpoint_hash=None,
        created_at=format_time(T0 + timedelta(minutes=1)),
        signer_key_id=signer.kid,
        alg=signer.alg,
    )
    return manifest, sign_jws(signer, manifest.to_dict(), typ=CHECKPOINT_TYP)


def bundle_for(index: int, signer: LocalSigner, n: int = 5) -> dict[str, Any]:
    chain = make_chain(n)
    manifest, sig = checkpoint(chain, signer)
    record, entry, digest = chain[index]
    proof = merkle.inclusion_proof(index, [entry_leaf(h) for _, _, h in chain], alg=ALG)
    return build_bundle(
        record_type=entry.record_type,
        record_id=entry.record_id,
        record=record,
        entry=entry,
        entry_digest=digest,
        manifest=manifest,
        checkpoint_signature=sig,
        proof=proof,
        keys=[signer.public_jwk()],
    )


def test_chain_detects_modify_delete_and_reorder() -> None:
    chain = [(e, h) for _, e, h in make_chain(6)]
    assert verify_chain(chain) is None
    modified = list(chain)
    e3, h3 = modified[2]
    modified[2] = (replace(e3, record_hash=hash_json({"forged": True}, alg=ALG)), h3)
    brk = verify_chain(modified)
    assert brk is not None and brk.sequence_no == 3
    deleted = chain[:2] + chain[3:]
    brk = verify_chain(deleted)
    assert brk is not None and brk.sequence_no == 3
    reordered = [chain[0], chain[2], chain[1], *chain[3:]]
    brk = verify_chain(reordered)
    assert brk is not None and brk.sequence_no == 2


@pytest.mark.parametrize("n", [1, 2, 5, 8])
def test_bundle_verifies_offline_for_every_entry(n: int) -> None:
    signer = LocalSigner("evidence-k1")
    for i in range(n):
        b = bundle_for(i, signer, n)
        report = verify_bundle(json.loads(json.dumps(b)), trusted_keys=[signer.public_jwk()])
        assert report.ok, report.to_dict()
        assert report.keys_source == "trusted" and not report.structural_only


def test_bundle_without_record_is_structural_only() -> None:
    signer = LocalSigner("evidence-k1")
    b = bundle_for(2, signer)
    b["record"] = None
    report = verify_bundle(b)
    assert report.ok and report.structural_only and report.keys_source == "bundle"


def _tamper(b: dict[str, Any], path: str, value: Any) -> dict[str, Any]:
    out = copy.deepcopy(b)
    node = out
    *parents, last = path.split(".")
    for p in parents:
        node = node[p]
    node[last] = value
    return out


@pytest.mark.parametrize(
    ("path", "value", "failed"),
    [
        ("record.outcome", "DENY", "record_hash"),
        ("ledger_entry.committed_at", "2030-01-01T00:00:00.000000Z", "entry_hash"),
        ("ledger_entry.evidence_class", "E1", "entry_hash"),
        ("checkpoint.merkle_root", "sha-256:" + "00" * 32, "inclusion_proof"),
        ("checkpoint.last_sequence", 4, "structure"),  # tree_size no longer matches
        ("checkpoint.tree_size", 4, "structure"),
        ("inclusion_proof.leaf_index", 1, "inclusion_proof"),
        ("checkpoint.created_at", "2030-01-01T00:00:00.000000Z", "checkpoint_signature"),
        ("record_id", "r9", "record_identity"),
    ],
)
def test_tampering_is_detected(path: str, value: Any, failed: str) -> None:
    signer = LocalSigner("evidence-k1")
    b = bundle_for(2, signer)
    report = verify_bundle(_tamper(b, path, value), trusted_keys=[signer.public_jwk()])
    assert not report.ok
    assert failed in {c.name for c in report.checks if not c.ok}, report.to_dict()


def test_root_and_signature_tampering_and_untrusted_keys() -> None:
    signer, attacker = LocalSigner("evidence-k1"), LocalSigner("evidence-k1")
    b = bundle_for(2, signer)
    # an attacker re-signs a forged manifest with their own key under the same kid
    forged = copy.deepcopy(b)
    chain = make_chain(5)
    chain[2] = ({"x": 1}, chain[2][1], chain[2][2])
    manifest, sig = checkpoint(chain, attacker)
    forged["checkpoint"]["signature"] = sig
    assert not verify_bundle(forged, trusted_keys=[signer.public_jwk()]).ok
    # a corrupted signature
    head, payload, s = b["checkpoint"]["signature"].split(".")
    bad = _tamper(b, "checkpoint.signature", f"{head}.{payload}.{s[:-4]}AAAA")
    assert not verify_bundle(bad, trusted_keys=[signer.public_jwk()]).ok
    # unknown key / no keys
    assert not verify_bundle(b, trusted_keys=[]).ok
    assert not verify_bundle(b, trusted_keys=[LocalSigner("other").public_jwk()]).ok
    # garbage never raises
    assert not verify_bundle({"format": "zid-evidence-bundle/1", "profile": {}}).ok
    assert not verify_bundle({}).ok
    assert not verify_bundle(_tamper(b, "profile.hash_alg", "md5")).ok


def test_verifies_across_a_key_rotation() -> None:
    old, new = LocalSigner("evidence-2026-01"), LocalSigner("evidence-2026-07", "Ed25519")
    before = bundle_for(1, old)
    after = bundle_for(3, new)
    retained = [old.public_jwk(), new.public_jwk()]  # historic keys stay published
    assert verify_bundle(before, trusted_keys=retained).ok
    assert verify_bundle(after, trusted_keys=retained).ok
    assert not verify_bundle(before, trusted_keys=[new.public_jwk()]).ok


def test_checkpoint_signature_must_match_manifest_exactly() -> None:
    signer = LocalSigner("k")
    chain = make_chain(3)
    manifest, sig = checkpoint(chain, signer)
    verify_checkpoint_signature(manifest, sig, keys=[signer.public_jwk()])
    other = replace(manifest, last_sequence=2)
    with pytest.raises(SignatureError):
        verify_checkpoint_signature(other, sig, keys=[signer.public_jwk()])
    wrong_typ = sign_jws(signer, manifest.to_dict(), typ="JWT")
    with pytest.raises(SignatureError):
        verify_checkpoint_signature(manifest, wrong_typ, keys=[signer.public_jwk()])


def test_cli_verifies_a_bundle_file(tmp_path: Path, capsys: pytest.CaptureFixture[str]) -> None:
    signer = LocalSigner("evidence-k1")
    path = tmp_path / "bundle.json"
    keys = tmp_path / "keys.json"
    path.write_text(json.dumps(bundle_for(0, signer)), encoding="utf-8")
    keys.write_text(json.dumps({"keys": [signer.public_jwk()]}), encoding="utf-8")
    assert cli(["verify", str(path), "--keys", str(keys)]) == 0
    assert json.loads(capsys.readouterr().out)["ok"] is True
    path.write_text(
        json.dumps(_tamper(bundle_for(0, signer), "record.outcome", "DENY")), encoding="utf-8"
    )
    assert cli(["verify", str(path)]) == 1
    path.write_text('{"a": 1, "a": 2}', encoding="utf-8")
    assert cli(["verify", str(path)]) == 2
