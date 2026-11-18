"""Screening a subject against a licensed provider: PEP and associates, adverse media (ZID-DTV-WF-06 S06, S07, S11, ENG-10, ENG-11, ENG-13; CR-113).

The platform ships no provider adapter: a contracted adapter implements `LicensedScreeningPort` and is registered by name. This module is the gate and the ledger around it. A provider is asked only when
every control in `provider_controls.may_call` holds, before any charge. An outage opens a circuit and makes the class UNAVAILABLE; it is never charged, never retried into another region and never read as
clear. A hit is stored as a restricted candidate: a PEP is a risk condition to be looked at (enhanced due diligence), never a finding of wrongdoing, and an article is evidence to be judged, never a
conviction. Official sanctions lists can never fill these classes.
"""

from __future__ import annotations

import asyncio
import json
import uuid
from collections.abc import Collection, Mapping
from dataclasses import dataclass
from datetime import UTC, date, datetime
from decimal import Decimal
from typing import Protocol

from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncConnection

from zid_platform.modules.screening.domain import candidate_detail as cd
from zid_platform.modules.screening.domain import coverage as cov
from zid_platform.modules.screening.domain import provider_controls as pc
from zid_platform.modules.screening.domain.matching import Party
from zid_platform.modules.screening.infrastructure.providers import ProviderEntry

CALL_TIMEOUT_S = 20.0
CIRCUIT = pc.CircuitPolicy()
_circuits: dict[
    str, pc.Circuit
] = {}  # per process: an open circuit protects this process's calls, and resets on restart (documented)


@dataclass(frozen=True, slots=True)
class LicensedHit:
    """One thing a provider returned for the subject. A candidate for a person to judge, never a finding."""

    ref: str  # the provider's own reference
    label: str  # who or what the provider matched
    score: float
    detail: Mapping[
        str, object
    ]  # office held and dates, or article source and status: for the reviewer only


@dataclass(frozen=True, slots=True)
class LicensedResult:
    hits: tuple[LicensedHit, ...]
    data_version: str  # the version of the provider's data the answer came from
    request_id: str | None = None
    response_id: str | None = None


class LicensedScreeningPort(Protocol):
    provider_id: str

    async def screen(
        self, *, klass: cov.Klass, case_ref: str, party: Party, region: str
    ) -> LicensedResult: ...


@dataclass(frozen=True, slots=True)
class LicensedOutcome:
    result: cov.SourceResult
    hits: tuple[LicensedHit, ...]
    provider_id: str
    gate_reasons: tuple[str, ...]


def circuit_status(provider_id: str, now: datetime) -> dict[str, object]:
    """Whether this process would call the provider now: open after repeated outages, and when it closes."""
    c = _circuits.get(provider_id, pc.Circuit())
    return {
        "open": pc.circuit_open(c, CIRCUIT, now),
        "recent_failures": len([f for f in c.failures if now - f <= CIRCUIT.window]),
        "opened_at": c.opened_at.isoformat() if c.opened_at else None,
    }


def reset_circuits() -> None:
    _circuits.clear()


async def usage(conn: AsyncConnection, tenant_id: str, provider_id: str, now: datetime) -> pc.Usage:
    """What this tenant has used of this provider in the current calendar month, from the same rows the bill is made from."""
    start = datetime(now.year, now.month, 1, tzinfo=UTC)
    row = (
        await conn.execute(
            text(
                "SELECT count(*) FILTER (WHERE outcome <> 'UNAVAILABLE') AS calls, coalesce(sum(cost), 0) AS spend "
                "FROM verify.screening_supplier_call WHERE tenant_id = :t AND provider_id = :p AND created_at >= :s"
            ),
            {"t": uuid.UUID(tenant_id), "p": provider_id, "s": start},
        )
    ).one()
    return pc.Usage(Decimal(row.spend), int(row.calls))


def provider_for(
    registry: Mapping[str, ProviderEntry],
    klass: cov.Klass,
    adapters: Mapping[str, LicensedScreeningPort],
) -> ProviderEntry | None:
    """The first approved provider whose contract names the class and whose adapter is registered. There is no ranking and no substitute."""
    for entry in sorted(registry.values(), key=lambda e: e.provider.provider_id):
        p = entry.provider
        if (
            p.state is pc.ProviderState.APPROVED
            and klass.value in p.classes
            and p.provider_id in adapters
        ):
            return entry
    return None


def usable_classes(
    registry: Mapping[str, ProviderEntry], adapters: Mapping[str, LicensedScreeningPort]
) -> frozenset[cov.Klass]:
    return frozenset(
        k
        for k in (cov.Klass.PEP_RCA, cov.Klass.ADVERSE_MEDIA)
        if provider_for(registry, k, adapters) is not None
    )


async def screen_class(
    conn: AsyncConnection,
    tenant_id: str,
    *,
    request_id: str,
    klass: cov.Klass,
    party: Party,
    market: str,
    region: str,
    tenant_regions: Collection[str],
    registry: Mapping[str, ProviderEntry],
    adapters: Mapping[str, LicensedScreeningPort],
    now: datetime,
) -> LicensedOutcome:
    """One class for one subject. Every refusal and every outage is an UNAVAILABLE source, so the case cannot clear on it."""
    entry = provider_for(registry, klass, adapters)

    def down(provider_id: str, reasons: tuple[str, ...]) -> LicensedOutcome:
        return LicensedOutcome(
            cov.SourceResult(
                f"{provider_id}:{klass.value}", klass, cov.Tier.LICENSED, None, None, False
            ),
            (),
            provider_id,
            reasons,
        )

    if entry is None:
        return down("none", ("PROVIDER_NOT_AVAILABLE",))
    provider = entry.provider
    circuit = _circuits.get(provider.provider_id, pc.Circuit())
    gate = pc.may_call(
        provider,
        klass=klass.value,
        market=market,
        region=region,
        tenant_regions=tenant_regions,
        usage=await usage(conn, tenant_id, provider.provider_id, now),
        circuit=circuit,
        now=now,
        policy=CIRCUIT,
    )
    if not gate.allowed:
        return down(provider.provider_id, gate.reasons)
    key = pc.call_key(tenant_id, request_id, provider.provider_id, klass.value)
    already = (
        await conn.execute(
            text("SELECT outcome FROM verify.screening_supplier_call WHERE call_key = :k"),
            {"k": key},
        )
    ).first()
    if already is not None:  # the same call again: nothing is asked and nothing is billed twice
        return down(provider.provider_id, ("CALL_ALREADY_MADE",))
    adapter = adapters[provider.provider_id]
    try:
        result = await asyncio.wait_for(
            adapter.screen(klass=klass, case_ref=request_id, party=party, region=region),
            CALL_TIMEOUT_S,
        )
    except Exception:  # noqa: BLE001 - an outage of any kind: unavailable, never charged, never clear
        _circuits[provider.provider_id] = pc.record_failure(circuit, CIRCUIT, now)
        await _record(
            conn,
            tenant_id,
            request_id,
            provider,
            klass,
            region,
            key,
            "UNAVAILABLE",
            pc.charge_for(key, provider, {}, outage=True),
            None,
            None,
            now,
        )
        return down(provider.provider_id, ("PROVIDER_UNAVAILABLE",))
    _circuits[provider.provider_id] = pc.record_success(circuit)
    charge = pc.charge_for(key, provider, {}, outage=False)
    outcome = "HITS" if result.hits else "NO_HITS"
    await _record(
        conn,
        tenant_id,
        request_id,
        provider,
        klass,
        region,
        key,
        outcome,
        charge,
        result.request_id,
        result.response_id,
        now,
    )
    return LicensedOutcome(
        cov.SourceResult(
            f"{provider.provider_id}:{klass.value}",
            klass,
            cov.Tier.LICENSED,
            f"provider:{provider.provider_id}:{result.data_version}",
            now,
            True,
            len(result.hits),
            now,
        ),
        result.hits,
        provider.provider_id,
        (),
    )


async def _record(
    conn: AsyncConnection,
    tenant_id: str,
    request_id: str,
    provider: pc.Provider,
    klass: cov.Klass,
    region: str,
    key: str,
    outcome: str,
    charge: pc.Charge,
    provider_request_id: str | None,
    provider_response_id: str | None,
    now: datetime,
) -> None:
    await conn.execute(
        text(
            "INSERT INTO verify.screening_supplier_call (call_key, tenant_id, request_id, provider_id, klass, region, contract_version, "
            "price_version, outcome, cost, billable, provider_request_id, provider_response_id, created_at) VALUES (:k, :t, :r, :p, :c, :g, "
            ":cv, :pv, :o, :cost, :b, :pq, :ps, :at) ON CONFLICT (call_key) DO NOTHING"
        ),
        {
            "k": key,
            "t": uuid.UUID(tenant_id),
            "r": uuid.UUID(request_id),
            "p": provider.provider_id,
            "c": klass.value,
            "g": region,
            "cv": provider.contract_version or "",
            "pv": provider.price_version or "",
            "o": outcome,
            "cost": charge.cost,
            "b": charge.billable,
            "pq": provider_request_id,
            "ps": provider_response_id,
            "at": now,
        },
    )


def hit_row(
    provider_id: str,
    klass: cov.Klass,
    data_version: str,
    h: LicensedHit,
    *,
    subject: Party | None = None,
    today: date | None = None,
) -> dict[str, object]:
    """The candidate row for a licensed hit. A PEP hit carries what the dates say (former is a fact, never a lowering of risk); a media hit is linked to the subject only when two independent attributes agree, and one that is not is never stored as STRONG (CR-118)."""
    reasons = ["LICENSED_PROVIDER_CANDIDATE", *sorted(str(k).upper() for k in h.detail)]
    strength = "STRONG" if h.score >= 0.97 else "POSSIBLE"
    if klass is cov.Klass.PEP_RCA and today is not None:
        view = cd.pep_view(h.detail, today=today)
        reasons += [f"PEP_{view.status.value}", f"PEP_NOTE_{view.note}"]
    if klass is cov.Klass.ADVERSE_MEDIA and subject is not None:
        link = cd.link_article(
            {
                "birth": subject.birth,
                "country": list(subject.countries),
                "identifier": list(subject.identifiers),
            },
            h.detail,
        )
        reasons.append(f"ARTICLE_{link.result.value}")
        if not link.attach:
            strength = "POSSIBLE"  # a name alone, or a contradiction, is never a strong match
    return {
        "list_id": f"{provider_id}:{klass.value}",
        "list_version": data_version,
        "source_ref": h.ref,
        "matched_name": h.label,
        "score": max(0.0, min(1.0, h.score)),
        "strength": strength,
        "reasons": json.dumps(reasons),
        "programs": json.dumps([]),
    }
