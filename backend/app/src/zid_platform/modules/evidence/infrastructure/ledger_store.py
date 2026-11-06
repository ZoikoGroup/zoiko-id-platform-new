"""PostgreSQL access for evidence.ledger_entry and evidence.checkpoint (DEC-F2 §2).

Sequence numbers are assigned under a per-partition transaction-scoped advisory lock, taken by
both appends and checkpointing, so a partition's sequence is gapless and its checkpoints never race
an append. Times written to the ledger come from the database clock (`clock_timestamp()`), never
from the caller; a trigger refuses any other value.
"""

from __future__ import annotations

import json
import uuid
from collections.abc import AsyncIterator, Mapping
from contextlib import asynccontextmanager
from datetime import datetime
from typing import Any

from sqlalchemy import Row, text
from sqlalchemy.ext.asyncio import AsyncConnection

from zid_platform.modules.evidence.domain.ledger import PLATFORM_SCOPE

LOCK_CLASS = 0x45564944  # 'EVID': advisory-lock namespace of the evidence ledger

ENTRY_COLUMNS = (
    "partition_id, sequence_no, tenant_id, record_type, record_id, record_hash, "
    "previous_entry_hash, entry_hash, evidence_class::text AS evidence_class, committed_at, "
    "effective_at, observed_at, signer_key_id, entry_signature"
)
CHECKPOINT_COLUMNS = (
    "checkpoint_id, partition_id, tenant_id, first_sequence, last_sequence, merkle_root, "
    "previous_checkpoint_hash, checkpoint_hash, created_at, signer_key_id, alg, "
    "checkpoint_signature, signer_public_jwk, external_timestamp_ref"
)


@asynccontextmanager
async def platform_scope(conn: AsyncConnection) -> AsyncIterator[None]:
    """Make platform-security rows (tenant_id NULL) visible for the enclosed statements only."""
    previous = (
        await conn.execute(text("SELECT current_setting('app.evidence_scope', true)"))
    ).scalar_one()
    await conn.execute(
        text("SELECT set_config('app.evidence_scope', :s, true)"), {"s": PLATFORM_SCOPE}
    )
    try:
        yield
    finally:
        await conn.execute(
            text("SELECT set_config('app.evidence_scope', :s, true)"), {"s": previous or ""}
        )


async def lock_partition(conn: AsyncConnection, partition_id: str) -> None:
    await conn.execute(
        text("SELECT pg_advisory_xact_lock(:c, hashtext(:p))"), {"c": LOCK_CLASS, "p": partition_id}
    )


async def db_clock(conn: AsyncConnection) -> datetime:
    value: datetime = (await conn.execute(text("SELECT clock_timestamp()"))).scalar_one()
    return value


async def head(conn: AsyncConnection, partition_id: str) -> Row[Any] | None:
    return (
        await conn.execute(
            text(
                "SELECT sequence_no, entry_hash FROM evidence.ledger_entry "
                "WHERE partition_id = :p ORDER BY sequence_no DESC LIMIT 1"
            ),
            {"p": partition_id},
        )
    ).first()


async def find_entry(
    conn: AsyncConnection, partition_id: str, record_type: str, record_id: str
) -> Row[Any] | None:
    return (
        await conn.execute(
            text(
                f"SELECT {ENTRY_COLUMNS} FROM evidence.ledger_entry WHERE partition_id = :p "
                "AND record_type = :rt AND record_id = :rid"
            ),
            {"p": partition_id, "rt": record_type, "rid": record_id},
        )
    ).first()


async def find_record(
    conn: AsyncConnection, record_id: str, record_type: str | None = None
) -> list[Row[Any]]:
    """Entries for a record id visible in this transaction (RLS: the caller's tenant only)."""
    return list(
        (
            await conn.execute(
                text(
                    f"SELECT {ENTRY_COLUMNS} FROM evidence.ledger_entry WHERE record_id = :rid "
                    "AND (CAST(:rt AS text) IS NULL OR record_type = :rt) "
                    "ORDER BY partition_id, sequence_no LIMIT 2"
                ),
                {"rid": record_id, "rt": record_type},
            )
        ).all()
    )


async def insert_entry(conn: AsyncConnection, values: Mapping[str, Any]) -> None:
    await conn.execute(
        text("""
            INSERT INTO evidence.ledger_entry (partition_id, sequence_no, tenant_id, record_type,
                record_id, record_hash, previous_entry_hash, entry_hash, evidence_class,
                committed_at, effective_at, observed_at, signer_key_id, entry_signature)
            VALUES (:partition_id, :sequence_no, :tenant_id, :record_type, :record_id,
                :record_hash, :previous_entry_hash, :entry_hash,
                CAST(:evidence_class AS evidence.evidence_class), :committed_at, :effective_at,
                :observed_at, :signer_key_id, :entry_signature)
        """),
        {
            **values,
            "tenant_id": uuid.UUID(values["tenant_id"]) if values["tenant_id"] else None,
        },
    )


async def entries(
    conn: AsyncConnection, partition_id: str, first: int, last: int | None = None
) -> list[Row[Any]]:
    return list(
        (
            await conn.execute(
                text(
                    f"SELECT {ENTRY_COLUMNS} FROM evidence.ledger_entry WHERE partition_id = :p "
                    "AND sequence_no >= :f AND (CAST(:l AS bigint) IS NULL OR sequence_no <= :l) "
                    "ORDER BY sequence_no"
                ),
                {"p": partition_id, "f": first, "l": last},
            )
        ).all()
    )


async def entry_at(conn: AsyncConnection, partition_id: str, sequence_no: int) -> Row[Any] | None:
    return (
        await conn.execute(
            text(
                f"SELECT {ENTRY_COLUMNS} FROM evidence.ledger_entry "
                "WHERE partition_id = :p AND sequence_no = :s"
            ),
            {"p": partition_id, "s": sequence_no},
        )
    ).first()


async def last_checkpoint(conn: AsyncConnection, partition_id: str) -> Row[Any] | None:
    return (
        await conn.execute(
            text(
                f"SELECT {CHECKPOINT_COLUMNS} FROM evidence.checkpoint WHERE partition_id = :p "
                "ORDER BY last_sequence DESC LIMIT 1"
            ),
            {"p": partition_id},
        )
    ).first()


async def checkpoints(
    conn: AsyncConnection,
    partition_id: str,
    *,
    limit: int | None = None,
    newest_first: bool = False,
) -> list[Row[Any]]:
    order = "DESC" if newest_first else "ASC"
    return list(
        (
            await conn.execute(
                text(
                    f"SELECT {CHECKPOINT_COLUMNS} FROM evidence.checkpoint WHERE partition_id = :p "
                    f"ORDER BY first_sequence {order} LIMIT :n"
                ),
                {"p": partition_id, "n": limit if limit is not None else 2**62},
            )
        ).all()
    )


async def checkpoint_covering(
    conn: AsyncConnection, partition_id: str, sequence_no: int
) -> Row[Any] | None:
    return (
        await conn.execute(
            text(
                f"SELECT {CHECKPOINT_COLUMNS} FROM evidence.checkpoint WHERE partition_id = :p "
                "AND first_sequence <= :s AND last_sequence >= :s"
            ),
            {"p": partition_id, "s": sequence_no},
        )
    ).first()


async def insert_checkpoint(conn: AsyncConnection, values: Mapping[str, Any]) -> None:
    await conn.execute(
        text("""
            INSERT INTO evidence.checkpoint (checkpoint_id, partition_id, tenant_id, first_sequence,
                last_sequence, merkle_root, previous_checkpoint_hash, checkpoint_hash, created_at,
                signer_key_id, alg, checkpoint_signature, signer_public_jwk, external_timestamp_ref)
            VALUES (:checkpoint_id, :partition_id, :tenant_id, :first_sequence, :last_sequence,
                :merkle_root, :previous_checkpoint_hash, :checkpoint_hash, :created_at,
                :signer_key_id, :alg, :checkpoint_signature, CAST(:signer_public_jwk AS jsonb),
                :external_timestamp_ref)
        """),
        {
            **values,
            "checkpoint_id": uuid.UUID(values["checkpoint_id"]),
            "tenant_id": uuid.UUID(values["tenant_id"]) if values["tenant_id"] else None,
            "signer_public_jwk": json.dumps(values["signer_public_jwk"]),
        },
    )


async def set_timestamp_ref(conn: AsyncConnection, checkpoint_id: str, ref: str) -> bool:
    """Fill a checkpoint's external timestamp once (the only change the ledger triggers allow)."""
    result = await conn.execute(
        text(
            "UPDATE evidence.checkpoint SET external_timestamp_ref = :r "
            "WHERE checkpoint_id = :id AND external_timestamp_ref IS NULL"
        ),
        {"r": ref, "id": uuid.UUID(checkpoint_id)},
    )
    return bool(result.rowcount)


async def unanchored_checkpoints(conn: AsyncConnection, limit: int = 100) -> list[Row[Any]]:
    return list(
        (
            await conn.execute(
                text(
                    f"SELECT {CHECKPOINT_COLUMNS} FROM evidence.checkpoint "
                    "WHERE external_timestamp_ref IS NULL ORDER BY created_at LIMIT :n"
                ),
                {"n": limit},
            )
        ).all()
    )


async def partitions_with_entries(conn: AsyncConnection) -> list[str]:
    """Every partition that has at least one ledger entry visible in this transaction (the worker role sees all of them)."""
    rows = await conn.execute(
        text(
            "SELECT partition_id FROM evidence.ledger_entry GROUP BY partition_id ORDER BY partition_id"
        )
    )
    return [str(r.partition_id) for r in rows]


async def unsealed_partitions(conn: AsyncConnection) -> list[Row[Any]]:
    """Partitions with entries after their last checkpoint, visible in this transaction (the
    checkpointer's role reads every partition; a tenant transaction sees only its own)."""
    return list(
        (
            await conn.execute(
                text("""
                    SELECT e.partition_id, min(e.committed_at) AS oldest,
                           bool_or(e.evidence_class = 'E3') AS has_e3, count(*) AS unsealed
                    FROM evidence.ledger_entry e
                    WHERE e.sequence_no > coalesce((SELECT max(c.last_sequence)
                        FROM evidence.checkpoint c WHERE c.partition_id = e.partition_id), 0)
                    GROUP BY e.partition_id
                    ORDER BY e.partition_id
                """)
            )
        ).all()
    )
