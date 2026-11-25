#!/usr/bin/env python3
"""Release cell, evidence ledger, independent sign-off, claims interlock and signed release token (ZID-ENG-REL-002; REL-01, 02, 04, 05, 12, 13).

    release_cell.py fingerprint CELL.json [--config FILE ...]     the fingerprint of one release cell (stdout), with the config files hashed in
    release_cell.py drift      CELL.json FINGERPRINT              exit 1 if the cell no longer matches a recorded fingerprint
    release_cell.py ledger-verify LEDGER.json                     exit 1 if any entry was changed, removed or reordered
    release_cell.py issue  --cell CELL.json --ledger L.json --signoffs S.json --claims MONITORING.json --key PRIVATE.pem [--ttl-hours 24] > TOKEN.json
    release_cell.py verify --token TOKEN.json --cell CELL.json --public PUBLIC.pem

What it adds to the existing pieces: `release_gate.py` decides whether an artifact may be promoted (signature, provenance, SBOM, findings); `trace_workbench.py assess` decides G0 to G4 for a release tuple.
This module binds them to ONE exact cell: any change to the build, configuration, policy, providers, market, region, cohort or window changes the fingerprint, which voids the evidence, the sign-offs and the token.

Standard library, plus `cryptography` for the Ed25519 token. It holds no key, reads no network, and a token is issued only when every condition holds.
"""

from __future__ import annotations

import base64
import hashlib
import json
import sys
from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import Any

sys.path.insert(0, str(Path(__file__).resolve().parent))
import trace_workbench as tw

CELL_FIELDS = ("product_id", "release_id", "code_sha", "artifact_digest", "schema_revision", "config_hash", "policy_versions", "providers", "market", "region", "tenant_cohort", "window_start", "window_end")
ASSESSOR_ROLES = ("assessor_security", "assessor_qa", "assessor_privacy")  # independent: never the implementer
FUNCTIONAL_ROLES = ("release_manager", "executive")
MAX_CLAIMS_REPORT_AGE = timedelta(hours=24)


def _canon(v: Any) -> bytes:
    return json.dumps(v, sort_keys=True, separators=(",", ":"), ensure_ascii=False).encode()


def _sha(b: bytes) -> str:
    return hashlib.sha256(b).hexdigest()


# ---------------------------------------------------------------------------------------------------- REL-01 the cell fingerprint


def config_hash(files: Mapping[str, bytes]) -> str:
    """The hash of the exact configuration in force: every named file, by path and content, sorted."""
    return _sha(_canon(sorted((p, _sha(c)) for p, c in files.items())))


def missing_fields(cell: Mapping[str, Any]) -> list[str]:
    return [f for f in CELL_FIELDS if cell.get(f) in (None, "", [], {})]


def fingerprint(cell: Mapping[str, Any]) -> str:
    """Deterministic over the whole tuple. A missing field is an error, never a default: a cell with an unknown dimension has no fingerprint, so it can never be approved."""
    gaps = missing_fields(cell)
    if gaps:
        raise ValueError("the release cell is incomplete: " + ", ".join(gaps))
    body = {f: (sorted(cell[f]) if f == "providers" else cell[f]) for f in CELL_FIELDS}
    return _sha(_canon(body))


def drifted(recorded: str, cell: Mapping[str, Any]) -> bool:
    try:
        return fingerprint(cell) != recorded
    except ValueError:
        return True


# ---------------------------------------------------------------------------------------------------- REL-02 the evidence ledger


GENESIS = "0" * 64


def _entry_hash(e: Mapping[str, Any]) -> str:
    return _sha(_canon({k: e[k] for k in ("seq", "artifact", "recorded_by", "at", "prev")}))


def append(ledger: Sequence[Mapping[str, Any]], *, artifact_id: str, kind: str, digest: str, cell_fingerprint: str, recorded_by: str, at: datetime) -> list[dict[str, Any]]:
    """Add one artifact. The ledger is append-only: an artifact id already recorded with another digest is refused, and every entry commits to the one before it."""
    if len(digest) != 64 or any(c not in "0123456789abcdef" for c in digest):
        raise ValueError("the digest must be 64 hex characters")
    for e in ledger:
        if e["artifact"]["id"] == artifact_id:
            if e["artifact"]["digest"] != digest:
                raise ValueError(f"{artifact_id} is already recorded with a different digest; record new evidence under a new id")
            return [dict(x) for x in ledger]
    prev = ledger[-1]["entry_hash"] if ledger else GENESIS
    e: dict[str, Any] = {"seq": len(ledger) + 1, "artifact": {"id": artifact_id, "kind": kind, "digest": digest, "cell": cell_fingerprint}, "recorded_by": recorded_by, "at": at.isoformat(), "prev": prev}
    e["entry_hash"] = _entry_hash(e)
    return [*[dict(x) for x in ledger], e]


def verify_chain(ledger: Sequence[Mapping[str, Any]]) -> list[str]:
    """Any edited, removed, inserted or reordered entry breaks the chain from that point."""
    errors, prev = [], GENESIS
    for i, e in enumerate(ledger, 1):
        if e.get("seq") != i:
            errors.append(f"entry {i}: sequence is {e.get('seq')}")
        if e.get("prev") != prev:
            errors.append(f"entry {i}: does not follow the entry before it")
        if e.get("entry_hash") != _entry_hash(e):
            errors.append(f"entry {i}: its content was changed")
        prev = str(e.get("entry_hash"))
    return errors


def head(ledger: Sequence[Mapping[str, Any]]) -> str:
    return str(ledger[-1]["entry_hash"]) if ledger else GENESIS


def evidence_for(ledger: Sequence[Mapping[str, Any]], fp: str) -> list[Mapping[str, Any]]:
    """Only artifacts recorded for this exact cell count; evidence for another cell is not reusable."""
    return [e for e in ledger if e["artifact"]["cell"] == fp]


# ---------------------------------------------------------------------------------------------------- REL-05 independent sign-off


@dataclass(frozen=True)
class Signoff:
    role: str
    person: str
    fingerprint: str
    at: str


def signoff_problems(signoffs: Sequence[Signoff], fp: str, implementers: Sequence[str]) -> list[str]:
    """Sign-offs count only for this fingerprint, from people who did not build it, once each. Every assessor role and every functional role must be present."""
    out: list[str] = []
    live = [s for s in signoffs if s.fingerprint == fp]
    stale = len(signoffs) - len(live)
    if stale:
        out.append(f"{stale} sign-off(s) were given for a different cell and no longer count")
    people = [s.person for s in live]
    if len(set(people)) < len(people):
        out.append("one person signed in more than one role")
    for s in live:
        if s.person in implementers:
            out.append(f"{s.role}: {s.person} worked on this release and cannot sign it")
    have = {s.role for s in live}
    for r in (*ASSESSOR_ROLES, *FUNCTIONAL_ROLES):
        if r not in have:
            out.append(f"{r} has not signed")
    return out


# ---------------------------------------------------------------------------------------------------- REL-13 the claims interlock


def claims_problems(monitoring: Mapping[str, Any] | None, now: datetime) -> list[str]:
    """The Trust Center monitoring report must be fresh, the claims gate open and no live claim unsupported. A missing report blocks: silence is not assurance."""
    if not monitoring:
        return ["no Trust Center monitoring report: claims status is unknown"]
    out: list[str] = []
    try:
        age = now - datetime.fromisoformat(str(monitoring["as_of"]))
        if age > MAX_CLAIMS_REPORT_AGE:
            out.append("the Trust Center monitoring report is older than 24 hours")
    except (KeyError, ValueError):
        out.append("the Trust Center monitoring report has no valid time")
    if int(monitoring.get("unsupported_live_claims", 1)):
        out.append(f"{monitoring.get('unsupported_live_claims')} live public claim(s) have no current support")
    gate = monitoring.get("release_gate") or {}
    if not gate.get("open", False):
        out.append("the Trust Center release gate is closed: " + "; ".join(gate.get("reasons", [])) if gate.get("reasons") else "the Trust Center release gate is closed")
    return out


# ---------------------------------------------------------------------------------------------------- REL-04 and REL-12 decision and token


def release_problems(*, cell: Mapping[str, Any], ledger: Sequence[Mapping[str, Any]], signoffs: Sequence[Signoff], monitoring: Mapping[str, Any] | None, manifest: tw.Manifest, implementers: Sequence[str], now: datetime) -> list[str]:  # fmt: skip
    """Every reason this cell may not be released. Empty means it may. The same function runs when a token is issued, so there is no second, weaker path."""
    try:
        fp = fingerprint(cell)
    except ValueError as e:
        return [str(e)]
    out = [f"evidence ledger: {e}" for e in verify_chain(ledger)]
    if not evidence_for(ledger, fp):
        out.append("no evidence is recorded for this exact cell")
    out += signoff_problems(signoffs, fp, implementers)
    out += claims_problems(monitoring, now)
    for g in tw.assess(manifest):
        out += [f"{g.gate}: {b}" for b in g.blockers]
    return out


def issue_token(*, problems: Sequence[str], cell: Mapping[str, Any], ledger: Sequence[Mapping[str, Any]], signoffs: Sequence[Signoff], private_pem: bytes, now: datetime, ttl: timedelta = timedelta(hours=24)) -> dict[str, Any]:  # fmt: skip
    """Refuses unless there is nothing blocking. The token names the fingerprint, the ledger head and the signers, and expires."""
    if problems:
        raise PermissionError("not releasable: " + "; ".join(problems))
    from cryptography.hazmat.primitives import serialization
    from cryptography.hazmat.primitives.asymmetric import ed25519

    key = serialization.load_pem_private_key(private_pem, password=None)
    if not isinstance(key, ed25519.Ed25519PrivateKey):
        raise TypeError("the signing key must be an Ed25519 private key")
    body = {"fingerprint": fingerprint(cell), "ledger_head": head(ledger), "issued_at": now.isoformat(), "expires_at": (now + ttl).isoformat(), "signers": sorted({s.person for s in signoffs}), "market": cell["market"], "region": cell["region"]}  # fmt: skip
    return {"token": body, "signature": base64.urlsafe_b64encode(key.sign(_canon(body))).decode().rstrip("=")}


def verify_token(token: Mapping[str, Any], public_pem: bytes, cell_now: Mapping[str, Any], ledger: Sequence[Mapping[str, Any]], now: datetime) -> list[str]:
    """What the deploy step checks. A stale, forged, altered or drifted token is refused, with the reason."""
    from cryptography.exceptions import InvalidSignature
    from cryptography.hazmat.primitives import serialization

    body = token.get("token", {})
    try:
        pad = "=" * (-len(str(token.get("signature", ""))) % 4)
        serialization.load_pem_public_key(public_pem).verify(base64.urlsafe_b64decode(str(token.get("signature", "")) + pad), _canon(body))  # type: ignore[call-arg,union-attr]
    except (InvalidSignature, ValueError, TypeError):
        return ["the token signature is not valid"]
    out: list[str] = []
    if datetime.fromisoformat(body["expires_at"]) <= now:
        out.append("the token has expired")
    if drifted(body["fingerprint"], cell_now):
        out.append("the release cell no longer matches the approved fingerprint: build, configuration, policy, provider, market, region, cohort or window changed")
    if head(ledger) != body["ledger_head"]:
        out.append("the evidence ledger changed after approval")
    if body.get("market") != cell_now.get("market") or body.get("region") != cell_now.get("region"):
        out.append("the token is for another market or region")
    return out


# ---------------------------------------------------------------------------------------------------- command line


def _load(p: str) -> Any:
    return json.loads(Path(p).read_text(encoding="utf-8"))


def _arg(a: list[str], flag: str, default: str | None = None) -> str | None:
    return a[a.index(flag) + 1] if flag in a else default


def main(argv: list[str] | None = None) -> int:
    a = list(argv if argv is not None else sys.argv[1:])
    cmd = a[0] if a else ""
    if cmd == "fingerprint":
        cell = _load(a[1])
        files = [a[i + 1] for i, x in enumerate(a) if x == "--config"]
        if files:
            cell["config_hash"] = config_hash({f: Path(f).read_bytes() for f in files})
        print(fingerprint(cell))
        return 0
    if cmd == "drift":
        bad = drifted(a[2], _load(a[1]))
        print("DRIFTED" if bad else "MATCHES")
        return 1 if bad else 0
    if cmd == "ledger-verify":
        errs = verify_chain(_load(a[1]))
        for e in errs:
            print(e)
        return 1 if errs else 0
    if cmd in ("issue", "verify"):
        now = datetime.now(UTC)
        if cmd == "verify":
            errs = verify_token(_load(_arg(a, "--token") or ""), Path(_arg(a, "--public") or "").read_bytes(), _load(_arg(a, "--cell") or ""), _load(_arg(a, "--ledger") or "") if _arg(a, "--ledger") else [], now)
            for e in errs:
                print(e)
            return 1 if errs else 0
        cell, ledger = _load(_arg(a, "--cell") or ""), _load(_arg(a, "--ledger") or "")
        raw = _load(_arg(a, "--signoffs") or "")
        signoffs = [Signoff(**s) for s in raw["signoffs"]]
        manifest = tw.manifest_from_json(raw["manifest"])
        problems = release_problems(cell=cell, ledger=ledger, signoffs=signoffs, monitoring=_load(_arg(a, "--claims") or "") if _arg(a, "--claims") else None, manifest=manifest, implementers=raw.get("implementers", []), now=now)
        try:
            tok = issue_token(problems=problems, cell=cell, ledger=ledger, signoffs=signoffs, private_pem=Path(_arg(a, "--key") or "").read_bytes(), now=now, ttl=timedelta(hours=float(_arg(a, "--ttl-hours", "24") or 24)))
        except PermissionError as e:
            print(e, file=sys.stderr)
            return 1
        print(json.dumps(tok, indent=1))
        return 0
    print(__doc__)
    return 2


if __name__ == "__main__":
    raise SystemExit(main())
