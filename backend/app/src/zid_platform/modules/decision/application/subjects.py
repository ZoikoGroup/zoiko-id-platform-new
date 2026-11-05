"""Who is being asked about: a Zoiko iD principal, or a person the company's directory provisioned and the company's own system knows by its own id.

A relying system (a payout application) usually knows its people by its own user ids, not by Zoiko iD principal ids. It can send the subject as
`{"type": "directory_user", "id": "<its own id>"}` (for Auth0 that is the `user_id`, `auth0|...`), and the platform finds the person its directory sync
provisioned under that id, in the caller's own company only. An id nobody was provisioned under, or one that names more than one person, is not a
person the platform can decide for, and is answered DENY (SUBJECT_UNKNOWN), never guessed.

Decision must not import provisioning (a dependency cycle), so the lookup is handed in at start-up (`main.py`)."""

from __future__ import annotations

from collections.abc import Awaitable, Callable

from sqlalchemy.ext.asyncio import AsyncConnection

DIRECTORY_SUBJECT = "directory_user"
NO_PERSON = "00000000-0000-0000-0000-000000000000"  # what an unknown subject is decided as: nobody, so no authority, so DENY

SubjectResolver = Callable[[AsyncConnection, str], Awaitable[str | None]]
_resolver: SubjectResolver | None = None


def register_subject_resolver(resolver: SubjectResolver | None) -> None:
    global _resolver
    _resolver = resolver


async def resolve_subject(conn: AsyncConnection, subject_type: str, subject_id: str) -> str | None:
    """The principal id to decide for. A plain principal id is taken as given; a directory user is looked up; None when it cannot be found."""
    if subject_type != DIRECTORY_SUBJECT:
        return subject_id
    return await _resolver(conn, subject_id) if _resolver is not None else None
