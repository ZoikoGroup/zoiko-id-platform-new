"""Who may change a mandate's state without a company-wide permission (ZID-MANDATE-001 §9-10; CR-039).

A mandate is a person's own authority handed on, so the people it concerns may always stop it: the executor may pause it,
and the person who created it may pause or revoke it. Starting it, and stopping anyone else's, is oversight and needs the
`mandate.manage` action (held by the owner and administrators). Activation never rests with the executor.
"""

from __future__ import annotations

from enum import StrEnum


class Move(StrEnum):
    ACTIVATE = "activate"
    PAUSE = "pause"
    REVOKE = "revoke"


def own_moves(principal_id: str | None, *, executor: str, creator: str) -> frozenset[Move]:
    """The moves this person may make on the mandate by being its executor or its creator."""
    if not principal_id:
        return frozenset()
    moves: set[Move] = set()
    if principal_id in (executor, creator):
        moves.add(Move.PAUSE)
    if principal_id == creator:
        moves.add(Move.REVOKE)
    return frozenset(moves)
