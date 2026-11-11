"""GID-036 country / profession service packs (ZID-GAP-001 v2.0, section 07.1). Pure rules, no database, no network.

GID-036 negative proof   country code disabled, stale policy, old pack selected
GLB-28                   one pack losing its status blocks that pack only
GLB-29                   a pack listing an unavailable primary source as LIVE cannot be published
"""

from __future__ import annotations

from dataclasses import replace
from datetime import UTC, datetime, timedelta

import pytest
from zid_platform.modules.verification.domain.service_packs import (
    FeatureState,
    PackError,
    PackRegistry,
    ServicePack,
    compute_hash,
    parse,
    public_view,
    resolve,
    sealed,
    validate_pack,
    validate_registry,
)

NOW = datetime(2026, 11, 10, 12, tzinfo=UTC)
PAST = NOW - timedelta(days=30)
FUTURE = NOW + timedelta(days=30)


def _pack(**over) -> ServicePack:
    base = {
        "pack_id": "us-ca-law",
        "version": 1,
        "country_iso2": "US",
        "subdivision_code": "CA",
        "profession_code": "LAW",
        "service_code": "license.verify",
        "feature_state": FeatureState.LIVE_PROVEN,
        "data_residency_region": "us",
        "allowed_processing_regions": ("us",),
        "legal_basis_ref": "legal/us-ca-law-1",
        "source_registry_ids": ("src-calbar",),
        "verification_methods": ("PRIMARY_SOURCE",),
        "evidence_tier_required": "PRIMARY_SOURCE_CONFIRMED",
        "retention_policy_id": "ret-1",
        "human_review_rule_id": "hr-1",
        "valid_from": PAST,
        "legal_owner": "legal",
        "product_owner": "product",
        "qa_gate_id": "qa-1",
    }
    base.update(over)
    return sealed(ServicePack(**base))


def _reg(*packs: ServicePack, disabled: tuple[str, ...] = ()) -> PackRegistry:
    return PackRegistry("1", packs, frozenset(disabled))


def _resolve(reg: PackRegistry, **over):
    args = {
        "country": "US",
        "service_code": "license.verify",
        "profession": "LAW",
        "subdivision": "CA",
        "now": NOW,
        "environment": "production",
    }
    args.update(over)
    return resolve(reg, **args)


# --- resolution -------------------------------------------------------------------------------------------------------


def test_a_live_pack_resolves():
    assert _resolve(_reg(_pack())).pack_id == "us-ca-law"


@pytest.mark.parametrize(
    "over",
    [
        {"country": "GB"},  # another country is never a neighbour
        {"service_code": "academic.verify"},
        {"profession": "MEDICINE"},
        {"subdivision": "NY"},
        {"country": "usa"},
        {"country": ""},
    ],
)
def test_an_unsupported_scope_is_unavailable_never_a_near_match(over):
    with pytest.raises(PackError) as e:
        _resolve(_reg(_pack()), **over)
    assert e.value.code == "PACK_UNAVAILABLE"


def test_no_packs_at_all_is_unavailable():
    with pytest.raises(PackError) as e:
        _resolve(_reg())
    assert e.value.code == "PACK_UNAVAILABLE"


def test_a_disabled_country_code_is_unavailable_even_with_a_live_pack():
    with pytest.raises(PackError) as e:
        _resolve(_reg(_pack(), disabled=("us",)))
    assert e.value.code == "PACK_UNAVAILABLE"


def test_a_stale_pack_is_unavailable():
    expired = _pack(valid_until=NOW - timedelta(seconds=1))
    not_yet = _pack(pack_id="later", valid_from=FUTURE)
    for p in (expired, not_yet):
        with pytest.raises(PackError) as e:
            _resolve(_reg(p))
        assert e.value.code == "PACK_UNAVAILABLE"
    # the instant it expires, not a moment after
    assert _resolve(_reg(_pack(valid_until=NOW + timedelta(seconds=1)))).pack_id == "us-ca-law"


def test_the_highest_effective_version_wins_and_an_old_one_cannot_be_selected():
    v1, v2 = _pack(version=1), _pack(version=2)
    reg = _reg(v1, v2)
    assert _resolve(reg).version == 2
    with pytest.raises(PackError) as e:
        _resolve(reg, pack_id="us-ca-law", version=1)
    assert e.value.code == "PACK_VERSION_STALE"
    assert _resolve(reg, pack_id="us-ca-law", version=2).version == 2


def test_a_newer_version_still_in_design_does_not_replace_the_live_one_in_production():
    live = _pack(version=1)
    draft = _pack(version=2, feature_state=FeatureState.DESIGN)
    assert _resolve(_reg(live, draft)).version == 1
    assert (
        _resolve(_reg(live, draft), environment="local").version == 2
    )  # a developer sandbox may see it


@pytest.mark.parametrize("state", [FeatureState.STAGING, FeatureState.DESIGN, FeatureState.BLOCKED])
def test_an_unfinished_or_blocked_pack_never_serves_a_deployed_environment(state):
    with pytest.raises(PackError) as e:
        _resolve(_reg(_pack(feature_state=state)))
    assert e.value.code == "PACK_UNAVAILABLE"


def test_a_blocked_pack_is_blocked_even_in_a_sandbox():
    with pytest.raises(PackError):
        _resolve(_reg(_pack(feature_state=FeatureState.BLOCKED)), environment="local")


def test_the_most_specific_pack_wins():
    country = _pack(pack_id="us-law", profession_code=None, subdivision_code=None)
    state = _pack(pack_id="us-ca-law")
    assert _resolve(_reg(country, state)).pack_id == "us-ca-law"
    assert _resolve(_reg(country, state), subdivision="NY").pack_id == "us-law"


def test_a_blocked_specific_pack_does_not_fall_back_to_the_broader_one():
    broad = _pack(pack_id="us-law", profession_code=None, subdivision_code=None)
    blocked = _pack(pack_id="us-ca-law", feature_state=FeatureState.BLOCKED)
    with pytest.raises(PackError) as e:
        _resolve(_reg(broad, blocked))
    assert e.value.code == "PACK_UNAVAILABLE"


def test_glb_28_losing_one_packs_status_blocks_that_pack_only():
    uk = _pack(
        pack_id="uk-rtr",
        country_iso2="GB",
        subdivision_code=None,
        profession_code=None,
        service_code="rtr",
    )
    us = _pack()
    healthy = _reg(uk, us)
    assert (
        _resolve(
            healthy, country="GB", service_code="rtr", profession=None, subdivision=None
        ).pack_id
        == "uk-rtr"
    )
    broken = _reg(replace(uk, feature_state=FeatureState.BLOCKED), us)
    with pytest.raises(PackError):
        _resolve(broken, country="GB", service_code="rtr", profession=None, subdivision=None)
    assert _resolve(broken).pack_id == "us-ca-law"  # the unrelated pack is untouched


# --- publishing -------------------------------------------------------------------------------------------------------


def test_a_complete_live_pack_validates():
    validate_pack(_pack(), available_sources={"src-calbar"})


@pytest.mark.parametrize(
    "field",
    [
        "legal_basis_ref",
        "retention_policy_id",
        "human_review_rule_id",
        "legal_owner",
        "product_owner",
        "qa_gate_id",
    ],
)
def test_a_live_pack_cannot_omit_its_evidence(field):
    with pytest.raises(PackError) as e:
        validate_pack(_pack(**{field: None}))
    assert e.value.code == "PACK_EVIDENCE_MISSING"
    assert field in str(e.value)


def test_a_live_pack_needs_a_start_date_and_a_source():
    with pytest.raises(PackError) as e:
        validate_pack(_pack(valid_from=None))
    assert e.value.code == "PACK_EVIDENCE_MISSING"
    with pytest.raises(PackError) as e:
        validate_pack(_pack(source_registry_ids=()))
    assert e.value.code == "PACK_SOURCE_MISSING"


def test_the_home_region_must_be_among_the_allowed_processing_regions():
    with pytest.raises(PackError) as e:
        validate_pack(_pack(data_residency_region="eu", allowed_processing_regions=("us",)))
    assert e.value.code == "PACK_RESIDENCY_INCONSISTENT"
    with pytest.raises(PackError) as e:
        validate_pack(_pack(allowed_processing_regions=()))
    assert e.value.code == "PACK_EVIDENCE_MISSING"


def test_glb_29_a_live_pack_cannot_name_a_source_that_is_not_available():
    with pytest.raises(PackError) as e:
        validate_pack(_pack(), available_sources=set())
    assert e.value.code == "PACK_SOURCE_UNAVAILABLE"
    assert "src-calbar" in str(e.value)
    # the same pack as design is fine: it claims nothing
    validate_pack(
        _pack(feature_state=FeatureState.DESIGN, source_registry_ids=("src-calbar",)),
        available_sources=set(),
    )


def test_a_design_pack_may_be_incomplete():
    validate_pack(
        _pack(
            feature_state=FeatureState.DESIGN,
            legal_basis_ref=None,
            qa_gate_id=None,
            source_registry_ids=(),
        )
    )


def test_an_edited_pack_cannot_keep_its_old_approval():
    pack = _pack()
    tampered = replace(pack, legal_basis_ref="legal/other")  # hash not recomputed
    with pytest.raises(PackError) as e:
        validate_pack(tampered)
    assert e.value.code == "PACK_HASH_MISMATCH"
    assert compute_hash(pack) == pack.policy_hash
    assert compute_hash(sealed(tampered)) != pack.policy_hash


def test_dates_and_identity_are_checked():
    with pytest.raises(PackError) as e:
        validate_pack(_pack(valid_until=PAST - timedelta(days=1)))
    assert e.value.code == "PACK_DATES_INVALID"
    with pytest.raises(PackError) as e:
        validate_pack(_pack(country_iso2="usa"))
    assert e.value.code == "PACK_COUNTRY_INVALID"
    with pytest.raises(PackError) as e:
        validate_pack(_pack(version=0))
    assert e.value.code == "PACK_IDENTITY_INVALID"


def test_a_registry_refuses_a_duplicate_pack_version():
    with pytest.raises(PackError) as e:
        validate_registry(_reg(_pack(), _pack()))
    assert e.value.code == "PACK_VERSION_DUPLICATE"


# --- loading and the public view --------------------------------------------------------------------------------------


def test_parse_builds_a_registry_and_refuses_a_malformed_row():
    doc = {
        "version": "7",
        "disabled_countries": ["ru"],
        "packs": [
            {
                "pack_id": "gb-design",
                "version": 1,
                "country_iso2": "GB",
                "service_code": "academic.verify",
                "feature_state": "DESIGN",
                "data_residency_region": "uk",
                "allowed_processing_regions": ["uk"],
                "valid_from": "2026-01-01",
            }
        ],
    }
    reg = parse(doc)
    assert reg.version == "7" and reg.disabled_countries == {"RU"}
    assert reg.packs[0].feature_state is FeatureState.DESIGN
    assert reg.packs[0].valid_from == datetime(2026, 1, 1, tzinfo=UTC)
    for bad in (
        {"packs": [{"pack_id": "x", "feature_state": "LIVE"}]},  # not a state
        {"packs": [{"pack_id": "x", "surprise": 1}]},  # unknown field
    ):
        with pytest.raises(PackError) as e:
            parse(bad)
        assert e.value.code == "PACK_REGISTRY_INVALID"


def test_the_public_view_names_scope_and_state_but_no_legal_or_source_detail():
    view = public_view(
        _reg(_pack(), _pack(pack_id="d", feature_state=FeatureState.DESIGN)),
        now=NOW,
        environment="production",
    )
    assert [v["state"] for v in view] == ["LIVE_PROVEN", "UNAVAILABLE"]
    flat = str(view)
    for secret in ("legal/us-ca-law-1", "src-calbar", "legal", "qa-1"):
        assert secret not in flat.replace("us-ca-law", "")


def test_a_draft_pack_needs_no_hash_but_a_reliable_one_does():
    draft = replace(_pack(feature_state=FeatureState.DESIGN), policy_hash="")
    validate_pack(draft)
    with pytest.raises(PackError) as e:
        validate_pack(replace(_pack(feature_state=FeatureState.PARTNER_ONLY), policy_hash=""))
    assert e.value.code == "PACK_HASH_MISMATCH"
