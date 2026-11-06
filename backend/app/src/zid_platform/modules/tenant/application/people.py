"""How a person is shown to other people: "Name (M-0007)" (CR-061).

A UUID identifies a person to the machine; a person reading an approval, a team list or a review needs a name and a number they can say out loud.
Every screen and API that names a person uses `PersonRef`, so the form is the same everywhere:

- `member_no` is permanent and per company (migration tenant_0018): M-0001, M-0002, ...; assigned automatically the first time a person gets a role.
- `name` is the display label the person (or an owner/admin) gave. It is optional: a person without one is shown by their number alone.
- An erased person has no name (erasure clears it), and is shown by number.

Names are for the people inside one company, resolved through row-level security, so a company only ever sees its own. Audit records stay pseudonymous
by design; turning an audit handle back into a person remains the case-bound, recorded re-identification (EVIDENCE §13).
"""

from __future__ import annotations

import re
import uuid
from dataclasses import dataclass
from typing import Any

from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncConnection

from zid_platform.modules.principal.ports import display_labels, set_display_label

_NUMBERS = (
    "SELECT principal_id, member_no FROM platform.tenant_member_no WHERE principal_id = ANY(:ids)"
)
_CONTROL = re.compile(r"[\x00-\x1f\x7f]")
MAX_NAME = 120


class NameError_(ValueError):
    """The name is not acceptable (empty after trimming, too long, or contains control characters)."""


def member_code(member_no: int | None) -> str | None:
    return f"M-{member_no:04d}" if member_no else None


def clean_name(raw: str | None) -> str | None:
    """A display name as it will be stored: trimmed, single-spaced. None clears it. Raises NameError_ for text that cannot be a name."""
    if raw is None:
        return None
    value = re.sub(r"\s+", " ", raw).strip()
    if not value:
        return None
    if len(value) > MAX_NAME or _CONTROL.search(value):
        raise NameError_("a name is 1 to 120 characters, with no control characters")
    return value


@dataclass(frozen=True, slots=True)
class PersonRef:
    principal_id: str
    member_no: int | None
    name: str | None

    @property
    def code(self) -> str | None:
        return member_code(self.member_no)

    @property
    def label(self) -> str:
        """ "Anil Kumar (M-0003)", or "M-0003" when there is no name, or the short id when even the number is unknown."""
        code = self.code or self.principal_id[:8]
        return f"{self.name} ({code})" if self.name else code

    def view(self) -> dict[str, Any]:
        return {
            "principal_id": self.principal_id,
            "member_no": self.member_no,
            "member_code": self.code,
            "name": self.name,
            "label": self.label,
        }


async def person_refs(conn: AsyncConnection, principal_ids: list[str]) -> dict[str, PersonRef]:
    """principal id -> PersonRef for each id, within the caller's company. Unknown ids still get a ref (no number, no name)."""
    ids = sorted({i for i in principal_ids if i})
    if not ids:
        return {}
    rows = await conn.execute(text(_NUMBERS), {"ids": [uuid.UUID(i) for i in ids]})
    numbers = {str(r.principal_id): int(r.member_no) for r in rows}
    names = await display_labels(conn, ids)
    return {i: PersonRef(i, numbers.get(i), names.get(i)) for i in ids}


async def rename(conn: AsyncConnection, principal_id: str, raw: str | None) -> PersonRef:
    """Set or clear a person's name. Raises NameError_ for unacceptable text and LookupError for a person outside the caller's company."""
    name = clean_name(raw)
    if not await set_display_label(conn, principal_id, name):
        raise LookupError("no such person in this company")
    return (await person_refs(conn, [principal_id]))[principal_id]
