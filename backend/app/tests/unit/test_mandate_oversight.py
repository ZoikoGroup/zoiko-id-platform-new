"""Who may change a mandate without the company-wide permission (CR-039)."""

from __future__ import annotations

from zid_platform.modules.mandate.domain.oversight import Move, own_moves

EXE, CRE, OTHER = "exe", "cre", "other"


def test_the_executor_may_only_pause() -> None:
    assert own_moves(EXE, executor=EXE, creator=CRE) == {Move.PAUSE}


def test_the_creator_may_pause_and_revoke_but_not_activate() -> None:
    assert own_moves(CRE, executor=EXE, creator=CRE) == {Move.PAUSE, Move.REVOKE}


def test_someone_who_is_both_still_cannot_activate_their_own_mandate() -> None:
    assert Move.ACTIVATE not in own_moves(CRE, executor=CRE, creator=CRE)


def test_a_bystander_or_nobody_has_no_moves() -> None:
    assert own_moves(OTHER, executor=EXE, creator=CRE) == frozenset()
    assert own_moves(None, executor=EXE, creator=CRE) == frozenset()
