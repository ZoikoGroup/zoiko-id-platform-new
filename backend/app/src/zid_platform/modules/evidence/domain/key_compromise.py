"""Signing-key compromise rules (ZID-EVIDENCE-001 §25; EVID-22, AT-12). Pure domain.

Rotation retires a key gracefully: everything it signed stays valid. Compromise is different: from the moment the
key is believed to be in someone else's hands (`compromised_at`), a signature that claims to come from it proves
nothing, because the holder could have produced it. Anything signed *before* that moment is unaffected, so history
that predates the compromise keeps verifying and no tenant data is rewritten.
"""

from __future__ import annotations

from collections.abc import Mapping
from datetime import datetime


class KeyCompromiseError(ValueError):
    """A compromise declaration or replacement that cannot be accepted."""


def signed_while_compromised(
    kid: str | None, signed_at: datetime, compromised: Mapping[str, datetime]
) -> bool:
    """True when `kid` is a compromised key and `signed_at` is at or after its compromise time."""
    since = compromised.get(kid) if kid is not None else None
    return since is not None and signed_at >= since


def check_replacement(old_kid: str, new_kid: str, historic_kids: set[str]) -> None:
    """A compromised key is never replaced by itself or by any key already in the history."""
    if new_kid == old_kid:
        raise KeyCompromiseError("the replacement key must differ from the compromised key")
    if new_kid in historic_kids:
        raise KeyCompromiseError("the replacement key was already used")
