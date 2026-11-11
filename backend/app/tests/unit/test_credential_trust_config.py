"""The shipped credential-trust configuration (CR-069) loads, lists only samples, and states no real coverage.

GLB-34  a coverage claim the registry cannot back is refused: with only a simulator on file, no country has coverage
GID-065 a simulated source is usable for a sandbox but is never counted as coverage
"""

from __future__ import annotations

from datetime import date

import pytest
from zid_platform.modules.credential.domain import sources as src
from zid_platform.modules.credential.infrastructure import registries as reg
from zid_platform.modules.verification.infrastructure.pack_registry import pack_registry

D = date(2026, 11, 12)
ALL_CLAIMS = (
    "ACADEMIC_AWARD",
    "PROFESSIONAL_LICENSE",
    "INDUSTRY_CERTIFICATE",
    "CPD_RECORD",
    "EMPLOYMENT_ASSERTION",
    "INSURANCE_ASSERTION",
)


def test_every_shipped_file_loads():
    assert reg.issuer_registry().issuers
    assert reg.source_contracts()
    assert reg.policies()[0] and reg.policies()[1]
    assert pack_registry().packs


def test_the_only_source_on_file_is_a_simulator_and_it_is_never_coverage():
    contracts = reg.source_contracts()
    assert {c.source_id for c in contracts} == {"simulator", "simulator-disciplinary"}
    assert all(c.simulated for c in contracts)
    for claim in ALL_CLAIMS:
        for country in ("US", "GB", "DE", "FR", "IN"):
            assert (
                src.coverage(contracts, country=country, claim_type=claim, on=D)
                is src.Coverage.NO_COVERAGE
            )
    # a local or test caller may ask to see the simulator, and only then
    assert (
        src.coverage(
            reg.source_contracts(),
            country="US",
            claim_type="ACADEMIC_AWARD",
            on=D,
            include_simulated=True,
        )
        is src.Coverage.PRIMARY_SOURCE
    )


def test_glb_34_the_shipped_registry_backs_no_coverage_claim_at_all():
    for claim in ALL_CLAIMS:
        with pytest.raises(src.SourceError) as e:
            src.refuse_unbacked_claim(
                src.CoverageClaim(claim, frozenset({"US"})), reg.source_contracts(), on=D
            )
        assert e.value.code == "COVERAGE_CLAIM_UNBACKED"


def test_every_shipped_service_pack_is_a_draft_so_nothing_is_live_when_deployed():
    from datetime import UTC, datetime

    from zid_platform.modules.verification.domain.service_packs import FeatureState, public_view

    packs = pack_registry().packs
    assert packs and all(p.feature_state is FeatureState.DESIGN for p in packs)
    view = public_view(
        pack_registry(), now=datetime(2026, 11, 12, tzinfo=UTC), environment="production"
    )
    assert {v["state"] for v in view} == {"UNAVAILABLE"}


def test_only_sample_issuers_are_listed_and_the_sample_policy_names_its_scope():
    assert all(i.startswith("sample-") for i in reg.issuer_registry().issuers)
    policy = reg.find_policy("US", "CA", "LAW", "legal.advice")
    assert policy is not None and policy.policy_id.startswith("sample-")
    assert (
        reg.find_policy("US", "NY", "LAW", "legal.advice") is None
    )  # no policy for another place: no decision at all
    assert reg.find_policy("US", "CA", "LAW", "tax.advice") is None


def test_the_policy_loader_refuses_malformed_input():
    for bad in (
        {"policies": [{"policy_id": "p"}]},
        {
            "tier_rules": [
                {"rule_id": "r", "version": 1, "tier_a": ["NOT_A_DIMENSION"], "tier_b": []}
            ]
        },
        {
            "policies": [
                {
                    "policy_id": "p",
                    "version": 1,
                    "country": "US",
                    "profession_code": "X",
                    "service_code": "s",
                }
            ]
            * 2
        },
    ):
        with pytest.raises(ValueError, match="ELIGIBILITY_POLICY_INVALID"):
            reg.parse_policies(bad)
