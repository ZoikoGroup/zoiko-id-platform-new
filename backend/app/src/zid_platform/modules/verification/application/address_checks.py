"""WF-05 address claims and proof of address on a case (ZID-DTV-WF-05 DTV-17, §02, A05-A07, A10; CR-115).

set_address     the address as typed; a change is a new revision. With ADDRESS_LOOKUP cleared, the built-in format check records
                ADDRESS_FORMAT_VALID (PASS only where the postcode shape is known and right; never a location or a residence)
add_document    a proof of address document: an evidence upload reference, its type, what was READ from it; the market's rule assesses it
                and it waits for a person (ADDRESS_DOCUMENT cleared)
review          the maker's finding (A10); CONFIRMED records ADDRESS_DOCUMENT_REVIEWED PASS and waits for a checker
check           a second, different person; agreement records ADDRESS_ASSOCIATION_CONFIRMED PASS
"""

from __future__ import annotations

import json
import uuid
from datetime import date, datetime
from typing import Any

from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncConnection

from zid_platform.modules.verification.application import check_cases
from zid_platform.modules.verification.domain import address as ad
from zid_platform.modules.verification.domain import address_review as ar
from zid_platform.modules.verification.domain import check_cases as cc
from zid_platform.modules.verification.domain import check_methods as cm
from zid_platform.modules.verification.domain.assertions import Assertion
from zid_platform.modules.verification.infrastructure.address_policy_registry import proof_policy

FORMAT_SOURCE = "zid-address-format@1"
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
    conn: AsyncConnection, case_id: str, raw: dict[str, Any], *, now: datetime
) -> dict[str, Any]:
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
    return {
        "address_id": str(address_id),
        "revision": rev + 1,
        "country": address.country,
        "postcode": postcode,
        "format_outcome": recorded,
        "note": "An address that is entered or formatted is not proof that anyone lives there.",
    }


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


__all__ = ["FORMAT_SOURCE", "add_document", "check", "documents", "review", "set_address"]
