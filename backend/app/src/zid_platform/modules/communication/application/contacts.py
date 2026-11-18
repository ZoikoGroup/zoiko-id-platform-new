"""Where a tenant's email goes (Communication Estate §03 SENSITIVE DATA / PRIVACY).

The platform keeps only a keyed hash of any identifier (`core.principal_identifier`), so it cannot mail a
principal from what it already holds. This module is the one place a deliverable address is kept:

- the address is stored in the evidence vault, encrypted under the person's erasable subject key for a
  principal's own contact, or under the cell data key for an organization role contact (billing, security,
  technical, owner). Erasing the subject therefore makes their address unrecoverable with everything else;
- `platform.email_contact` keeps the vault object id and a keyed hash used to match suppressions;
- reading an address back decrypts it only inside `resolve_*`, at send time.
"""

from __future__ import annotations

import asyncio
import re
from dataclasses import dataclass
from email.utils import parseaddr

from sqlalchemy.ext.asyncio import AsyncConnection
from zid_crypto.handles import handle

from zid_platform.modules.communication.infrastructure import store
from zid_platform.modules.evidence.ports import current_runtime, read_object, store_object

ROLE_PURPOSES = ("OWNER", "BILLING", "SECURITY", "TECHNICAL")
_ADDRESS = re.compile(
    r"^[^@\s<>,;:\"\\]{1,64}@[A-Za-z0-9](?:[A-Za-z0-9.\-]{0,251}[A-Za-z0-9])?\.[A-Za-z]{2,}$"
)


class ContactError(ValueError):
    def __init__(self, code: str) -> None:
        super().__init__(code)
        self.code = code


@dataclass(frozen=True, slots=True)
class Recipient:
    address: str
    address_hash: str
    display_name: str | None = None
    timezone: str = "UTC"
    locale: str = "en-US"

    def __repr__(self) -> str:  # an address must never reach a log line through a repr
        return f"Recipient(hash={self.address_hash[:12]}...)"


def normalize_address(raw: str) -> str:
    """A plain `local@domain` address, lower-cased; anything that could smuggle a header or a second
    recipient (display names, angle brackets, commas, control characters) is refused."""
    value = raw.strip()
    if len(value) > 254 or any(ord(c) < 33 or ord(c) == 127 for c in value):
        raise ContactError("ADDRESS_INVALID")
    if parseaddr(value)[1] != value or not _ADDRESS.match(value):
        raise ContactError("ADDRESS_INVALID")
    return value.lower()


async def address_hash(address: str) -> str:
    """A keyed hash of the normalized address, the same everywhere, so a bounce or unsubscribe recorded for
    an address applies in every tenant. Without the key the address cannot be recomputed from a guess."""
    rt = current_runtime()
    return await asyncio.to_thread(
        handle, rt.mac_key, "email-global", "email-address", normalize_address(address)
    )


def mask(address: str) -> str:
    """`j***@example.com`: enough for an administrator to recognise a contact, not to harvest it."""
    local, _, domain = address.partition("@")
    return f"{local[:1]}***@{domain}"


async def _store(conn: AsyncConnection, tenant_id: str, address: str, subject: str | None) -> str:
    object_id, _ = await store_object(
        conn,
        tenant_id,
        content=address.encode("utf-8"),
        object_type="communication_contact",
        classification="D3",
        retention_class="contact",
        subject_principal_id=subject,
    )
    return object_id


async def set_principal_contact(
    conn: AsyncConnection, tenant_id: str, principal_id: str, address: str, *, actor: str | None
) -> str:
    """The address a principal's own notices go to. Replaces the previous one."""
    normalized = normalize_address(address)
    h = await address_hash(normalized)
    object_id = await _store(conn, tenant_id, normalized, principal_id)
    await store.supersede_principal_contact(conn, tenant_id, principal_id)
    return await store.insert_contact(
        conn,
        tenant_id=tenant_id,
        purpose="PRINCIPAL",
        principal_id=principal_id,
        address_hash=h,
        vault_object_id=object_id,
        created_by=actor,
    )


async def add_role_contact(
    conn: AsyncConnection, tenant_id: str, purpose: str, address: str, *, actor: str | None
) -> str:
    if purpose not in ROLE_PURPOSES:
        raise ContactError("PURPOSE_INVALID")
    normalized = normalize_address(address)
    h = await address_hash(normalized)
    for existing in await store.contacts_for(conn, tenant_id, purpose=purpose):
        if existing["address_hash"] == h:
            return str(existing["contact_id"])  # already there: idempotent
    object_id = await _store(conn, tenant_id, normalized, None)
    return await store.insert_contact(
        conn,
        tenant_id=tenant_id,
        purpose=purpose,
        principal_id=None,
        address_hash=h,
        vault_object_id=object_id,
        created_by=actor,
    )


async def _recipients(
    conn: AsyncConnection, tenant_id: str, rows: list[dict[str, object]]
) -> list[Recipient]:
    out: list[Recipient] = []
    for row in rows:
        address = (await read_object(conn, tenant_id, str(row["vault_object_id"]))).decode("utf-8")
        out.append(Recipient(address=address, address_hash=str(row["address_hash"])))
    return out


async def resolve_principal(
    conn: AsyncConnection, tenant_id: str, principal_id: str
) -> list[Recipient]:
    return await _recipients(
        conn,
        tenant_id,
        await store.contacts_for(conn, tenant_id, purpose="PRINCIPAL", principal_id=principal_id),
    )


async def resolve_role(conn: AsyncConnection, tenant_id: str, purpose: str) -> list[Recipient]:
    return await _recipients(
        conn, tenant_id, await store.contacts_for(conn, tenant_id, purpose=purpose)
    )


async def list_masked(conn: AsyncConnection, tenant_id: str) -> list[dict[str, str]]:
    """Role contacts for the administrators' screen, addresses masked."""
    out = []
    for purpose in ROLE_PURPOSES:
        for row in await store.contacts_for(conn, tenant_id, purpose=purpose):
            address = (await read_object(conn, tenant_id, str(row["vault_object_id"]))).decode(
                "utf-8"
            )
            out.append(
                {"contact_id": str(row["contact_id"]), "purpose": purpose, "address": mask(address)}
            )
    return out


async def keep_destination(conn: AsyncConnection, tenant_id: str, value: str) -> str:
    """Encrypt a deliverable destination (an email address or E.164 number) another module must reach later, such as a WF-05 case contact
    (CR-114). The vault object id is the only thing the caller keeps."""
    object_id, _ = await store_object(
        conn,
        tenant_id,
        content=value.encode("utf-8"),
        object_type="communication_contact",
        classification="D3",
        retention_class="contact",
    )
    return object_id


async def read_destination(conn: AsyncConnection, tenant_id: str, object_id: str) -> str:
    """At send time only."""
    return (await read_object(conn, tenant_id, object_id)).decode("utf-8")
