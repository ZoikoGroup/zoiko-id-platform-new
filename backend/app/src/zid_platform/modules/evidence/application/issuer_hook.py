"""The issuer registry is the credential module's. The evidence module's `issuer` check asks it through a hook the application registers at start-up,
so evidence never imports credential (a direct import made a dependency cycle: credential already reads evidence).

With no binder registered the check says so (UNAVAILABLE, NO_ISSUER_SOURCE) and decides nothing."""

from __future__ import annotations

from collections.abc import Callable
from typing import Any

IssuerBinder = Callable[..., dict[str, Any]]
_binder: IssuerBinder | None = None


def register_issuer_binding(binder: IssuerBinder | None) -> None:
    global _binder
    _binder = binder


def issuer_binding() -> IssuerBinder | None:
    return _binder
