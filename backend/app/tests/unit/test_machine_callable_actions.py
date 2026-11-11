"""CR-066: which actions a signed-key service client may call without a person's passkey. The list is deliberately one entry long, and these tests make
adding another a visible, reviewed change rather than a quiet line in a catalogue."""

from __future__ import annotations

from zid_platform.modules.authentication.infrastructure import step_up_routes as sr
from zid_platform.protected import catalog

EXPECTED = {"machine.build.register"}


def test_only_registering_a_build_is_callable_by_a_machine() -> None:
    marked = {
        name
        for name, spec in catalog().items()
        if spec.get("machine_callable") is not False and "machine_callable" in spec
    }
    assert marked == EXPECTED, (
        "adding a machine-callable action changes who is trusted without a passkey: update this test with the change request"
    )


def test_a_machine_callable_action_is_a_proposal_never_an_approval_or_a_release() -> None:
    for name in EXPECTED:
        spec = catalog()[name]
        assert spec["min_risk_class"] in ("R1", "R2"), (
            f"{name}: an R3 action needs more than a signed key"
        )
        assert (
            ".manage" not in name
            and "approve" not in name
            and "revoke" not in name
            and "activate" not in name
        )


def test_the_step_up_exemption_and_the_catalogue_mark_agree() -> None:
    """A route is exempt only if its action is machine-callable too: two separate, explicit opt-ins that must both be true."""
    sr.route_actions.cache_clear()
    exempt_routes = {r.match for r in sr.route_actions().rules if r.machine_exempt}
    assert exempt_routes == {"POST /v1/machine/builds"}
    assert EXPECTED == {
        name for name, spec in catalog().items() if spec.get("machine_callable") is True
    }


def test_the_pipeline_scope_is_one_string_in_the_two_modules_that_use_it() -> None:
    """The integration module issues pipeline clients with it; the session module recognises them by it. Two modules, one string, kept equal here."""
    from zid_platform.modules.integration.application.service import PIPELINE_SCOPE
    from zid_platform.modules.session.domain.oauth import BUILD_PIPELINE_SCOPE

    assert PIPELINE_SCOPE == BUILD_PIPELINE_SCOPE == "machine.build:register"
