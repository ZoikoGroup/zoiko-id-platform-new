"""WF-05 address claims and proof of address on a case (ZID-DTV-WF-05 DTV-17, §02, A05-A07, A10; CR-115).

set_address     the address as typed; a change is a new revision. With ADDRESS_LOOKUP cleared, the built-in format check records
                ADDRESS_FORMAT_VALID (PASS only where the postcode shape is known and right; never a location or a residence)
add_document    a proof of address document: an evidence upload reference, its type, what was READ from it; the market's rule assesses it
                and it waits for a person (ADDRESS_DOCUMENT cleared)
review          the maker's finding (A10); CONFIRMED records ADDRESS_DOCUMENT_REVIEWED PASS and waits for a checker
check           a second, different person; agreement records ADDRESS_ASSOCIATION_CONFIRMED PASS
"""

from __future__ import annotations

import asyncio
import json
import os
import re
import uuid
from dataclasses import dataclass, replace
from datetime import date, datetime, timedelta
from functools import lru_cache
from pathlib import Path
from typing import Any

import yaml
from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncConnection

from zid_platform.modules.evidence.ports import current_runtime
from zid_platform.modules.verification.application import check_cases
from zid_platform.modules.verification.domain import address as ad
from zid_platform.modules.verification.domain import address_review as ar
from zid_platform.modules.verification.domain import check_cases as cc
from zid_platform.modules.verification.domain import check_methods as cm
from zid_platform.modules.verification.domain import postal_challenge as pc
from zid_platform.modules.verification.domain.assertions import Assertion
from zid_platform.modules.verification.infrastructure.address_policy_registry import proof_policy

FORMAT_SOURCE = "zid-address-format@1"
CONFIG = Path(
    os.environ.get("ZID_PLATFORM_CONFIG") or Path(__file__).resolve().parents[6] / "config"
)
ADDRESS_METHODS = frozenset(
    {
        cm.CheckMethod.ADDRESS_LOOKUP,
        cm.CheckMethod.ADDRESS_DOCUMENT,
        cm.CheckMethod.ADDRESS_DATA_SOURCE,
        cm.CheckMethod.ADDRESS_POSTAL_CHALLENGE,
    }
)


async def _open_case(
    conn: AsyncConnection, case_id: str, needs: frozenset[cm.CheckMethod]
) -> cc.Case:
    case = await check_cases.get_case(conn, case_id)
    if case.status is cc.CaseStatus.CLOSED:
        raise cc.CaseError("CASE_CLOSED")
    if not needs & set(case.available):
        raise cc.CaseError(
            "METHOD_NOT_AVAILABLE_FOR_CASE", ",".join(sorted(m.value for m in needs))
        )
    return case


def _fields(a: ad.Address) -> dict[str, str]:
    return {
        k: getattr(a, k)
        for k in ("line1", "line2", "city", "region", "postal_code", "country")
        if getattr(a, k)
    }


async def set_address(
    conn: AsyncConnection,
    case_id: str,
    raw: dict[str, Any],
    *,
    now: datetime,
    locator: ad.AddressProvider | None = None,
    source: ad.AddressProvider | None = None,
    subject_name: str | None = None,
) -> dict[str, Any]:
    """`locator` (a directory or geospatial adapter) and `source` (a licensed association source) are the deployment's; none ships. Each answer is
    recorded only as what that provider states, from the method the case was cleared for."""
    case = await _open_case(conn, case_id, ADDRESS_METHODS)
    address = ad.parse_address(raw)
    postcode = ad.postcode_check(address)
    cid = uuid.UUID(case_id)
    rev = (
        (
            await conn.execute(
                text(
                    "SELECT coalesce(max(revision), 0) AS r FROM verify.check_address WHERE case_id = :c"
                ),
                {"c": cid},
            )
        )
        .one()
        .r
    )
    await conn.execute(
        text(
            "UPDATE verify.check_address SET superseded_at = :n WHERE case_id = :c AND superseded_at IS NULL"
        ),
        {"n": now, "c": cid},
    )
    address_id = uuid.uuid4()
    await conn.execute(
        text(
            "INSERT INTO verify.check_address (address_id, tenant_id, case_id, revision, country, fields, postcode, created_at) "
            "VALUES (:id, :t, :c, :r, :co, CAST(:f AS jsonb), :p, :at)"
        ),
        {
            "id": address_id, "t": uuid.UUID(case.tenant_id), "c": cid, "r": rev + 1, "co": address.country,
            "f": json.dumps(_fields(address)), "p": postcode, "at": now,
        },
    )  # fmt: skip
    recorded = None
    if cm.CheckMethod.ADDRESS_LOOKUP in case.available:
        verdict, reasons = (
            (cc.Verdict.PASS, ())
            if postcode == "OK"
            else (cc.Verdict.REVIEW, ("POSTCODE_BAD_SHAPE",))
            if postcode == "BAD_SHAPE"
            else (cc.Verdict.INDETERMINATE, ("POSTCODE_NOT_CHECKED",))
        )
        rec = await check_cases.record_assertion(
            conn, case_id, assertion=Assertion.ADDRESS_FORMAT_VALID, verdict=verdict, method=cm.CheckMethod.ADDRESS_LOOKUP,
            source=FORMAT_SOURCE, reason_codes=reasons, confidence=None, observed_at=now, expires_at=None, recorded_by="address-format",
            now=now,
        )  # fmt: skip
        recorded = rec.verdict.value
    location = association = None
    if locator is not None and cm.CheckMethod.ADDRESS_LOOKUP in case.available:
        location = await _from_provider(
            conn,
            case_id,
            locator,
            address,
            subject_name or "",
            Assertion.ADDRESS_LOCATION_MATCH,
            cm.CheckMethod.ADDRESS_LOOKUP,
            now,
        )
    if source is not None and subject_name and cm.CheckMethod.ADDRESS_DATA_SOURCE in case.available:
        association = await _from_provider(
            conn,
            case_id,
            source,
            address,
            subject_name,
            Assertion.ADDRESS_ASSOCIATION_CONFIRMED,
            cm.CheckMethod.ADDRESS_DATA_SOURCE,
            now,
        )
    return {
        "location_outcome": location,
        "source_outcome": association,
        "address_id": str(address_id),
        "revision": rev + 1,
        "country": address.country,
        "postcode": postcode,
        "format_outcome": recorded,
        "note": "An address that is entered or formatted is not proof that anyone lives there.",
    }


def _code(raw: str) -> str:
    """A supplier's words as a stable reason code; never "fraud" (WF-05 section 21)."""
    code = re.sub(r"[^A-Z0-9_]", "_", raw.upper()).strip("_")[:60] or "PROVIDER_REASON"
    return "PROVIDER_REASON" if "FRAUD" in code or not code[0].isalpha() else code


async def _from_provider(
    conn: AsyncConnection,
    case_id: str,
    provider: ad.AddressProvider,
    address: ad.Address,
    name: str,
    assertion: Assertion,
    method: cm.CheckMethod,
    now: datetime,
) -> str:
    """A provider that fails or has no answer is UNAVAILABLE, never a pass or a fail. Only an explicit MATCH is a PASS; anything else is REVIEW."""
    answer = await asyncio.to_thread(ad.provider_answer, provider, name, address)
    if not answer.available:
        verdict, reasons, src = (
            cc.Verdict.UNAVAILABLE,
            tuple(_code(r) for r in answer.reasons) or ("DATA_SOURCE_UNAVAILABLE",),
            None,
        )
    elif (answer.match or "").upper() == "MATCH":
        verdict, reasons, src = cc.Verdict.PASS, (), answer.provider or "address-provider"
    else:
        verdict, reasons, src = (
            cc.Verdict.REVIEW,
            (_code(f"PROVIDER_{answer.match or 'NO_ANSWER'}"),),
            answer.provider or "address-provider",
        )
    rec = await check_cases.record_assertion(
        conn, case_id, assertion=assertion, verdict=verdict, method=method, source=src, reason_codes=tuple(reasons), confidence=None,
        observed_at=now, expires_at=None, recorded_by="address-provider", now=now,
    )  # fmt: skip
    return rec.verdict.value


async def _current_address(conn: AsyncConnection, case_id: str) -> tuple[uuid.UUID, ad.Address]:
    row = (
        await conn.execute(
            text(
                "SELECT address_id, fields FROM verify.check_address WHERE case_id = :c AND superseded_at IS NULL"
            ),
            {"c": uuid.UUID(case_id)},
        )
    ).first()
    if row is None:
        raise cc.CaseError("ADDRESS_REQUIRED", "give the address the document should support first")
    fields = row.fields if isinstance(row.fields, dict) else json.loads(row.fields)
    return row.address_id, ad.parse_address(fields)


async def add_document(
    conn: AsyncConnection,
    case_id: str,
    *,
    upload_ref: str,
    doc_type: str,
    issuer: str,
    issued_on: date,
    name_on_document: str,
    address_on_document: dict[str, Any],
    subject_name: str,
    now: datetime,
) -> dict[str, Any]:
    """`subject_name` is compared and never stored. What was read from the document is kept apart from the upload it was read from."""
    case = await _open_case(conn, case_id, frozenset({cm.CheckMethod.ADDRESS_DOCUMENT}))
    address_id, claimed = await _current_address(conn, case_id)
    policy = proof_policy()
    if doc_type not in ad.accepted_types(policy, case.market):
        raise cc.CaseError(
            "DOCUMENT_TYPE_NOT_OFFERED", "not an accepted document for this market (A06)"
        )
    read_addr = ad.parse_address(address_on_document)
    doc = ad.ProofDocument(doc_type, issuer, issued_on, name_on_document, read_addr)
    a = ad.assess_proof(doc, claimed, subject_name, policy, today=now.date(), market=case.market)
    document_id = uuid.uuid4()
    read = {
        "issuer": issuer,
        "issued_on": issued_on.isoformat(),
        "name_on_document": name_on_document,
        "address": _fields(read_addr),
    }
    await conn.execute(
        text(
            "INSERT INTO verify.check_address_document (document_id, tenant_id, case_id, address_id, upload_ref, doc_type, read_fields, support, "
            "findings, policy_status, status, created_at) VALUES (:id, :t, :c, :a, :u, :d, CAST(:r AS jsonb), :s, :f, :ps, 'AWAITING_REVIEW', :at)"
        ),
        {
            "id": document_id, "t": uuid.UUID(case.tenant_id), "c": uuid.UUID(case_id), "a": address_id, "u": upload_ref, "d": doc_type,
            "r": json.dumps(read), "s": a.support.value, "f": [f.code for f in a.findings], "ps": a.policy_status, "at": now,
        },
    )  # fmt: skip
    return {
        "document_id": str(document_id),
        "status": ar.Status.AWAITING_REVIEW.value,
        "assessment": a.support.value,
        "findings": [f.code for f in a.findings],
        "policy_status": a.policy_status,
        "notes": list(a.notes),
    }


async def _document(conn: AsyncConnection, case_id: str, document_id: str) -> Any:
    try:
        did = uuid.UUID(document_id)
    except ValueError as e:
        raise cc.CaseError("DOCUMENT_NOT_FOUND") from e
    row = (
        await conn.execute(
            text(
                "SELECT document_id, case_id, support, findings, policy_status, status FROM verify.check_address_document "
                "WHERE document_id = :d FOR UPDATE"
            ),
            {"d": did},
        )
    ).first()
    if row is None or str(row.case_id) != case_id:
        raise cc.CaseError("DOCUMENT_NOT_FOUND")
    return row


def _assessment(row: Any) -> ad.Assessment:
    return ad.Assessment(
        ad.Support(row.support),
        tuple(ad.Finding(c) for c in row.findings),
        None,
        "",
        policy_status=row.policy_status,
    )


async def _step(
    conn: AsyncConnection,
    row: Any,
    step: str,
    finding: str,
    reasons: tuple[str, ...],
    by: str,
    now: datetime,
) -> None:
    case = await check_cases.get_case(conn, str(row.case_id))
    await conn.execute(
        text(
            "INSERT INTO verify.check_address_review (review_id, tenant_id, document_id, step, finding, reason_codes, decided_by, decided_at) "
            "VALUES (:id, :t, :d, :s, :f, :r, :by, :at)"
        ),
        {
            "id": uuid.uuid4(),
            "t": uuid.UUID(case.tenant_id),
            "d": row.document_id,
            "s": step,
            "f": finding,
            "r": list(reasons),
            "by": by,
            "at": now,
        },
    )


async def _record(
    conn: AsyncConnection, case_id: str, res: ar.Result, by: str, now: datetime
) -> None:
    await check_cases.record_assertion(
        conn, case_id, assertion=res.assertion, verdict=res.verdict, method=cm.CheckMethod.ADDRESS_DOCUMENT, source=f"reviewer:{by}",
        reason_codes=res.reason_codes, confidence=None, observed_at=now, expires_at=None, recorded_by=by, now=now,
    )  # fmt: skip


async def review(
    conn: AsyncConnection,
    case_id: str,
    document_id: str,
    *,
    finding: ar.Finding,
    reasons: tuple[str, ...],
    by: str,
    now: datetime,
) -> dict[str, Any]:
    await _open_case(conn, case_id, frozenset({cm.CheckMethod.ADDRESS_DOCUMENT}))
    row = await _document(conn, case_id, document_id)
    res = ar.maker(
        _assessment(row),
        finding,
        reasons,
        status=ar.Status(row.status),
        policy_status=row.policy_status,
    )
    await _step(conn, row, "MAKER", finding.value, reasons, by, now)
    await conn.execute(
        text("UPDATE verify.check_address_document SET status = :s WHERE document_id = :d"),
        {"s": res.status.value, "d": row.document_id},
    )
    await _record(conn, case_id, res, by, now)
    return {
        "document_id": document_id,
        "status": res.status.value,
        "recorded": {res.assertion.value: res.verdict.value},
    }


async def check(
    conn: AsyncConnection,
    case_id: str,
    document_id: str,
    *,
    agrees: bool,
    reasons: tuple[str, ...],
    by: str,
    now: datetime,
) -> dict[str, Any]:
    await _open_case(conn, case_id, frozenset({cm.CheckMethod.ADDRESS_DOCUMENT}))
    row = await _document(conn, case_id, document_id)
    maker_by = (
        await conn.execute(
            text(
                "SELECT decided_by FROM verify.check_address_review WHERE document_id = :d AND step = 'MAKER'"
            ),
            {"d": row.document_id},
        )
    ).scalar_one_or_none()
    res = ar.checker(
        status=ar.Status(row.status),
        maker_by=maker_by or "",
        checker_by=by,
        agrees=agrees,
        reasons=reasons,
    )
    await _step(conn, row, "CHECKER", "AGREED" if agrees else "DISAGREED", reasons, by, now)
    await conn.execute(
        text("UPDATE verify.check_address_document SET status = :s WHERE document_id = :d"),
        {"s": res.status.value, "d": row.document_id},
    )
    await _record(conn, case_id, res, by, now)
    return {
        "document_id": document_id,
        "status": res.status.value,
        "recorded": {res.assertion.value: res.verdict.value},
    }


async def documents(conn: AsyncConnection, case_id: str) -> list[dict[str, Any]]:
    await check_cases.get_case(conn, case_id)
    rows = (
        await conn.execute(
            text(
                "SELECT document_id, doc_type, support, findings, policy_status, status, created_at FROM verify.check_address_document "
                "WHERE case_id = :c ORDER BY created_at"
            ),
            {"c": uuid.UUID(case_id)},
        )
    ).all()
    return [
        {
            "document_id": str(r.document_id), "doc_type": r.doc_type, "assessment": r.support, "findings": list(r.findings),
            "policy_status": r.policy_status, "status": r.status, "created_at": r.created_at.isoformat(),
        }
        for r in rows
    ]  # fmt: skip


# ---------------------------------------------------------------------------------------------------------------------- postal code (A08)

POSTAL_SOURCE = "zid-postal-code@1"
_POSTAL = "challenge_id, case_id, address_revision, secret_hash, state, expires_at, attempt_count, created_at, consumed_at"


@lru_cache(maxsize=1)
def postal_ttl_days() -> dict[str, int]:
    doc: dict[str, Any] = (
        yaml.safe_load((CONFIG / "verification/postal-challenge.yaml").read_text(encoding="utf-8"))
        or {}
    )
    out = {str(k).upper(): int(v) for k, v in (doc.get("ttl_days") or {}).items()}
    for market, days in out.items():
        if not re.fullmatch(r"[A-Z]{2}", market) or not pc.MIN_TTL.days <= days <= pc.MAX_TTL.days:
            raise ValueError(f"postal-challenge.yaml: {market}")
    return out


async def _postal_key() -> bytes:
    mac = current_runtime().mac_key
    return await asyncio.to_thread(mac.mac, b"zid.wf05.postal-code.v1")


@dataclass(frozen=True, slots=True)
class PostalDispatch:
    """What a mail house needs, after the transaction commits. The only place the address and the code meet."""

    challenge_id: str
    tenant_id: str
    address: dict[str, str]
    code: str
    expires_at: datetime


def _postal(r: Any) -> pc.Postal:
    return pc.Postal(
        str(r.challenge_id), r.address_revision, bytes(r.secret_hash), pc.State(r.state), r.expires_at, r.attempt_count, r.created_at,
        r.consumed_at,
    )  # fmt: skip


async def _load_postal(conn: AsyncConnection, challenge_id: str) -> Any:
    try:
        cid = uuid.UUID(challenge_id)
    except ValueError as e:
        raise cc.CaseError("CHALLENGE_NOT_FOUND") from e
    sql = f"SELECT {_POSTAL} FROM verify.check_postal_challenge WHERE challenge_id = :id FOR UPDATE"
    row = (await conn.execute(text(sql), {"id": cid})).first()
    if row is None:
        raise cc.CaseError("CHALLENGE_NOT_FOUND")
    return row


async def _save_postal(conn: AsyncConnection, p: pc.Postal) -> None:
    await conn.execute(
        text(
            "UPDATE verify.check_postal_challenge SET state = :s, attempt_count = :a, consumed_at = :c WHERE challenge_id = :id"
        ),
        {
            "s": p.state.value,
            "a": p.attempt_count,
            "c": p.consumed_at,
            "id": uuid.UUID(p.challenge_id),
        },
    )


async def _address_revision(conn: AsyncConnection, case_id: str) -> int:
    sql = "SELECT revision FROM verify.check_address WHERE case_id = :c AND superseded_at IS NULL"
    return int((await conn.execute(text(sql), {"c": uuid.UUID(case_id)})).scalar_one_or_none() or 0)


async def start_postal(
    conn: AsyncConnection, case_id: str, *, now: datetime
) -> tuple[dict[str, Any], PostalDispatch]:
    """A code posted to the current address. Any earlier open postal code of the case is revoked."""
    case = await _open_case(conn, case_id, frozenset({cm.CheckMethod.ADDRESS_POSTAL_CHALLENGE}))
    days = postal_ttl_days().get(case.market)
    if days is None:
        raise cc.CaseError(
            "POSTAL_NOT_CONFIGURED_FOR_MARKET", "no approved postal expiry for this market"
        )
    address_id, address = await _current_address(conn, case_id)
    rev = await _address_revision(conn, case_id)
    open_sql = (
        f"SELECT {_POSTAL} FROM verify.check_postal_challenge WHERE case_id = :c "
        "AND state IN ('PENDING_DISPATCH', 'DISPATCHED') FOR UPDATE"
    )
    for r in (await conn.execute(text(open_sql), {"c": uuid.UUID(case_id)})).all():
        await _save_postal(conn, replace(_postal(r), state=pc.State.REVOKED))
    challenge_id = str(uuid.uuid4())
    p, code = pc.issue(
        challenge_id=challenge_id,
        address_revision=rev,
        key=await _postal_key(),
        ttl=timedelta(days=days),
        now=now,
    )
    await conn.execute(
        text(
            "INSERT INTO verify.check_postal_challenge (tenant_id, address_id, challenge_id, case_id, address_revision, secret_hash, state, "
            "expires_at, attempt_count, created_at) VALUES (:t, :ad, :id, :c, :r, :h, :s, :ex, 0, :at)"
        ),
        {
            "t": uuid.UUID(case.tenant_id), "ad": address_id, "id": uuid.UUID(challenge_id), "c": uuid.UUID(case_id), "r": rev,
            "h": p.secret_hash, "s": p.state.value, "ex": p.expires_at, "at": now,
        },
    )  # fmt: skip
    view = {
        "challenge_id": challenge_id,
        "status": p.state.value,
        "expires_at": p.expires_at.isoformat(),
        "note": "A posted code shows access to mail at the address during the challenge, not ownership or identity.",
    }
    return view, PostalDispatch(challenge_id, case.tenant_id, _fields(address), code, p.expires_at)


async def mark_dispatched(
    conn: AsyncConnection, challenge_id: str, *, dispatch_ref: str, now: datetime
) -> None:
    """The mail house accepted the letter: an audited dispatch (A08)."""
    pc.dispatched(_postal(await _load_postal(conn, challenge_id)))
    await conn.execute(
        text(
            "UPDATE verify.check_postal_challenge SET state = 'DISPATCHED', dispatch_ref = :r, dispatched_at = :n WHERE challenge_id = :id"
        ),
        {"r": dispatch_ref[:128], "n": now, "id": uuid.UUID(challenge_id)},
    )


async def confirm_postal(
    conn: AsyncConnection, case_id: str, challenge_id: str, code: str, *, now: datetime
) -> dict[str, Any]:
    case = await _open_case(conn, case_id, frozenset({cm.CheckMethod.ADDRESS_POSTAL_CHALLENGE}))
    row = await _load_postal(conn, challenge_id)
    if str(row.case_id) != case.case_id:
        raise cc.CaseError("CHALLENGE_NOT_FOUND")
    before = _postal(row)
    after, ok = pc.confirm(
        before,
        code,
        key=await _postal_key(),
        address_revision=await _address_revision(conn, case_id),
        now=now,
    )
    if after != before:
        await _save_postal(conn, after)
    if ok:
        await check_cases.record_assertion(
            conn, case_id, assertion=Assertion.ADDRESS_ASSOCIATION_CONFIRMED, verdict=cc.Verdict.PASS,
            method=cm.CheckMethod.ADDRESS_POSTAL_CHALLENGE, source=POSTAL_SOURCE, reason_codes=(), confidence=None, observed_at=now,
            expires_at=None, recorded_by="postal-code", now=now,
        )  # fmt: skip
    return {
        "challenge_id": challenge_id,
        "status": after.state.value,
        "verified": ok,
        "attempts_left": max(0, pc.MAX_ATTEMPTS - after.attempt_count),
    }


__all__ = [
    "FORMAT_SOURCE",
    "POSTAL_SOURCE",
    "PostalDispatch",
    "add_document",
    "check",
    "confirm_postal",
    "documents",
    "mark_dispatched",
    "review",
    "set_address",
    "start_postal",
]
