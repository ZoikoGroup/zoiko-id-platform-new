"""The hook through which the app emails a team invitation.

The tenant module knows who is invited, to what and with which ticket; it does not know how email is sent, and must not import the module that does (the two would depend on each other).
The app's start-up code gives it the sender once (`configure_mailer`). With none given, nothing is sent and the caller is told so, so an invitation is never lost.
"""

from __future__ import annotations

from collections.abc import Awaitable, Callable
from typing import Any

Mailer = Callable[..., Awaitable[dict[str, str]]]
_mailer: Mailer | None = None


def configure_mailer(mailer: Mailer | None) -> None:
    global _mailer
    _mailer = mailer


async def send_invitation(request: Any, **fields: Any) -> dict[str, str]:
    """Hand the invitation to the configured sender. {"status": "NOT_CONFIGURED"} when the app gave none."""
    if _mailer is None:
        return {"status": "NOT_CONFIGURED"}
    return await _mailer(request, **fields)
