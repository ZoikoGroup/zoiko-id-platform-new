"""Storage for the market matrix (`commercial.market_matrix*`, migration commercial_0027; ZID-COM-WF-2026-11 v3.0 WF-I01, WF-I05).

The rules live in `domain/market_matrix.py` and are repeated by the database: rows and approvals only on a DRAFT, no self-approval, one person one role, four
approvals before activation, an activated matrix immutable. Functions here read and write; they add no rule of their own beyond naming the refusal.
"""

from __future__ import annotations

import uuid
from datetime import datetime
from typing import Any

from sqlalchemy import text
from sqlalchemy.exc import DBAPIError
from sqlalchemy.ext.asyncio import AsyncConnection

from zid_platform.modules.commercial.domain.catalogue import CommercialError
from zid_platform.modules.commercial.domain.market_matrix import ROLES, Row

_COLUMNS = (
    "family, country, seller_entity, merchant_route, currency, tax_class, data_region, offer_ref, provider_rights_ref, "
    "legal_basis_ref, regulated_status_ref, release_evidence_ref, payment_configured, tax_configured, "
    "provider_rights_expires, regulated_status_expires"
)


def _row(r: Any) -> Row:
    return Row(
        family=r["family"], country=r["country"], seller_entity=r["seller_entity"],
        merchant_route=r["merchant_route"], currency=r["currency"], tax_class=r["tax_class"],
        data_region=r["data_region"], offer_ref=r["offer_ref"], legal_basis_ref=r["legal_basis_ref"],
        release_evidence_ref=r["release_evidence_ref"], payment_configured=r["payment_configured"],
        tax_configured=r["tax_configured"], provider_rights_ref=r["provider_rights_ref"],
        regulated_status_ref=r["regulated_status_ref"], provider_rights_expires=r["provider_rights_expires"],
        regulated_status_expires=r["regulated_status_expires"],
    )  # fmt: skip


async def matrix(conn: AsyncConnection, matrix_id: str) -> dict[str, Any] | None:
    r = (
        (
            await conn.execute(
                text("SELECT * FROM commercial.market_matrix WHERE matrix_id = :i"),
                {"i": uuid.UUID(matrix_id)},
            )
        )
        .mappings()
        .first()
    )
    return dict(r) if r else None


async def matrices(conn: AsyncConnection) -> list[dict[str, Any]]:
    rows = (
        (await conn.execute(text("SELECT * FROM commercial.market_matrix ORDER BY version DESC")))
        .mappings()
        .all()
    )
    return [dict(r) for r in rows]


async def rows(conn: AsyncConnection, matrix_id: str) -> list[Row]:
    found = (
        (
            await conn.execute(
                text(
                    f"SELECT {_COLUMNS} FROM commercial.market_matrix_row WHERE matrix_id = :i ORDER BY family, country"
                ),
                {"i": uuid.UUID(matrix_id)},
            )
        )
        .mappings()
        .all()
    )
    return [_row(r) for r in found]


async def approvals(conn: AsyncConnection, matrix_id: str) -> list[dict[str, Any]]:
    found = (
        (
            await conn.execute(
                text(
                    "SELECT role, approver, approved_at FROM commercial.market_matrix_approval "
                    "WHERE matrix_id = :i ORDER BY role"
                ),
                {"i": uuid.UUID(matrix_id)},
            )
        )
        .mappings()
        .all()
    )
    return [dict(r) for r in found]


async def active(conn: AsyncConnection, now: datetime) -> dict[str, Any] | None:
    r = (
        (
            await conn.execute(
                text(
                    "SELECT * FROM commercial.market_matrix WHERE status = 'ACTIVE' AND effective_from <= :n "
                    "ORDER BY version DESC LIMIT 1"
                ),
                {"n": now},
            )
        )
        .mappings()
        .first()
    )
    return dict(r) if r else None


async def active_rows(
    conn: AsyncConnection, now: datetime
) -> tuple[dict[str, Any] | None, dict[tuple[str, str], Row]]:
    """The ACTIVE matrix in force at `now` and its rows by (family, country). None and an empty map when no matrix is active: every fact then stays unrecorded."""
    m = await active(conn, now)
    if m is None:
        return None, {}
    return m, {(r.family, r.country): r for r in await rows(conn, str(m["matrix_id"]))}


async def create_draft(
    conn: AsyncConnection, *, copy_of: str | None, created_by: str, note: str | None
) -> dict[str, Any]:
    version = int(
        (
            await conn.execute(
                text("SELECT coalesce(max(version), 0) + 1 FROM commercial.market_matrix")
            )
        ).scalar_one()
    )
    new_id = uuid.uuid4()
    if copy_of and await matrix(conn, copy_of) is None:
        raise CommercialError("MARKET_MATRIX_NOT_FOUND")
    await conn.execute(
        text(
            "INSERT INTO commercial.market_matrix (matrix_id, version, status, created_by, note) "
            "VALUES (:i, :v, 'DRAFT', :a, :n)"
        ),
        {"i": new_id, "v": version, "a": uuid.UUID(created_by), "n": note},
    )
    if copy_of:  # the rows are copied; the approvals are NOT: a copy is signed afresh
        await conn.execute(
            text(
                f"INSERT INTO commercial.market_matrix_row (matrix_id, {_COLUMNS}) "
                f"SELECT :new, {_COLUMNS} FROM commercial.market_matrix_row WHERE matrix_id = :old"
            ),
            {"new": new_id, "old": uuid.UUID(copy_of)},
        )
    return {"matrix_id": str(new_id), "version": version, "status": "DRAFT"}


async def put_row(conn: AsyncConnection, matrix_id: str, row: Row) -> None:
    m = await matrix(conn, matrix_id)
    if m is None:
        raise CommercialError("MARKET_MATRIX_NOT_FOUND")
    if m["status"] != "DRAFT":
        raise CommercialError("MARKET_MATRIX_NOT_DRAFT", "an activated market matrix is immutable")
    await conn.execute(
        text(
            f"INSERT INTO commercial.market_matrix_row (matrix_id, {_COLUMNS}) VALUES (:m, :family, :country, "
            ":seller_entity, :merchant_route, :currency, :tax_class, :data_region, :offer_ref, :provider_rights_ref, "
            ":legal_basis_ref, :regulated_status_ref, :release_evidence_ref, :payment_configured, :tax_configured, "
            ":provider_rights_expires, :regulated_status_expires) "
            "ON CONFLICT (matrix_id, family, country) DO UPDATE SET seller_entity = EXCLUDED.seller_entity, "
            "merchant_route = EXCLUDED.merchant_route, currency = EXCLUDED.currency, tax_class = EXCLUDED.tax_class, "
            "data_region = EXCLUDED.data_region, offer_ref = EXCLUDED.offer_ref, "
            "provider_rights_ref = EXCLUDED.provider_rights_ref, legal_basis_ref = EXCLUDED.legal_basis_ref, "
            "regulated_status_ref = EXCLUDED.regulated_status_ref, release_evidence_ref = EXCLUDED.release_evidence_ref, "
            "payment_configured = EXCLUDED.payment_configured, tax_configured = EXCLUDED.tax_configured, "
            "provider_rights_expires = EXCLUDED.provider_rights_expires, regulated_status_expires = EXCLUDED.regulated_status_expires"
        ),
        {"m": uuid.UUID(matrix_id), **{k: getattr(row, k) for k in row.__slots__}},
    )


async def delete_row(conn: AsyncConnection, matrix_id: str, family: str, country: str) -> bool:
    m = await matrix(conn, matrix_id)
    if m is None:
        raise CommercialError("MARKET_MATRIX_NOT_FOUND")
    if m["status"] != "DRAFT":
        raise CommercialError("MARKET_MATRIX_NOT_DRAFT", "an activated market matrix is immutable")
    done = await conn.execute(
        text(
            "DELETE FROM commercial.market_matrix_row WHERE matrix_id = :m AND family = :f AND country = :c"
        ),
        {"m": uuid.UUID(matrix_id), "f": family, "c": country},
    )
    return bool(done.rowcount)


async def approve(conn: AsyncConnection, matrix_id: str, *, role: str, approver: str) -> None:
    """One role signs. The author cannot (also checked by the table), one person cannot sign twice, and a role cannot be signed twice."""
    if role not in ROLES:
        raise CommercialError("MARKET_MATRIX_ROLE_UNKNOWN", role)
    m = await matrix(conn, matrix_id)
    if m is None:
        raise CommercialError("MARKET_MATRIX_NOT_FOUND")
    if m["status"] != "DRAFT":
        raise CommercialError("MARKET_MATRIX_NOT_DRAFT")
    if str(m["created_by"]) == approver:
        raise CommercialError(
            "MARKET_MATRIX_SELF_APPROVAL", "the author cannot approve their own market matrix"
        )
    existing = await approvals(conn, matrix_id)
    if any(a["role"] == role for a in existing):
        raise CommercialError("MARKET_MATRIX_ROLE_ALREADY_SIGNED", role)
    if any(str(a["approver"]) == approver for a in existing):
        raise CommercialError(
            "MARKET_MATRIX_PERSON_ALREADY_SIGNED", "one person cannot sign for two roles"
        )
    try:
        await conn.execute(
            text(
                "INSERT INTO commercial.market_matrix_approval (matrix_id, role, approver) VALUES (:m, :r, :a)"
            ),
            {"m": uuid.UUID(matrix_id), "r": role, "a": uuid.UUID(approver)},
        )
    except DBAPIError as e:
        raise CommercialError("MARKET_MATRIX_APPROVAL_REFUSED", str(e.orig)[:160]) from e


async def withdraw(conn: AsyncConnection, matrix_id: str) -> None:
    m = await matrix(conn, matrix_id)
    if m is None:
        raise CommercialError("MARKET_MATRIX_NOT_FOUND")
    if m["status"] != "DRAFT":
        raise CommercialError("MARKET_MATRIX_NOT_DRAFT")
    await conn.execute(
        text("UPDATE commercial.market_matrix SET status = 'WITHDRAWN' WHERE matrix_id = :i"),
        {"i": uuid.UUID(matrix_id)},
    )


async def activate(
    conn: AsyncConnection,
    matrix_id: str,
    *,
    activator: str,
    effective_from: datetime,
    now: datetime,
) -> None:
    """DRAFT to ACTIVE with all four approvals, by someone other than the author. The previously active matrix becomes SUPERSEDED."""
    m = await matrix(conn, matrix_id)
    if m is None:
        raise CommercialError("MARKET_MATRIX_NOT_FOUND")
    if m["status"] != "DRAFT":
        raise CommercialError("MARKET_MATRIX_NOT_DRAFT")
    if str(m["created_by"]) == activator:
        raise CommercialError(
            "MARKET_MATRIX_SELF_APPROVAL", "the author cannot activate their own market matrix"
        )
    signed = {a["role"] for a in await approvals(conn, matrix_id)}
    missing = [r for r in ROLES if r not in signed]
    if missing:
        raise CommercialError("MARKET_MATRIX_APPROVALS_MISSING", ",".join(missing))
    if not await rows(conn, matrix_id):
        raise CommercialError("MARKET_MATRIX_EMPTY", "a matrix needs rows")
    await conn.execute(
        text(
            "UPDATE commercial.market_matrix SET status = 'SUPERSEDED' WHERE status = 'ACTIVE' AND version < :v"
        ),
        {"v": m["version"]},
    )
    await conn.execute(
        text(
            "UPDATE commercial.market_matrix SET status = 'ACTIVE', effective_from = :e, activated_by = :a, "
            "activated_at = :n WHERE matrix_id = :i"
        ),
        {"e": effective_from, "a": uuid.UUID(activator), "n": now, "i": uuid.UUID(matrix_id)},
    )
