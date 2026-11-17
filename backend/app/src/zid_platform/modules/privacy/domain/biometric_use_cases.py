"""Ecosystem use cases of biometric methods (ZID-DTV-WF-04 BIO-19; QA-028; CR-110). Pure: no I/O.

`config/privacy/biometric-use-cases.yaml` lists the Zoiko products (Zoiko Rooms, Zoikorum, ...) that use a biometric method through Zoiko iD, each
under its own purpose. A purpose listed there is refused for every biometric method until its integration acceptance is signed (an approval
reference and an effective date that has arrived): COUNSEL, missing or future means not certified. A purpose not listed is not affected. Because a
method scope names its purpose exactly, an approval for one product's purpose never serves another product's.
"""

from __future__ import annotations

import re
from collections.abc import Mapping
from dataclasses import dataclass
from datetime import date
from typing import Any

COUNSEL = "COUNSEL"
CODE = re.compile(r"^[A-Z][A-Z0-9_]{1,63}$")


class UseCaseError(ValueError):
    pass


@dataclass(frozen=True, slots=True)
class UseCase:
    use_case_id: str
    consumer: str
    purposes: frozenset[str]
    approval_ref: str | None
    effective_from: date | None


def parse(doc: Mapping[str, Any]) -> tuple[UseCase, ...]:
    """The use cases in the file. A malformed entry refuses the whole file; a purpose claimed by two use cases is refused (one owner per purpose)."""
    out: list[UseCase] = []
    seen: set[str] = set()
    for raw in doc.get("use_cases") or []:
        uid = str(raw.get("id", ""))
        purposes = frozenset(str(p) for p in raw.get("purposes") or ())
        if not CODE.match(uid) or not purposes or not all(CODE.match(p) for p in purposes):
            raise UseCaseError(f"use case {uid!r}: id and purposes are codes")
        if purposes & seen:
            raise UseCaseError(f"{uid}: a purpose belongs to one use case")
        seen |= purposes
        acc = raw.get("acceptance") or {}
        ref = acc.get("approval_ref")
        eff = acc.get("effective_from")
        when: date | None = None
        if isinstance(eff, date):
            when = eff
        elif isinstance(eff, str) and eff != COUNSEL:
            try:
                when = date.fromisoformat(eff)
            except ValueError as e:
                raise UseCaseError(f"{uid}: effective_from is a date or COUNSEL") from e
        out.append(
            UseCase(
                uid,
                str(raw.get("consumer", "")),
                purposes,
                str(ref) if ref and ref != COUNSEL else None,
                when,
            )
        )
    return tuple(out)


def problems(cases: tuple[UseCase, ...], purpose: str, *, today: date) -> list[str]:
    """Why this purpose may not use a biometric method now. Empty for a purpose no use case claims, or a certified one."""
    for c in cases:
        if purpose in c.purposes:
            if c.approval_ref is None or c.effective_from is None:
                return [f"USE_CASE_NOT_CERTIFIED:{c.use_case_id}"]
            if c.effective_from > today:
                return [f"USE_CASE_NOT_YET_EFFECTIVE:{c.use_case_id}"]
            return []
    return []
