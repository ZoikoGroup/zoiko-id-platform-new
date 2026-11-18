"""The governed CSCA trust store (ZID-DTV-WF-01 section 13, screen O2; CR-107): stage, validate, independently approve, activate, roll back, retire.

What this adds to the importer that already existed (`evidence.infrastructure.csca_import`, an operator tool that writes files): a record of where each
list came from (URI, SHA-256 of the bytes, the anchors and their fingerprints, who staged it), a validation that can be repeated from the stored bytes, a
second person who must approve and activate, and a way back. Nothing becomes trusted by being imported. `effective_csca` is what a chip check uses:
the active package once the store governs, the deployment's csca folder before that.

Every state change is an outbox event carrying `evidence_class` E3 and the actor, so the evidence module's own ingest turns it into a protected audit
record (the same path every other evidence-bearing event takes). No certificate bytes and no master list are put in an event.
"""

from __future__ import annotations

import base64
import hashlib
import uuid
from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from datetime import datetime
from typing import Any

from cryptography import x509
from cryptography.hazmat.primitives.serialization import Encoding
from sqlalchemy.ext.asyncio import AsyncConnection
from zid_outbox import emit

from zid_platform.modules.evidence.application import epassport
from zid_platform.modules.evidence.application.csca_master_list import (
    MAX_BYTES,
    fingerprint,
    read_master_list,
)
from zid_platform.modules.evidence.domain import trust_store as rules
from zid_platform.modules.evidence.domain.trust_store import State, TrustStoreError
from zid_platform.modules.evidence.infrastructure import trust_package_store as store
from zid_platform.modules.evidence.infrastructure.source_policy import load as load_source_policy

MAX_ANCHORS = 5
_PUBLIC = (
    "package_id", "kind", "seq", "version", "state", "source_uri", "source_note", "package_sha256", "anchor_sha256", "list_status",
    "list_reasons", "list_signer", "certificate_count", "manifest_digest", "staged_by", "staged_at", "validated_at", "decided_by",
    "decided_at", "decision_reason", "activated_by", "activated_at", "ended_at", "ended_by", "ended_reason",
)  # fmt: skip


def view(row: Mapping[str, Any]) -> dict[str, Any]:
    """The package as an administrator may see it: no master list bytes, no anchor PEMs, no certificate bodies."""
    out: dict[str, Any] = {}
    for k in _PUBLIC:
        v = row.get(k)
        out[k] = (
            str(v) if isinstance(v, uuid.UUID) else v.isoformat() if isinstance(v, datetime) else v
        )
    return out


def _load_cert(data: bytes) -> x509.Certificate:
    return (
        x509.load_pem_x509_certificate(data)
        if b"BEGIN CERTIFICATE" in data
        else x509.load_der_x509_certificate(data)
    )


def _country(cert: x509.Certificate) -> str:
    attrs = cert.subject.get_attributes_for_oid(x509.NameOID.COUNTRY_NAME)
    code = "".join(ch for ch in str(attrs[0].value).upper() if "A" <= ch <= "Z") if attrs else ""
    return code[:3] or "XX"


async def _emit(
    conn: AsyncConnection,
    tenant_id: str,
    row: Mapping[str, Any],
    name: str,
    version: int,
    actor: str,
    **extra: Any,
) -> None:
    await emit(
        conn,
        name=name,
        tenant_id=tenant_id,
        aggregate_type="trust_package",
        aggregate_id=str(row["package_id"]),
        aggregate_version=version,
        data={
            "package_id": str(row["package_id"]),
            "kind": row["kind"],
            "seq": row["seq"],
            "state": extra.pop("state"),
            "evidence_class": "E3",
            "actor_principal_id": actor,
            "package_commitment": row["package_sha256"],
            **extra,
        },
    )  # fmt: skip


async def _must(
    conn: AsyncConnection, package_id: str, *, lock: bool = True, heavy: bool = False
) -> dict[str, Any]:
    row = await store.load(conn, package_id, lock=lock, heavy=heavy)
    if row is None:
        raise TrustStoreError("PACKAGE_NOT_FOUND")
    return row


async def stage(
    conn: AsyncConnection,
    *,
    tenant_id: str,
    actor: str,
    master_list: bytes,
    anchors: Sequence[bytes],
    confirmed_anchor_sha256: Sequence[str],
    source_uri: str,
    source_note: str | None,
    now: datetime,
) -> dict[str, Any]:
    """Record an imported list with its chain of custody. Nothing is read from the list yet and nothing is trusted."""
    if not master_list or len(master_list) > MAX_BYTES:
        raise TrustStoreError("PACKAGE_SIZE_INVALID")
    if not 1 <= len(anchors) <= MAX_ANCHORS:
        raise TrustStoreError("ANCHOR_COUNT_INVALID", f"1 to {MAX_ANCHORS} anchors")
    pems: list[str] = []
    fingerprints: list[str] = []
    for raw in anchors:
        try:
            cert = _load_cert(raw)
        except ValueError as e:
            raise TrustStoreError("ANCHOR_UNREADABLE") from e
        fingerprints.append(fingerprint(cert))
        pems.append(cert.public_bytes(Encoding.PEM).decode("ascii"))
    # the stager must have compared each anchor with the country's own publication: they type the fingerprints back
    if sorted({f.lower() for f in confirmed_anchor_sha256}) != sorted(set(fingerprints)):
        raise TrustStoreError(
            "ANCHOR_FINGERPRINT_MISMATCH",
            "the confirmed fingerprints must be exactly the SHA-256 of the anchors supplied",
        )
    package_id = str(uuid.uuid4())
    await store.insert(
        conn,
        tenant_id=tenant_id,
        package_id=package_id,
        source_uri=source_uri,
        source_note=source_note,
        package_sha256=hashlib.sha256(master_list).hexdigest(),
        package_der=master_list,
        anchor_pems=pems,
        anchor_sha256=sorted(set(fingerprints)),
        staged_by=actor,
        now=now,
    )
    row = await _must(conn, package_id, lock=False)
    await _emit(
        conn, tenant_id, row, "evidence.trust_package.staged", 1, actor, state=State.STAGED.value
    )
    return row


async def validate(
    conn: AsyncConnection,
    *,
    tenant_id: str,
    actor: str,
    package_id: str,
    now: datetime,
    source_policy: rules.SourcePolicy | None = None,
) -> dict[str, Any]:
    """Verify the stored master list against the stored anchors. A list that does not verify is REJECTED; it never becomes a candidate."""
    row = await _must(conn, package_id, heavy=True)
    rules.require_validate(State(row["state"]))
    anchors = [x509.load_pem_x509_certificate(p.encode("ascii")) for p in row["anchor_pems"]]
    result = read_master_list(bytes(row["package_der"]), anchors=anchors, at=now)
    records = [
        {
            "fingerprint": fingerprint(c),
            "country": _country(c),
            "subject": c.subject.rfc4514_string()[:300],
            "not_after": c.not_valid_after_utc.isoformat(),
            "der_b64": base64.b64encode(c.public_bytes(Encoding.DER)).decode("ascii"),
        }
        for c in result.certificates
    ]
    policy = source_policy if source_policy is not None else load_source_policy()
    problems = rules.authority_problems(
        policy, {r["country"] for r in records}, str(row["source_uri"])
    )
    new_state = rules.after_validation(
        result.status, len(records), authority_ok=not (policy.enforce and problems)
    )
    reasons = list(result.reasons)
    reasons += [p if policy.enforce else f"ADVISORY:{p}" for p in problems]
    if new_state is State.REJECTED and not reasons:
        reasons = ["NO_CERTIFICATES"]
    version = await store.update(
        conn,
        package_id,
        expected_version=row["version"],
        state=new_state.value,
        list_status=result.status,
        list_reasons=reasons,
        list_signer=result.signer,
        certificates=records,
        certificate_count=len(records),
        manifest_digest=rules.manifest_digest(records) if records else None,
        validated_at=now,
        **(
            {
                "decision_reason": (
                    "SOURCE_NOT_ALLOWLISTED: "
                    if result.status == "VERIFIED" and problems
                    else "LIST_DID_NOT_VERIFY: "
                )
                + ",".join(reasons)[:450],
                "decided_at": now,
            }
            if new_state is State.REJECTED
            else {}
        ),
    )
    fresh = await _must(conn, package_id, lock=False)
    await _emit(
        conn, tenant_id, fresh, f"evidence.trust_package.{'validated' if new_state is State.VALIDATED else 'rejected'}", version, actor,
        state=new_state.value, outcome=result.status, certificate_count=len(records),
    )  # fmt: skip
    return fresh


async def approve(
    conn: AsyncConnection, *, tenant_id: str, actor: str, package_id: str, now: datetime
) -> dict[str, Any]:
    row = await _must(conn, package_id)
    rules.require_approve(State(row["state"]), staged_by=str(row["staged_by"]), actor=actor)
    version = await store.update(
        conn,
        package_id,
        expected_version=row["version"],
        state=State.APPROVED.value,
        decided_by=actor,
        decided_at=now,
    )
    fresh = await _must(conn, package_id, lock=False)
    await _emit(
        conn,
        tenant_id,
        fresh,
        "evidence.trust_package.approved",
        version,
        actor,
        state=State.APPROVED.value,
    )
    return fresh


async def reject(
    conn: AsyncConnection,
    *,
    tenant_id: str,
    actor: str,
    package_id: str,
    reason: str | None,
    now: datetime,
) -> dict[str, Any]:
    row = await _must(conn, package_id)
    why = rules.require_reject(State(row["state"]), reason)
    version = await store.update(
        conn, package_id, expected_version=row["version"], state=State.REJECTED.value, decided_by=actor if actor != str(row["staged_by"]) else None,
        decided_at=now, decision_reason=why,
    )  # fmt: skip
    fresh = await _must(conn, package_id, lock=False)
    await _emit(
        conn,
        tenant_id,
        fresh,
        "evidence.trust_package.rejected",
        version,
        actor,
        state=State.REJECTED.value,
    )
    return fresh


async def activate(
    conn: AsyncConnection, *, tenant_id: str, actor: str, package_id: str, now: datetime
) -> dict[str, Any]:
    """Make an approved package the active trust set. The previous active package becomes SUPERSEDED, in the same transaction."""
    row = await _must(conn, package_id)
    rules.require_activate(State(row["state"]), staged_by=str(row["staged_by"]), actor=actor)
    current = await store.active(conn)
    if current is not None:
        locked = await _must(conn, str(current["package_id"]))
        cv = await store.update(
            conn, str(locked["package_id"]), expected_version=locked["version"], state=State.SUPERSEDED.value, ended_at=now, ended_by=actor,
            ended_reason="SUPERSEDED_BY_SEQ_" + str(row["seq"]),
        )  # fmt: skip
        await _emit(
            conn, tenant_id, await _must(conn, str(locked["package_id"]), lock=False), "evidence.trust_package.superseded", cv, actor,
            state=State.SUPERSEDED.value,
        )  # fmt: skip
    version = await store.update(
        conn,
        package_id,
        expected_version=row["version"],
        state=State.ACTIVE.value,
        activated_by=actor,
        activated_at=now,
    )
    fresh = await _must(conn, package_id, lock=False)
    await _emit(
        conn,
        tenant_id,
        fresh,
        "evidence.trust_package.activated",
        version,
        actor,
        state=State.ACTIVE.value,
    )
    return fresh


async def rollback(
    conn: AsyncConnection,
    *,
    tenant_id: str,
    actor: str,
    to_package_id: str | None,
    reason: str | None,
    now: datetime,
) -> dict[str, Any]:
    """Restore an earlier package. The current one becomes ROLLED_BACK (it is never a rollback target) and the reason is recorded."""
    refs = [
        rules.PackageRef(
            str(s["package_id"]), int(s["seq"]), State(s["state"]), str(s["staged_by"]),
            s["activated_at"].isoformat() if s["activated_at"] else None,
        )
        for s in await store.states(conn)
    ]  # fmt: skip
    current, target, why = rules.plan_rollback(refs, to_package_id=to_package_id, reason=reason)
    cur = await _must(conn, current.package_id)
    tgt = await _must(conn, target.package_id)
    cv = await store.update(
        conn, current.package_id, expected_version=cur["version"], state=State.ROLLED_BACK.value, ended_at=now, ended_by=actor, ended_reason=why
    )  # fmt: skip
    await _emit(
        conn, tenant_id, await _must(conn, current.package_id, lock=False), "evidence.trust_package.rolled_back", cv, actor,
        state=State.ROLLED_BACK.value,
    )  # fmt: skip
    tv = await store.update(
        conn, target.package_id, expected_version=tgt["version"], state=State.ACTIVE.value, activated_by=actor if actor != str(tgt["staged_by"]) else None,
        activated_at=now, ended_at=None, ended_by=None, ended_reason=None,
    )  # fmt: skip
    fresh = await _must(conn, target.package_id, lock=False)
    await _emit(
        conn,
        tenant_id,
        fresh,
        "evidence.trust_package.activated",
        tv,
        actor,
        state=State.ACTIVE.value,
        outcome="ROLLBACK_RESTORE",
    )
    return fresh


async def retire(
    conn: AsyncConnection, *, tenant_id: str, actor: str, reason: str | None, now: datetime
) -> dict[str, Any]:
    """Stop trusting the active package. The store keeps governing, so afterwards no chip can be certified until another package is activated."""
    current = await store.active(conn)
    if current is None:
        raise TrustStoreError("NOTHING_TO_RETIRE", "there is no active package")
    row = await _must(conn, str(current["package_id"]))
    why = rules.require_retire(State(row["state"]), reason)
    version = await store.update(
        conn, str(row["package_id"]), expected_version=row["version"], state=State.RETIRED.value, ended_at=now, ended_by=actor, ended_reason=why
    )  # fmt: skip
    fresh = await _must(conn, str(row["package_id"]), lock=False)
    await _emit(
        conn,
        tenant_id,
        fresh,
        "evidence.trust_package.retired",
        version,
        actor,
        state=State.RETIRED.value,
    )
    return fresh


async def read(conn: AsyncConnection, package_id: str) -> dict[str, Any]:
    return await _must(conn, package_id, lock=False)


async def listing(conn: AsyncConnection) -> list[dict[str, Any]]:
    return await store.listing(conn)


async def check_chip(
    conn: AsyncConnection,
    *,
    package_id: str,
    sod: bytes,
    groups: Mapping[int, bytes],
    crls: Sequence[Any] = (),
    at: datetime | None = None,
) -> dict[str, Any]:
    """The O2 validation fixture: run a chip check against ONE package, staged or not, without trusting it for anyone.

    A package that never validated has no certificates and so answers INDETERMINATE for every chip, which is the point."""
    row = await _must(conn, package_id, lock=False, heavy=True)
    certs = [
        x509.load_der_x509_certificate(base64.b64decode(c["der_b64"])) for c in row["certificates"]
    ]
    verdict = epassport.verify(sod, groups, csca=certs, crls=tuple(crls), at=at)
    return {
        "package_id": str(row["package_id"]),
        "package_state": row["state"],
        "certificates_used": len(certs),
        "trusted_for_production": row["state"] == State.ACTIVE.value,
        **verdict.public(),
    }


@dataclass(frozen=True, slots=True)
class EffectiveTrust:
    csca: tuple[x509.Certificate, ...]
    governed: bool  # True once any package was ever activated: then the store is the whole truth
    package_id: str | None = None
    seq: int | None = None
    manifest_digest: str | None = None

    def public(self) -> dict[str, Any]:
        return {
            "source": "TRUST_STORE" if self.governed else "CONFIG_FOLDER",
            "package_id": self.package_id,
            "package_seq": self.seq,
            "manifest_digest": self.manifest_digest,
        }


async def effective_csca(
    conn: AsyncConnection, folder: Sequence[x509.Certificate]
) -> EffectiveTrust:
    """The country signing CAs a chip check may use. Once the tenant's store has ever activated a package it governs (an empty active set trusts nothing);
    before that the deployment's csca folder applies, exactly as it did before the store existed."""
    states = await store.states(conn)
    if not rules.governs(State(s["state"]) for s in states):
        return EffectiveTrust(tuple(folder), False)
    row = await store.active(conn)
    if row is None:
        return EffectiveTrust((), True)
    certs = tuple(
        x509.load_der_x509_certificate(base64.b64decode(c["der_b64"])) for c in row["certificates"]
    )
    return EffectiveTrust(
        certs, True, str(row["package_id"]), int(row["seq"]), row["manifest_digest"]
    )


__all__ = [
    "EffectiveTrust",
    "TrustStoreError",
    "activate",
    "approve",
    "check_chip",
    "effective_csca",
    "listing",
    "read",
    "reject",
    "retire",
    "rollback",
    "stage",
    "validate",
    "view",
]
