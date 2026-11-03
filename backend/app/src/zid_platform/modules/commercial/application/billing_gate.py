"""The billing permission check every customer billing route asks (ZID-COM-CL-002; slice L-14).

Commercial does not decide who holds billing authority: the tenant module owns roles and grants and hands its decision in at start-up
(`configure`), because commercial must not import tenant. Until it is handed one, every billing route is refused (503): billing never
opens because a start-up step was missed.

This file has no `from __future__ import annotations` and imports the request machinery inside `permission`, on purpose: the machinery
imports the module ports that import this file, and FastAPI needs the real types of the dependency's parameters.
"""

from collections.abc import Awaitable, Callable
from typing import Annotated, Any

from fastapi import Depends, Request
from zid_errors import ZidError

Checker = Callable[[Request, Any, str], Awaitable[None]]
_checker: Checker | None = None


def configure(checker: Checker | None) -> None:
    global _checker
    _checker = checker


def permission(key: str) -> Callable[..., Awaitable[None]]:
    """A route dependency: the caller needs billing permission `key` (one of the CL-02 keys)."""
    from zid_platform.api_auth import Caller, authenticated_caller

    async def gate(
        request: Request, caller: Annotated[Caller, Depends(authenticated_caller)]
    ) -> None:
        if _checker is None:
            raise ZidError(
                "BILLING_ACCESS_UNAVAILABLE", "Billing access is not available.", http_status=503
            )
        await _checker(request, caller, key)

    return gate
