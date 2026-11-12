"""The policy pack gate rule (ZID-RCG-003 T01, T02): ENABLED in the country registry is not enough; an approved pack must also govern."""

from __future__ import annotations

from typing import Any

import pytest
from zid_platform.modules.verification.domain.countries import Availability
from zid_platform.modules.verification.domain.policy_packs import (
    GATE_MODES,
    Outcome,
    PackError,
    Resolution,
    gate_decision,
    pack_needed,
)

APPLICABLE = Resolution(Outcome.APPLICABLE, "pack_in_force")
UNAVAILABLE = Resolution(Outcome.UNAVAILABLE, "market_not_approved")
REVIEW = Resolution(Outcome.REVIEW, "policy_missing")


def test_a_capability_that_is_not_enabled_is_left_to_the_country_gate_and_no_pack_is_read() -> None:
    for mode in ("shadow", "enforce"):
        for state in (Availability.DISABLED, Availability.SANDBOX):
            assert not pack_needed(mode, state)
            d = gate_decision(mode, state, None)
            assert (d.allowed, d.would_refuse, d.reason) == (True, False, "country_gate_decides")


def test_off_never_consults_the_packs() -> None:
    assert not pack_needed("off", Availability.ENABLED)
    d = gate_decision("off", Availability.ENABLED, None)
    assert (d.allowed, d.would_refuse, d.reason) == (True, False, "off")


def test_t01_an_enabled_market_with_no_approved_pack_is_refused_in_enforce_and_only_reported_in_shadow() -> (
    None
):
    enforce = gate_decision("enforce", Availability.ENABLED, UNAVAILABLE)
    assert (enforce.allowed, enforce.would_refuse, enforce.code) == (
        False,
        True,
        "REGULATORY_POLICY_UNAVAILABLE",
    )
    shadow = gate_decision("shadow", Availability.ENABLED, UNAVAILABLE)
    assert (shadow.allowed, shadow.would_refuse, shadow.code) == (
        True,
        True,
        "REGULATORY_POLICY_UNAVAILABLE",
    )


def test_t02_too_little_precision_needs_a_review_never_a_pass() -> None:
    enforce = gate_decision("enforce", Availability.ENABLED, REVIEW)
    assert (enforce.allowed, enforce.code, enforce.reason) == (
        False,
        "REGULATORY_REVIEW_REQUIRED",
        "policy_missing",
    )
    assert gate_decision("shadow", Availability.ENABLED, REVIEW).allowed  # reported, not blocked


def test_a_governing_pack_lets_the_request_go_on() -> None:
    for mode in ("shadow", "enforce"):
        d = gate_decision(mode, Availability.ENABLED, APPLICABLE)
        assert (d.allowed, d.would_refuse, d.code) == (True, False, None)


def test_a_missing_resolution_counts_as_no_pack_not_as_a_pass() -> None:
    d = gate_decision("enforce", Availability.ENABLED, None)
    assert (d.allowed, d.code, d.reason) == (
        False,
        "REGULATORY_POLICY_UNAVAILABLE",
        "no_resolution",
    )
    assert gate_decision("shadow", Availability.ENABLED, None).would_refuse


def test_an_unknown_mode_is_an_error_not_a_default() -> None:
    assert GATE_MODES == ("off", "shadow", "enforce")
    with pytest.raises(PackError) as e:
        pack_needed("sometimes", Availability.ENABLED)
    assert e.value.code == "PACK_GATE_MODE_UNKNOWN"


# ---- the switch and the wiring ----------------------------------------------------------------------------------------


def _start(**kw: Any) -> None:
    from zid_crypto import KeyRing, LocalSigner
    from zid_platform.main import create_app
    from zid_platform.settings import Settings

    create_app(
        Settings(
            token_issuer="https://i.example",
            token_audience="https://a.example",
            public_base_url="https://a.example",
            **kw,
        ),
        keyring=KeyRing(LocalSigner("k")),
    )


@pytest.mark.parametrize("env", ["staging", "production"])
def test_a_deployed_cell_cannot_switch_the_pack_check_off(env: str) -> None:
    with pytest.raises(RuntimeError, match="ZID_REGULATORY_PACKS=off"):
        _start(environment=env, regulatory_packs="off")


def test_off_is_allowed_locally_and_shadow_is_the_default() -> None:
    from zid_platform.settings import Settings

    _start(environment="local", regulatory_packs="off")
    assert (
        Settings(
            token_issuer="https://i.example",
            token_audience="https://a.example",
            public_base_url="https://a.example",
        ).regulatory_packs
        == "shadow"
    )


def test_every_individual_verification_entry_point_asks_the_pack_gate_after_the_country_gate() -> (
    None
):
    """A new way to start a verification cannot skip the check quietly: start, bulk start and reverify each call `_country_gate` and then
    `_pack_gate`, before any provider is asked."""
    import ast
    from pathlib import Path

    path = (
        Path(__file__).resolve().parents[2]
        / "src/zid_platform/modules/verification/api/verification.py"
    )
    tree = ast.parse(path.read_text(encoding="utf-8"))
    wired = {}
    for fn in (
        n
        for n in ast.walk(tree)
        if isinstance(n, ast.AsyncFunctionDef) and n.name in {"start", "start_bulk", "reverify"}
    ):
        calls = [
            (c.lineno, c.func.id)
            for c in ast.walk(fn)
            if isinstance(c, ast.Call)
            and isinstance(c.func, ast.Name)
            and c.func.id in {"_country_gate", "_pack_gate"}
        ]
        wired[fn.name] = [name for _, name in sorted(calls)]
    assert wired == {
        name: ["_country_gate", "_pack_gate"] for name in ("start", "start_bulk", "reverify")
    }
