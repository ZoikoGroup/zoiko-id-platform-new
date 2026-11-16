"""T42: what an issuer or an accreditation body LOST between two statements, judged for today. Gaining or keeping a status is never a loss."""

from __future__ import annotations

from datetime import date
from typing import Any

from zid_platform.modules.credential.domain import issuer_history as ih
from zid_platform.modules.credential.domain import trust_graph as tg

TODAY = date(2026, 11, 14)


def _issuer(issuer_id: str = "board", **over: Any) -> tg.Issuer:
    base: dict[str, Any] = {
        "issuer_id": issuer_id,
        "kind": tg.IssuerKind.REGULATOR,
        "names": (tg.NameUse("Board"),),
        "countries": frozenset({"US"}),
        "claim_types": frozenset({"PROFESSIONAL_LICENSE"}),
        "authority": tg.Authority.OFFICIAL,
    }
    return tg.Issuer(**{**base, **over})


def _accreditation(body: str = "acc-body", **over: Any) -> tg.Accreditation:
    base: dict[str, Any] = {"body_issuer_id": body, "program": None, "valid_from": date(2020, 1, 1)}
    return tg.Accreditation(**{**base, **over})


def test_a_first_statement_loses_nothing() -> None:
    assert ih.status_lost(None, _issuer(revoked_on=date(2026, 1, 1)), on=TODAY) == ()


def test_a_revocation_that_has_taken_effect_is_a_loss_once() -> None:
    before = _issuer()
    after = _issuer(revoked_on=TODAY)
    assert ih.status_lost(before, after, on=TODAY) == (ih.REVOKED,)
    assert ih.status_lost(after, after, on=TODAY) == ()  # already lost before: not new
    assert ih.status_lost(before, _issuer(revoked_on=date(2027, 1, 1)), on=TODAY) == ()  # not yet


def test_losing_official_authority_is_a_loss() -> None:
    after = _issuer(authority=tg.Authority.UNRECORDED, claim_types=frozenset())
    assert ih.status_lost(_issuer(), after, on=TODAY) == (ih.AUTHORITY_LOST,)


def test_an_accreditation_that_ended_or_was_removed_is_a_loss_and_a_new_one_is_not() -> None:
    held = _issuer(accreditations=(_accreditation(),))
    ended = _issuer(accreditations=(_accreditation(valid_until=date(2026, 11, 1)),))
    assert ih.status_lost(held, ended, on=TODAY) == (ih.ACCREDITATION_LOST,)
    assert ih.status_lost(held, _issuer(), on=TODAY) == (ih.ACCREDITATION_LOST,)
    assert ih.status_lost(_issuer(), held, on=TODAY) == ()
    assert ih.status_lost(held, held, on=TODAY) == ()


def test_proofs_rest_on_the_accrediting_body_even_when_the_accreditation_has_ended() -> None:
    ended = _issuer("uni", accreditations=(_accreditation(valid_until=date(2020, 6, 1)),))
    assert ih.accredited_by(ended, "acc-body")
    assert not ih.accredited_by(ended, "someone-else")
