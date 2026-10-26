"""Subject-evidence vault: handles, identity resolver, per-subject keys, supporting objects, legal
holds and crypto-erasure (DEC-F1 §1-4; EVIDENCE-001 §13-14, §22; PRIV-001 §9-11).

    handle   = await actor_handle(conn, tenant, principal_id)     # pseudonym in receipts
    pid      = await resolve_handle(conn, tenant, handle)          # privileged, audited
    obj_id   = await store_object(conn, tenant, content=..., subject_principal_id=...)
    result   = await erase_subject(conn, tenant, principal_id, request_ref=..., actor=...)

Per-subject keys: a random AES-256 key per (tenant, subject), stored only wrapped under the cell's
SUBJECT_ERASURE_KEY (KMS/HSM) with an encryption context naming the tenant and subject. The resolver
row (handle -> principal id) and every supporting object about that subject are encrypted under it.
Crypto-erasure removes the only wrapped copy, so nothing that remains - rows, replicas, indexes -
can be decrypted again; receipts keep their handles, hashes and commitments and still verify.

Backups (DEC-F1 §4): a restore from before the erasure would bring the wrapped key back, so the
restore path re-applies the erasure tombstones (`reapply_tombstones`, exported continuously outside
the database through the outbox) before the cell serves traffic; the cell SUBJECT_ERASURE_KEY is
rotated and old versions retired after `backup_erasure_window`, which bounds the residual risk.
"""

from __future__ import annotations

import asyncio
import logging
import secrets
import time
from collections.abc import Iterable, Mapping
from contextlib import suppress
from dataclasses import dataclass, field
from datetime import UTC, datetime
from typing import Any

from cryptography.exceptions import InvalidTag
from cryptography.hazmat.primitives.ciphers.aead import AESGCM
from sqlalchemy.ext.asyncio import AsyncConnection
from zid_context import uuid7
from zid_crypto import CryptoError
from zid_crypto.envelope import Aad, Envelope, open_envelope, rewrap_envelope, seal
from zid_crypto.handles import handle
from zid_crypto.providers import (
    KeyDestroyedError,
    KeyMetadata,
    KeyRef,
    LocalKeyProvider,
    zeroize,
)
from zid_evidence import Digest, hash_bytes, hash_json
from zid_outbox import emit

from zid_platform.modules.evidence.application.runtime import EvidenceRuntime, current_runtime
from zid_platform.modules.evidence.domain.ledger import LedgerError
from zid_platform.modules.evidence.domain.receipts import ACTOR_DOMAIN, SUBJECT_DOMAIN
from zid_platform.modules.evidence.infrastructure import vault_store as store
from zid_platform.modules.evidence.infrastructure.vault_store import Hold

log = logging.getLogger("zid.evidence.vault")

CLASSIFICATIONS = ("D1", "D2", "D3", "D4")
_NONCE = 12


def _now() -> datetime:
    return datetime.now(UTC)


async def make_handle(rt: EvidenceRuntime, tenant_id: str, domain: str, value: str) -> str:
    """HMAC under HMAC_INTERNAL (KMS GenerateMac in deployed cells: off the event loop)."""
    return await asyncio.to_thread(handle, rt.mac_key, tenant_id, domain, value)


async def subject_ref(tenant_id: str, principal_id: str, rt: EvidenceRuntime | None = None) -> str:
    return await make_handle(rt or current_runtime(), tenant_id, SUBJECT_DOMAIN, principal_id)


def _context(tenant_id: str, ref: str) -> dict[str, str]:
    return {"tenant": tenant_id, "subject": ref, "purpose": "subject-erasure-key"}


async def _subject_key(
    conn: AsyncConnection, rt: EvidenceRuntime, tenant_id: str, ref: str, *, create: bool
) -> tuple[bytearray, str] | None:
    """(plaintext subject key, key id); None when the subject was erased (or none and no create).
    The caller zeroizes the key after use."""
    row = await store.load_subject_key(conn, tenant_id, ref)
    if row is None and create:
        dek = bytearray(secrets.token_bytes(32))
        try:
            wrapped, exact = await rt.provider.wrap_data_key(
                rt.kek_for(tenant_id), dek, _context(tenant_id, ref)
            )
        finally:
            zeroize(dek)
        row = await store.insert_subject_key(
            conn,
            tenant_id=tenant_id,
            subject_ref=ref,
            key_id=str(uuid7()),
            wrapped_key=wrapped,
            kek_id=exact.key_id,
            kek_version=exact.version or 1,
        )
    if row is None or row.state != "ACTIVE" or row.wrapped_key is None:
        return None
    kek = KeyRef(rt.subject_kek.purpose, row.kek_id, row.kek_version)
    try:
        key = await rt.provider.unwrap_data_key(kek, row.wrapped_key, _context(tenant_id, ref))
    except KeyDestroyedError as e:
        # The subject key row is still ACTIVE, so this is not an erasure: the KEK that wraps it is gone or
        # disabled (a customer-held key switched off, HYOK). Refuse, and say why; never fall back to another key.
        raise LedgerError(
            "EVIDENCE_KEY_UNAVAILABLE", "the tenant key is disabled or destroyed"
        ) from e
    return key, row.key_id


def _encrypt(key: bytearray, aad: bytes, plaintext: bytes) -> bytes:
    nonce = secrets.token_bytes(_NONCE)
    return nonce + AESGCM(bytes(key)).encrypt(nonce, plaintext, aad)


def _decrypt(key: bytearray, aad: bytes, blob: bytes) -> bytes:
    try:
        return AESGCM(bytes(key)).decrypt(blob[:_NONCE], blob[_NONCE:], aad)
    except InvalidTag as e:
        raise LedgerError("EVIDENCE_SOURCE_INTEGRITY_FAILED", "ciphertext does not decrypt") from e


def _resolver_aad(tenant_id: str, h: str) -> bytes:
    return f"zid-resolver-v1|{tenant_id}|{h}".encode()


# ------------------------------------------------------------------------------------- handles


async def actor_handle(
    conn: AsyncConnection,
    tenant_id: str,
    principal_id: str,
    *,
    domain: str = ACTOR_DOMAIN,
    rt: EvidenceRuntime | None = None,
) -> str:
    """The pseudonymous handle of `principal_id` in `domain`, registered with the resolver.

    After the subject was erased the handle is still returned (deterministic) but no longer
    registered: nothing can resolve it again."""
    rt = rt or current_runtime()
    h = await make_handle(rt, tenant_id, domain, principal_id)
    if await store.handle_row(conn, tenant_id, h) is not None:
        return h
    ref = await make_handle(rt, tenant_id, SUBJECT_DOMAIN, principal_id)
    got = await _subject_key(conn, rt, tenant_id, ref, create=True)
    if got is None:
        return h
    key, _ = got
    try:
        blob = _encrypt(key, _resolver_aad(tenant_id, h), principal_id.encode())
    finally:
        zeroize(key)
    await store.insert_handle(
        conn, tenant_id=tenant_id, handle=h, domain=domain, subject_ref=ref, ciphertext=blob
    )
    return h


async def resolve_handle(
    conn: AsyncConnection, tenant_id: str, h: str, *, rt: EvidenceRuntime | None = None
) -> str:
    """Handle -> principal id. Callers authorize first (evidence.identity.resolve, R3/E3).

    EVIDENCE_RAW_OBJECT_ERASED: identity-resolution data unavailable by lawful erasure."""
    rt = rt or current_runtime()
    row = await store.handle_row(conn, tenant_id, h)
    if row is None:
        raise LedgerError("NOT_FOUND", "unknown handle")
    if row.status != "ACTIVE" or row.principal_ciphertext is None:
        raise LedgerError("EVIDENCE_RAW_OBJECT_ERASED", "identity-resolution data lawfully erased")
    got = await _subject_key(conn, rt, tenant_id, row.subject_ref, create=False)
    if got is None:
        raise LedgerError("EVIDENCE_RAW_OBJECT_ERASED", "identity-resolution data lawfully erased")
    key, _ = got
    try:
        return _decrypt(key, _resolver_aad(tenant_id, h), bytes(row.principal_ciphertext)).decode()
    finally:
        zeroize(key)


# ---------------------------------------------------------------------------- evidence objects


def _object_aad(tenant_id: str, object_id: str, object_type: str) -> Aad:
    return Aad(tenant_id, "evidence_object", object_id, object_type, "1")


async def store_object(
    conn: AsyncConnection,
    tenant_id: str,
    *,
    content: bytes,
    object_type: str,
    classification: str,
    retention_class: str,
    subject_principal_id: str | None = None,
    expires_at: datetime | None = None,
    rt: EvidenceRuntime | None = None,
) -> tuple[str, Digest]:
    """Encrypt and store a supporting artifact (§14); the receipt keeps only (id, digest).

    About one person: encrypted under that subject's erasable key. Otherwise: envelope-encrypted
    under the cell DATA_KEK. Never stored for an erased subject (EVIDENCE_RAW_OBJECT_ERASED)."""
    rt = rt or current_runtime()
    if classification not in CLASSIFICATIONS:
        raise LedgerError("EVIDENCE_SCHEMA_INVALID", "classification must be D1-D4")
    object_id = str(uuid7())
    digest = hash_bytes(content, alg="sha-256")
    ref: str | None = None
    if subject_principal_id is not None:
        ref = await make_handle(rt, tenant_id, SUBJECT_DOMAIN, subject_principal_id)
        got = await _subject_key(conn, rt, tenant_id, ref, create=True)
        if got is None:
            raise LedgerError("EVIDENCE_RAW_OBJECT_ERASED", "the subject was lawfully erased")
        key, key_id = got
        try:
            aad = _object_aad(tenant_id, object_id, object_type).encode()
            ciphertext = _encrypt(key, aad, content)
        finally:
            zeroize(key)
        key_ref = f"subject:{key_id}"
    else:
        envelope = await seal(
            content, _object_aad(tenant_id, object_id, object_type), rt.provider, rt.data_kek
        )
        ciphertext = envelope.to_bytes()
        key_ref = f"kek:{envelope.kek.key_id}:{envelope.kek.version}"
    await store.insert_object(
        conn,
        {
            "evidence_object_id": object_id,
            "tenant_id": tenant_id,
            "subject_ref": ref,
            "object_type": object_type,
            "classification": classification,
            "retention_class": retention_class,
            "content_hash": str(digest),
            "object_uri": f"pg:evidence.evidence_object/{object_id}",
            "encryption_key_ref": key_ref,
            "ciphertext": ciphertext,
            "expires_at": expires_at,
        },
    )
    await emit(
        conn,
        name="evidence.object.stored",
        tenant_id=tenant_id,
        aggregate_type="evidence_object",
        aggregate_id=object_id,
        aggregate_version=1,
        data={
            "evidence_object_id": object_id,
            "object_type": object_type,
            "classification": classification,
            "content_hash": str(digest),
            "retention_class": retention_class,
        },
    )
    return object_id, digest


async def read_object(
    conn: AsyncConnection, tenant_id: str, object_id: str, *, rt: EvidenceRuntime | None = None
) -> bytes:
    """Decrypt a supporting artifact (callers authorize: evidence.object.read, R3) and check its
    digest. EVIDENCE_RAW_OBJECT_ERASED / EVIDENCE_RETENTION_EXPIRED once disposed."""
    rt = rt or current_runtime()
    row = await store.load_object(conn, object_id)
    if row is None:
        raise LedgerError("NOT_FOUND", "unknown evidence object")
    if row.status == "ERASED":
        raise LedgerError("EVIDENCE_RAW_OBJECT_ERASED", "supporting evidence lawfully erased")
    if row.status == "EXPIRED":
        raise LedgerError("EVIDENCE_RETENTION_EXPIRED", "supporting evidence no longer retained")
    aad = _object_aad(tenant_id, object_id, row.object_type)
    if row.subject_ref is not None:
        got = await _subject_key(conn, rt, tenant_id, row.subject_ref, create=False)
        if got is None:
            raise LedgerError("EVIDENCE_RAW_OBJECT_ERASED", "supporting evidence lawfully erased")
        key, _ = got
        try:
            content = _decrypt(key, aad.encode(), bytes(row.ciphertext))
        finally:
            zeroize(key)
    else:
        try:
            content = await open_envelope(
                Envelope.from_bytes(bytes(row.ciphertext)), aad, rt.provider
            )
        except CryptoError as e:
            raise LedgerError("EVIDENCE_KEY_UNAVAILABLE", e.code) from e
    if not hash_bytes(content, alg="sha-256").matches(Digest.parse(row.content_hash)):
        raise LedgerError("EVIDENCE_SOURCE_INTEGRITY_FAILED", "content digest mismatch")
    return content


# --------------------------------------------------------------------------------- legal holds


async def create_hold(
    conn: AsyncConnection,
    tenant_id: str,
    *,
    basis: str,
    reference: str,
    requested_by: str,
    approved_by: str,
    subject_principal_ids: Iterable[str] = (),
    object_types: Iterable[str] = (),
    review_at: datetime,
    expires_at: datetime | None = None,
    now: datetime | None = None,
    rt: EvidenceRuntime | None = None,
) -> Hold:
    """An explicit, scoped, approved hold (§22): never tenant-wide by default (PRIV-001 §11)."""
    rt, now = rt or current_runtime(), now or _now()
    if approved_by == requested_by:
        raise LedgerError("EVIDENCE_ACCESS_DENIED", "a hold needs an independent approver")
    refs = [await make_handle(rt, tenant_id, SUBJECT_DOMAIN, p) for p in subject_principal_ids]
    types = sorted(set(object_types))
    if not refs and not types:
        raise LedgerError("EVIDENCE_SCHEMA_INVALID", "a hold names subjects and/or object types")
    if review_at <= now or (expires_at is not None and expires_at <= now):
        raise LedgerError("EVIDENCE_SCHEMA_INVALID", "review/expiry must be in the future")
    hold = await store.insert_hold(
        conn,
        {
            "legal_hold_id": str(uuid7()),
            "tenant_id": tenant_id,
            "basis": basis,
            "reference": reference,
            "requested_by": requested_by,
            "approved_by": approved_by,
            "subject_refs": sorted(set(refs)),
            "object_types": types,
            "effective_at": now,
            "review_at": review_at,
            "expires_at": expires_at,
        },
    )
    await emit(
        conn,
        name="evidence.legal_hold.created",
        tenant_id=tenant_id,
        aggregate_type="legal_hold",
        aggregate_id=hold.legal_hold_id,
        aggregate_version=1,
        data={
            "legal_hold_id": hold.legal_hold_id,
            "subject_count": len(hold.subject_refs),
            "object_types": list(hold.object_types),
            "approved_by": approved_by,
            "review_at": review_at.isoformat(),
            "evidence_class": "E3",
        },
    )
    return hold


async def hold_status(conn: AsyncConnection, hold_id: str) -> str | None:
    """ACTIVE / RELEASED / EXPIRED, or None when the hold is not on record (after a restore: it was lost)."""
    hold = await store.load_hold(conn, hold_id)
    return hold.status if hold else None


async def release_hold(
    conn: AsyncConnection,
    tenant_id: str,
    hold_id: str,
    *,
    by: str,
    reason: str,
    now: datetime | None = None,
) -> Hold:
    """Release: ordinary retention resumes from the original trigger (PRIV-08), nothing resets."""
    now = now or _now()
    hold = await store.load_hold(conn, hold_id, lock=True)
    if hold is None:
        raise LedgerError("NOT_FOUND", "unknown legal hold")
    if hold.status != "ACTIVE":
        raise LedgerError("EVIDENCE_LEGAL_HOLD", f"hold is {hold.status}")
    if by == hold.requested_by:
        raise LedgerError("EVIDENCE_ACCESS_DENIED", "the requester cannot release their own hold")
    released = await store.release_hold(conn, hold, by=by, reason=reason, now=now)
    await emit(
        conn,
        name="evidence.legal_hold.released",
        tenant_id=tenant_id,
        aggregate_type="legal_hold",
        aggregate_id=hold_id,
        aggregate_version=released.version,
        data={"legal_hold_id": hold_id, "released_by": by, "evidence_class": "E3"},
    )
    return released


async def holding(
    conn: AsyncConnection,
    tenant_id: str,
    *,
    subject_ref: str | None,
    object_type: str | None,
    now: datetime | None = None,
) -> list[Hold]:
    return [
        h
        for h in await store.active_holds(conn, tenant_id, now or _now())
        if h.covers(subject_ref, object_type)
    ]


async def is_held(
    conn: AsyncConnection,
    tenant_id: str,
    *,
    subject_principal_id: str | None = None,
    object_type: str | None = None,
    now: datetime | None = None,
    rt: EvidenceRuntime | None = None,
) -> bool:
    """For other modules' retention (privacy.ports): is this subject/object type held?"""
    ref = (
        await make_handle(rt or current_runtime(), tenant_id, SUBJECT_DOMAIN, subject_principal_id)
        if subject_principal_id
        else None
    )
    return bool(await holding(conn, tenant_id, subject_ref=ref, object_type=object_type, now=now))


# ------------------------------------------------------------------------------ crypto-erasure


@dataclass(frozen=True, slots=True)
class ErasureResult:
    subject_ref: str
    outcome: str  # ERASED | PARTIAL (something held) | NOTHING_TO_ERASE
    tombstone_id: str | None
    erased_objects: tuple[str, ...] = ()
    retained_objects: tuple[str, ...] = ()
    hold_ids: tuple[str, ...] = ()
    key_destroyed: bool = False
    handles_erased: int = 0
    details: Mapping[str, Any] = field(default_factory=dict)


@dataclass(frozen=True, slots=True)
class TenantErasure:
    subjects: int
    erased: int  # subjects fully crypto-erased
    partial: int  # subjects with something held back by a legal hold
    nothing: int
    objects_erased: int
    objects_retained: int
    dedicated_key_scheduled: bool  # the company's own KEK was scheduled for destruction


async def erase_tenant(
    conn: AsyncConnection,
    tenant_id: str,
    *,
    request_ref: str,
    now: datetime | None = None,
    rt: EvidenceRuntime | None = None,
) -> TenantErasure:
    """TEN-25: crypto-erase every subject the company's evidence vault holds, through the same legal-hold evaluation
    as a single person's erasure; a held subject is kept and reported, never destroyed. Only when nothing is held back
    is the company's own dedicated KEK scheduled for destruction (a shared platform KEK is never touched). Receipts,
    hashes and checkpoints stay valid: the ledger is retained per policy. Idempotent."""
    rt, now = rt or current_runtime(), now or _now()
    seen: set[str] = set()
    erased = partial = nothing = objs = kept = 0
    while True:
        fresh = [
            r
            for r in await store.active_subject_keys(conn, 500, tenant_id)
            if str(r.subject_ref) not in seen
        ]
        if not fresh:
            break
        for row in fresh:
            ref = str(row.subject_ref)
            seen.add(ref)
            res = await erase_subject_ref(
                conn, tenant_id, ref, request_ref=request_ref, now=now, rt=rt
            )
            objs += len(res.erased_objects)
            kept += len(res.retained_objects)
            if res.outcome == "ERASED":
                erased += 1
            elif res.outcome == "PARTIAL":
                partial += 1
            else:
                nothing += 1
    scheduled = False
    own_kek = rt.tenant_keks.get(tenant_id)
    if own_kek is not None and partial == 0:
        await rt.provider.destroy_key(own_kek)
        scheduled = True
    return TenantErasure(len(seen), erased, partial, nothing, objs, kept, scheduled)


async def erase_subject(
    conn: AsyncConnection,
    tenant_id: str,
    principal_id: str,
    *,
    request_ref: str,
    now: datetime | None = None,
    rt: EvidenceRuntime | None = None,
) -> ErasureResult:
    """Crypto-erase a subject's evidence, after legal-hold evaluation (DEC-F1 §3).

    No hold: every supporting object is disposed, the resolver rows are erased and the subject key
    is destroyed - nothing about the person can be decrypted again, while receipts, hashes and
    checkpoints stay valid. A hold on some objects: the unheld ones are disposed, the held ones and
    the key are kept (PARTIAL, PRIV-17: retained items are restricted and reasoned). Idempotent."""
    rt, now = rt or current_runtime(), now or _now()
    ref = await make_handle(rt, tenant_id, SUBJECT_DOMAIN, principal_id)
    return await erase_subject_ref(conn, tenant_id, ref, request_ref=request_ref, now=now, rt=rt)


async def erase_subject_ref(
    conn: AsyncConnection,
    tenant_id: str,
    ref: str,
    *,
    request_ref: str,
    now: datetime,
    rt: EvidenceRuntime,
) -> ErasureResult:
    """`erase_subject` for a subject already named by its handle (tenant deletion walks the handles it holds)."""
    await store.load_subject_key(conn, tenant_id, ref, lock=True)  # serialize erasure per subject
    objects = await store.subject_objects(conn, tenant_id, ref)
    holds_now = await store.active_holds(conn, tenant_id, now)
    erased, retained, hold_ids = [], [], set()
    for obj in objects:
        if obj.status != "STORED":
            continue
        covering = [h for h in holds_now if h.covers(ref, obj.object_type)]
        if covering:
            retained.append(str(obj.evidence_object_id))
            hold_ids |= {h.legal_hold_id for h in covering}
            continue
        if await store.dispose_object(conn, str(obj.evidence_object_id), "ERASED"):
            erased.append(str(obj.evidence_object_id))
    subject_held = [h for h in holds_now if h.covers(ref, None) and h.subject_refs]
    hold_ids |= {h.legal_hold_id for h in subject_held}
    keep_key = bool(retained or subject_held)
    destroyed, handles = False, 0
    if not keep_key:
        handles = await store.erase_handles(conn, tenant_id, ref)
        destroyed = await store.destroy_subject_key(conn, tenant_id, ref)
    if not (erased or handles or destroyed or retained):
        return ErasureResult(ref, "NOTHING_TO_ERASE", None)
    outcome = "PARTIAL" if keep_key else "ERASED"
    tombstone_id = str(uuid7())
    body = {
        "schema_version": "erasure-tombstone.v1",
        "tombstone_id": tombstone_id,
        "tenant_id": tenant_id,
        "subject_ref": ref,
        "request_ref": request_ref,
        "outcome": outcome,
        "erased_objects": erased,
        "retained_objects": retained,
        "legal_hold_ids": sorted(hold_ids),
        "key_destroyed": destroyed,
        "erased_at": now.isoformat(),
    }
    await store.insert_tombstone(
        conn,
        {
            "tombstone_id": tombstone_id,
            "tenant_id": tenant_id,
            "subject_ref": ref,
            "request_ref": request_ref,
            "outcome": outcome,
            "body": body,
            "record_hash": str(hash_json(body, alg="sha-256")),
        },
    )
    # The tombstone travels through the outbox to the restore-replay archive (PRIV-001 §10).
    await emit(
        conn,
        name="evidence.object.erased",
        tenant_id=tenant_id,
        aggregate_type="erasure_tombstone",
        aggregate_id=tombstone_id,
        aggregate_version=1,
        data={
            k: body[k]
            for k in ("tombstone_id", "subject_ref", "outcome", "key_destroyed", "erased_objects")
        },
    )
    return ErasureResult(
        ref,
        outcome,
        tombstone_id,
        tuple(erased),
        tuple(retained),
        tuple(sorted(hold_ids)),
        destroyed,
        handles,
    )


async def reapply_tombstones(
    conn: AsyncConnection, tenant_id: str, tombstones: Iterable[Mapping[str, Any]]
) -> int:
    """Restore path (PRIV-11, EVID-27): re-apply erasures recorded after the backup was taken.

    `tombstones` come from the archive outside the restored database (the `evidence.object.erased`
    stream). Returns how many subjects had to be erased again."""
    again = 0
    for t in tombstones:
        if str(t.get("tenant_id", tenant_id)) != tenant_id:
            continue
        ref = str(t["subject_ref"])
        changed = False
        for obj_id in t.get("erased_objects", ()):
            changed |= await store.dispose_object(conn, str(obj_id), "ERASED")
        if t.get("outcome") == "ERASED":
            changed |= bool(await store.erase_handles(conn, tenant_id, ref))
            changed |= await store.destroy_subject_key(conn, tenant_id, ref)
        again += int(changed)
    return again


async def dispose_due_objects(
    conn: AsyncConnection, now: datetime | None = None, *, limit: int = 500
) -> list[str]:
    """Retention sweep (worker, BYPASSRLS role): supporting objects past expires_at are disposed
    unless a hold covers them; held ones resume the original schedule once released (PRIV-08)."""
    now = now or _now()
    disposed = []
    for row in await store.objects_due(conn, now, limit):
        tenant = str(row.tenant_id)
        if await holding(
            conn, tenant, subject_ref=row.subject_ref, object_type=row.object_type, now=now
        ):
            continue
        if await store.dispose_object(conn, str(row.evidence_object_id), "EXPIRED"):
            disposed.append(str(row.evidence_object_id))
            await emit(
                conn,
                name="evidence.retention.deleted",
                tenant_id=tenant,
                aggregate_type="evidence_object",
                aggregate_id=str(row.evidence_object_id),
                aggregate_version=2,
                data={
                    "evidence_object_id": str(row.evidence_object_id),
                    "content_hash": row.content_hash,
                    "retention_class": row.retention_class,
                    "due_at": row.expires_at.isoformat(),
                },
            )
    return disposed


async def rewrap_subject_keys(
    conn: AsyncConnection,
    *,
    rt: EvidenceRuntime | None = None,
    limit: int = 500,
    tenant_id: str | None = None,
) -> int:
    """Background rewrap (DEC-F5 §7: no flag-day rotation): subject keys still wrapped under a
    retired KEK version, or under the platform KEK after a tenant moved to its own key, are
    rewrapped under the current KEK so the old version can be retired. Worker role (all tenants).
    A destroyed (erased) subject key is never touched. A key that cannot be unwrapped (a
    customer-held KEK that is unavailable) is skipped and retried next pass; it never blocks the
    other tenants' rotation."""
    rt = rt or current_runtime()
    current: dict[str, int] = {}
    done = 0
    for row in await store.active_subject_keys(conn, limit, tenant_id):
        tenant = str(row.tenant_id)
        target = rt.kek_for(tenant)
        if target.key_id not in current:
            current[target.key_id] = (await rt.provider.metadata(target)).version
        version = current[target.key_id]
        if row.kek_id == target.key_id and int(row.kek_version) == version:
            continue
        context = _context(tenant, row.subject_ref)
        old = KeyRef(rt.subject_kek.purpose, row.kek_id, int(row.kek_version))
        try:
            key = await rt.provider.unwrap_data_key(old, bytes(row.wrapped_key), context)
        except CryptoError:
            with suppress(Exception):  # a failing log sink never aborts the pass (EVID-29)
                log.warning(
                    "subject key rewrap skipped (KEK unavailable)",
                    extra={"tenant_id": tenant, "kek_id": row.kek_id},
                )
            continue
        try:
            wrapped, exact = await rt.provider.wrap_data_key(target.at(version), key, context)
        finally:
            zeroize(key)
        await store.rewrap_subject_key(
            conn, tenant, row.subject_ref, wrapped, exact.key_id, exact.version or version
        )
        done += 1
    return done


async def rewrap_object_envelopes(
    conn: AsyncConnection,
    *,
    rt: EvidenceRuntime | None = None,
    limit: int = 500,
    tenant_id: str | None = None,
) -> int:
    """Background rewrap of objects enveloped under the cell DATA_KEK (DEC-F5 §7): the wrapped DEK
    moves to the current KEK version; ciphertext, nonce and content hash are untouched, so nothing
    is decrypted in bulk or rewritten. An envelope that cannot be opened is skipped and retried."""
    rt = rt or current_runtime()
    target = rt.data_kek
    version = (await rt.provider.metadata(target)).version
    current_ref = f"kek:{target.key_id}:{version}"
    done = 0
    for row in await store.kek_wrapped_objects(conn, current_ref, limit, tenant_id):
        envelope = Envelope.from_bytes(bytes(row.ciphertext))
        aad = _object_aad(str(row.tenant_id), str(row.evidence_object_id), row.object_type)
        try:
            moved = await rewrap_envelope(envelope, aad, rt.provider, target.at(version))
        except CryptoError:
            with suppress(Exception):  # a failing log sink never aborts the pass (EVID-29)
                log.warning(
                    "object rewrap skipped (KEK unavailable)",
                    extra={"tenant_id": str(row.tenant_id), "kek_id": envelope.kek.key_id},
                )
            continue
        await store.update_object_envelope(
            conn,
            str(row.evidence_object_id),
            moved.to_bytes(),
            f"kek:{moved.kek.key_id}:{moved.kek.version}",
        )
        done += 1
    return done


async def kek_dependents(conn: AsyncConnection, kek: KeyRef) -> int:
    """How many live artifacts still depend on this exact KEK version (dependency inventory)."""
    if kek.version is None:
        raise CryptoError("CRYPTO_KEY_UNKNOWN", "dependency check needs the exact key version")
    return await store.count_kek_dependents(conn, kek.key_id, kek.version)


async def retire_kek_version(
    conn: AsyncConnection, kek: KeyRef, *, rt: EvidenceRuntime | None = None
) -> KeyMetadata:
    """Destroy a superseded KEK version, but only once nothing depends on it (CRYPTO-001 §8:
    "retirement occurs only after dependency inventory confirms no active artifact depends on
    the key"). Refuses with CRYPTO_KEK_HAS_DEPENDENTS otherwise.

    Local/test providers hold versions themselves and destroy the version here; KMS rotates key
    material transparently and a version label is not a KMS key, so a deployed cell retires old
    key material through the key policy (IaC) after this same check."""
    rt = rt or current_runtime()
    remaining = await kek_dependents(conn, kek)
    if remaining:
        raise CryptoError(
            "CRYPTO_KEK_HAS_DEPENDENTS", f"{remaining} artifacts still wrapped under {kek.key_id}"
        )
    if not isinstance(rt.provider, LocalKeyProvider):
        raise CryptoError("CRYPTO_RETIRE_UNSUPPORTED", "retire KMS key material through its policy")
    return await rt.provider.destroy_key(kek)


@dataclass(frozen=True, slots=True)
class CompromiseReport:
    kek: KeyRef
    dependents_before: int
    rewrapped: int
    dependents_after: int
    retired: bool
    seconds: float


async def respond_to_kek_compromise(
    conn: AsyncConnection, kek: KeyRef, *, rt: EvidenceRuntime | None = None
) -> CompromiseReport:
    """Incident response for a compromised KEK version (CRYPTO-001 §8.1, §26): identify what depends
    on it, rotate so no new artifact can be wrapped under it, rewrap every dependent to the
    successor and retire the compromised version once the dependency count is zero. Local/test
    providers only (a deployed KMS key is rotated by the key policy); the counts and timing are
    returned for the incident record. `kek` must name the exact version that is compromised."""
    rt = rt or current_runtime()
    started = time.monotonic()
    if not isinstance(rt.provider, LocalKeyProvider):
        raise CryptoError("CRYPTO_RETIRE_UNSUPPORTED", "rotate KMS key material through its policy")
    if kek.version is None:
        raise CryptoError("CRYPTO_KEY_UNKNOWN", "name the exact compromised key version")
    before = await kek_dependents(conn, kek)
    if (await rt.provider.metadata(kek)).state.value == "ACTIVE":
        rt.provider.rotate_key(kek)  # the old version is RETIRED: nothing new can be wrapped
    rewrapped = 0
    while moved := await rewrap_subject_keys(conn, rt=rt) + await rewrap_object_envelopes(
        conn, rt=rt
    ):
        rewrapped += moved
    after = await kek_dependents(conn, kek)
    retired = False
    if after == 0:
        await retire_kek_version(conn, kek, rt=rt)
        retired = True
    return CompromiseReport(kek, before, rewrapped, after, retired, time.monotonic() - started)


@dataclass(frozen=True, slots=True)
class RekeyReport:
    subject_ref: str
    objects_reencrypted: int
    old_key_id: str
    new_key_id: str


async def rekey_subject(
    conn: AsyncConnection, tenant_id: str, subject_ref_: str, *, rt: EvidenceRuntime | None = None
) -> RekeyReport | None:
    """Recover from a compromised data key (CRYPTO-001 §8.1, §26; CRYP-11): the one subject's objects are
    decrypted under the old key and re-encrypted under a fresh key wrapped by the tenant's KEK, in the
    caller's transaction, then the old key is discarded. Other subjects, other tenants and the KEKs are not
    touched, so the blast radius is that subject's objects and nothing else. None when the subject has no
    active key (never stored, or already erased). Worker role: it rewrites ciphertext."""
    rt = rt or current_runtime()
    got = await _subject_key(conn, rt, tenant_id, subject_ref_, create=False)
    if got is None:
        return None
    old, old_key_id = got
    new = bytearray(secrets.token_bytes(32))
    new_key_id = str(uuid7())
    moved = 0
    try:
        for row in await store.subject_object_blobs(conn, tenant_id, subject_ref_):
            aad = _object_aad(tenant_id, str(row.evidence_object_id), row.object_type).encode()
            plaintext = _decrypt(old, aad, bytes(row.ciphertext))
            await store.update_object_ciphertext(
                conn,
                str(row.evidence_object_id),
                _encrypt(new, aad, plaintext),
                f"subject:{new_key_id}",
            )
            moved += 1
        wrapped, exact = await rt.provider.wrap_data_key(
            rt.kek_for(tenant_id), new, _context(tenant_id, subject_ref_)
        )
        await store.replace_subject_key(
            conn,
            tenant_id,
            subject_ref_,
            key_id=new_key_id,
            wrapped=wrapped,
            kek_id=exact.key_id,
            kek_version=exact.version or 1,
        )
    finally:
        zeroize(old)
        zeroize(new)
    return RekeyReport(subject_ref_, moved, old_key_id, new_key_id)


__all__ = [
    "CompromiseReport",
    "ErasureResult",
    "RekeyReport",
    "actor_handle",
    "create_hold",
    "dispose_due_objects",
    "erase_subject",
    "holding",
    "is_held",
    "kek_dependents",
    "make_handle",
    "read_object",
    "reapply_tombstones",
    "rekey_subject",
    "release_hold",
    "resolve_handle",
    "respond_to_kek_compromise",
    "retire_kek_version",
    "rewrap_object_envelopes",
    "rewrap_subject_keys",
    "store_object",
    "subject_ref",
]
