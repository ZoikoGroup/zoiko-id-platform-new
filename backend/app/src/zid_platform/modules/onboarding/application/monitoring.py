"""Ongoing monitoring of company screening (ZID-ED-04 section 07).

The screening module rescreens when a list changes or a clear outcome lapses. This module adds the triggers that need the official
register: it reads the directors and persons with significant control of every live case again, and when what the register says has
changed it records the new snapshot and asks for a rescreen (`REGISTER_CHANGED`); the worker answers on its next pass and the new
decision names the reason. A register that cannot be read changes nothing (it is never read as "no one"), and a country that is no
longer open for business verification is left alone, as in the other jobs.
"""

from __future__ import annotations

import logging
from collections.abc import Callable, Mapping
from datetime import datetime

from sqlalchemy.ext.asyncio import AsyncConnection
from zid_db import in_tenant
from zid_errors import ZidError

from zid_platform.modules.organization.ports import KybAdapter, KybError, RegisterRead, kyb_get_case
from zid_platform.modules.organization.ports import kyb_register_people as read_register
from zid_platform.modules.screening.ports import (
    Party,
    PartyKind,
    RegisterEvidence,
    RegisterPerson,
    live_kyb_cases,
    record_register,
)

log = logging.getLogger("zid.screening.monitoring")


def register_evidence(read: RegisterRead) -> RegisterEvidence | None:
    """What the official register listed, in the screening module's terms. None: the country has no source."""
    if read.status == "NOT_APPLICABLE":
        return None
    people: list[RegisterPerson] = []
    for i, p in enumerate(sorted(read.people, key=lambda x: (x.kind, x.name, x.natures)), 1):
        ref = f"{'psc' if p.kind == 'PSC' else 'off'}{i}"
        kind = PartyKind.ENTITY if p.entity else PartyKind.PERSON
        people.append(
            RegisterPerson(ref, p.kind, Party(ref, kind, p.name, p.birth_year), p.natures, p.ceased)
        )
    return RegisterEvidence(read.status, tuple(people), read.content_hash)


async def register_watch_pass(
    conn: AsyncConnection,
    adapters: Mapping[str, KybAdapter],
    now: datetime,
    *,
    country_gate: Callable[[str], None] | None = None,
) -> dict[str, int]:
    """tenant id -> cases whose register changed; `tenant id:held_by_country` -> cases left alone because their country is closed."""
    out: dict[str, int] = {}
    for tenant, case_id, kyb_id, country in await live_kyb_cases(conn):
        if country_gate is not None:
            try:
                country_gate(country)
            except (ValueError, ZidError):
                key = f"{tenant}:held_by_country"
                out[key] = out.get(key, 0) + 1
                continue
        try:
            async with conn.begin_nested(), in_tenant(conn, tenant):
                kyb = await kyb_get_case(conn, tenant, kyb_id, now=now)
                ev = register_evidence(
                    await read_register(adapters, kyb.registration_number, country, now)
                )
                if (
                    ev is not None
                    and ev.status == "OK"
                    and await record_register(
                        conn, tenant, case_id, ev, source="companies-house", now=now
                    )
                ):
                    out[tenant] = out.get(tenant, 0) + 1
        except (KybError, ZidError):
            log.exception("register watch of case %s failed", case_id)
        except Exception:
            log.exception("register watch of case %s failed unexpectedly", case_id)
    return out
