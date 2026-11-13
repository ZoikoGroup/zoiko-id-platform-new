"""Connectors to official registers, trusted lists and professional registers (ZID-RCG-003 RCG-G02, RCG-G04, section 05; CR-087). Pure domain.

An attestation (`attestation.py`) is what an official register said. This module is how the platform may ASK one itself instead of an operator
copying the entry by hand. Rules, each with a test:
- a source is used only when it is configured, enabled, names the contract or published terms that let us query it, and is allowed in this
  environment. The built-in simulator is a development stand-in: local, test and dev only, never deployed (same rule as the country registry);
- a reading must come from the register the source names: its entry URL is on the source's https base, and its scheme is the source's scheme.
  A connector cannot vouch for a different register;
- the proof hash is the sha256 of the exact bytes the register returned, computed here, never taken from the connector's say-so; those bytes are
  preserved and the attestation points at them;
- "not listed" is an answer, not an error: nothing is recorded, so the scope has no fresh snapshot and goes to HOLD by the attestation rules. An
  unreachable register never keeps a certification alive.
"""

from __future__ import annotations

import hashlib
import re
from collections.abc import Mapping
from dataclasses import dataclass, field
from datetime import date, datetime
from enum import StrEnum
from typing import Any

from zid_platform.modules.verification.domain.attestation import HTTPS, Scope, Snapshot
from zid_platform.modules.verification.domain.certification import CertStatus

SIMULATOR = "simulator"
SANDBOX_ENVIRONMENTS = frozenset({"local", "test", "dev"})
SIMULATOR_BASE = "https://register.simulator.invalid/"
SOURCE_ID = re.compile(r"^[a-z0-9][a-z0-9-]{0,63}$")
SCHEME = re.compile(r"^[A-Z][A-Z0-9_]{0,63}$")
ANY_SCHEME = "*"  # the simulator only: it stands in for any register in development


class SourceError(ValueError):
    def __init__(self, code: str, detail: str = "") -> None:
        super().__init__(f"{code}: {detail}" if detail else code)
        self.code = code


class SourceKind(StrEnum):
    OFFICIAL_REGISTER = "OFFICIAL_REGISTER"  # e.g. the UK DIATF / DVS register
    TRUSTED_LIST = "TRUSTED_LIST"  # e.g. an EU member state trusted list
    PROFESSIONAL_REGISTER = (
        "PROFESSIONAL_REGISTER"  # e.g. a regulator's register of licensed professionals
    )


@dataclass(frozen=True)
class Source:
    source_id: str
    kind: SourceKind
    scheme: str
    base_url: str  # every entry URL from this source starts here
    contract_ref: str | None  # the contract or published terms that permit automated queries
    enabled: bool = True
    environments: frozenset[str] = field(default_factory=frozenset)  # empty = every environment


@dataclass(frozen=True)
class Reading:
    """What a connector got back for one scope. `body` is the register's response, byte for byte."""

    scope: Scope
    scheme_version: str
    certificate_ref: str
    component_only: bool
    status: CertStatus
    valid_from: date
    valid_until: date | None
    register_url: str
    register_entry_id: str
    cab: str | None
    fetched_at: datetime
    body: bytes


@dataclass(frozen=True)
class Usable:
    usable: bool
    reasons: tuple[str, ...]


def simulator_source() -> Source:
    return Source(
        source_id=SIMULATOR,
        kind=SourceKind.OFFICIAL_REGISTER,
        scheme=ANY_SCHEME,
        base_url=SIMULATOR_BASE,
        contract_ref="sandbox-only",
        environments=SANDBOX_ENVIRONMENTS,
    )


def parse(doc: Mapping[str, Any]) -> dict[str, Source]:
    """The configured sources. A malformed entry stops the load: a register we cannot describe is not one we may ask."""
    out: dict[str, Source] = {}
    for raw in doc.get("sources") or []:
        sid = str(raw.get("source_id", ""))
        if not SOURCE_ID.match(sid) or sid == SIMULATOR:
            raise SourceError("SOURCE_ID_INVALID", sid)
        if sid in out:
            raise SourceError("SOURCE_DUPLICATE", sid)
        base = str(raw.get("base_url", ""))
        if not HTTPS.match(base):
            raise SourceError(
                "SOURCE_URL_INVALID", f"{sid}: an official source is named by an https address"
            )
        if not SCHEME.match(str(raw.get("scheme", ""))):
            raise SourceError("SOURCE_SCHEME_INVALID", sid)
        try:
            kind = SourceKind(str(raw.get("kind")))
        except ValueError as e:
            raise SourceError("SOURCE_KIND_INVALID", sid) from e
        out[sid] = Source(
            source_id=sid,
            kind=kind,
            scheme=str(raw.get("scheme", "")),
            base_url=base,
            contract_ref=(str(raw["contract_ref"]) if raw.get("contract_ref") else None),
            enabled=bool(raw.get("enabled", False)),
            environments=frozenset(raw.get("environments") or ()),
        )
    return out


def usable(source: Source | None, *, environment: str, connector_available: bool) -> Usable:
    """May the platform query this source here and now? Every reason is listed."""
    if source is None:
        return Usable(False, ("SOURCE_UNKNOWN",))
    reasons: list[str] = []
    if not source.enabled:
        reasons.append("SOURCE_DISABLED")
    if not source.contract_ref:
        reasons.append("SOURCE_CONTRACT_MISSING")
    if source.environments and environment not in source.environments:
        reasons.append("SOURCE_NOT_FOR_ENVIRONMENT")
    if not source.scheme:
        reasons.append("SOURCE_SCHEME_MISSING")
    if not connector_available:
        reasons.append("SOURCE_CONNECTOR_MISSING")
    return Usable(not reasons, tuple(reasons))


def proof_hash(body: bytes) -> str:
    return hashlib.sha256(body).hexdigest()


def to_snapshot(
    reading: Reading,
    source: Source,
    *,
    snapshot_id: str,
    evidence_locator: str,
    recorded_by: str,
    recorded_at: datetime,
) -> Snapshot:
    """The attestation snapshot for a reading, after checking it came from the register the source names."""
    if not reading.register_url.startswith(source.base_url):
        raise SourceError("SOURCE_URL_MISMATCH", "the entry is not on the source's register")
    if source.scheme != ANY_SCHEME and reading.scope.scheme != source.scheme:
        raise SourceError(
            "SOURCE_SCHEME_MISMATCH", f"{reading.scope.scheme} is not {source.scheme}"
        )
    if not reading.body:
        raise SourceError("SOURCE_RESPONSE_EMPTY")
    return Snapshot(
        snapshot_id=snapshot_id,
        scope=reading.scope,
        scheme_version=reading.scheme_version,
        certificate_ref=reading.certificate_ref,
        component_only=reading.component_only,
        status=reading.status,
        valid_from=reading.valid_from,
        valid_until=reading.valid_until,
        register_url=reading.register_url,
        register_entry_id=reading.register_entry_id,
        cab=reading.cab,
        fetched_at=reading.fetched_at,
        proof_hash=proof_hash(reading.body),
        evidence_locator=evidence_locator,
        recorded_by=recorded_by,
        recorded_at=recorded_at,
    )
