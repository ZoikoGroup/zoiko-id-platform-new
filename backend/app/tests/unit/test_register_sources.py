"""Register-source connectors (CR-087): who may be asked, and that a reading proves only what its register says."""

from __future__ import annotations

import asyncio
from datetime import UTC, datetime

import pytest
from zid_platform.modules.verification.domain import register_sources as rs
from zid_platform.modules.verification.domain.attestation import Scope
from zid_platform.modules.verification.domain.certification import CertStatus
from zid_platform.modules.verification.infrastructure.register_sources import (
    SimulatorConnector,
    sources,
)

NOW = datetime(2026, 11, 12, 9, tzinfo=UTC)


def _source(**kw: object) -> rs.Source:
    base: dict[str, object] = {
        "source_id": "uk-diatf",
        "kind": rs.SourceKind.OFFICIAL_REGISTER,
        "scheme": "UK_DIATF",
        "base_url": "https://register.example.gov.uk/",
        "contract_ref": "OGL-v3",
    }
    return rs.Source(**(base | kw))  # type: ignore[arg-type]


def test_a_source_is_used_only_with_a_contract_a_connector_and_in_its_environments() -> None:
    assert rs.usable(_source(), environment="production", connector_available=True).usable
    assert rs.usable(None, environment="local", connector_available=True).reasons == (
        "SOURCE_UNKNOWN",
    )
    u = rs.usable(
        _source(contract_ref=None, enabled=False),
        environment="production",
        connector_available=False,
    )
    assert set(u.reasons) == {
        "SOURCE_CONTRACT_MISSING",
        "SOURCE_DISABLED",
        "SOURCE_CONNECTOR_MISSING",
    }
    assert rs.usable(
        _source(environments=frozenset({"staging"})),
        environment="production",
        connector_available=True,
    ).reasons == ("SOURCE_NOT_FOR_ENVIRONMENT",)


def test_the_simulator_never_counts_outside_sandbox_environments() -> None:
    sim = sources()[rs.SIMULATOR]
    assert rs.usable(sim, environment="local", connector_available=True).usable
    assert not rs.usable(sim, environment="production", connector_available=True).usable


def test_the_shipped_configuration_lists_no_real_source() -> None:
    assert set(sources()) == {rs.SIMULATOR}


def test_configuration_is_refused_when_it_cannot_describe_a_register() -> None:
    with pytest.raises(rs.SourceError, match="SOURCE_URL_INVALID"):
        rs.parse(
            {
                "sources": [
                    {
                        "source_id": "x",
                        "kind": "OFFICIAL_REGISTER",
                        "scheme": "UK_DIATF",
                        "base_url": "http://plain",
                    }
                ]
            }
        )
    with pytest.raises(rs.SourceError, match="SOURCE_ID_INVALID"):
        rs.parse(
            {
                "sources": [
                    {
                        "source_id": "simulator",
                        "kind": "OFFICIAL_REGISTER",
                        "scheme": "X",
                        "base_url": "https://a.b/",
                    }
                ]
            }
        )
    parsed = rs.parse(
        {
            "sources": [
                {
                    "source_id": "uk-diatf",
                    "kind": "OFFICIAL_REGISTER",
                    "scheme": "UK_DIATF",
                    "base_url": "https://a.b/",
                }
            ]
        }
    )
    assert parsed["uk-diatf"].enabled is False  # off until someone turns it on


def _reading(scope: Scope) -> rs.Reading:
    r = asyncio.run(SimulatorConnector().read(scope, now=NOW))
    assert r is not None
    return r


def test_the_proof_hash_is_computed_from_the_bytes_and_the_entry_must_be_on_the_sources_register() -> (
    None
):
    scope = Scope("sim-acme", "UK_DIATF", "GB", "RTR")
    reading = _reading(scope)
    snap = rs.to_snapshot(
        reading,
        sources()[rs.SIMULATOR],
        snapshot_id="s1",
        evidence_locator="register-response:x",
        recorded_by="op",
        recorded_at=NOW,
    )
    assert snap.proof_hash == rs.proof_hash(reading.body) and snap.status is CertStatus.ACTIVE
    with pytest.raises(rs.SourceError, match="SOURCE_URL_MISMATCH"):
        rs.to_snapshot(
            reading,
            _source(),
            snapshot_id="s1",
            evidence_locator="x:y:z",
            recorded_by="op",
            recorded_at=NOW,
        )
    with pytest.raises(rs.SourceError, match="SOURCE_SCHEME_MISMATCH"):
        rs.to_snapshot(
            reading,
            _source(base_url="https://register.simulator.invalid/", scheme="EU_TL"),
            snapshot_id="s1",
            evidence_locator="x:y:z",
            recorded_by="op",
            recorded_at=NOW,
        )


def test_the_simulator_says_not_listed_for_unknown_providers_and_reports_withdrawals() -> None:
    assert (
        asyncio.run(SimulatorConnector().read(Scope("acme", "UK_DIATF", "GB", "RTR"), now=NOW))
        is None
    )
    assert (
        _reading(Scope("sim-withdrawn-acme", "UK_DIATF", "GB", "RTR")).status
        is CertStatus.WITHDRAWN
    )
    assert _reading(Scope("sim-component-acme", "UK_DIATF", "GB", "RTR")).component_only
