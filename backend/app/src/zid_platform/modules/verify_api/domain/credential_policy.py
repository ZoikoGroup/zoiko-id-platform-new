"""What a partner can rely on about credentials, rotation and revocation (ZID-CP-WF10 v2.0, WF-10.04; CR-173). Pure domain, no I/O.

"Revoke instantly" is a promise; "a revoked credential is refused on the next call, and no token lives longer than fifteen minutes" is a measurable statement. This module states
the second kind, built from the platform's own constants so the statement cannot drift from the behaviour. The behaviours themselves are proved by existing tests, named below.
"""

from __future__ import annotations

from typing import Any

from zid_platform.modules.verify_api.domain import model as m

PROVED_BY = {
    "revocation_applies_to_issued_tokens": "backend/app/tests/component/test_verify_api_pg.py::test_revoking_a_credential_or_suspending_an_application_ends_its_tokens_at_once",
    "rotation_overlap_is_bounded": "backend/app/tests/component/test_verify_api_pg.py::test_a_rotated_credential_keeps_the_old_one_working_for_a_bounded_overlap_and_no_longer",
    "scope_removal_applies_to_issued_tokens": "backend/app/tests/component/test_wf10_scopes_pg.py::test_a_removed_scope_is_refused_on_the_next_call_even_for_a_token_issued_before_and_a_staged_removal_waits",
}


def policy(
    objective_seconds: float | None = None,
    measured_p99_seconds: float | None = None,
    measured_count: int = 0,
) -> dict[str, Any]:
    return {
        "access_token_lifetime_seconds": int(m.TOKEN_TTL.total_seconds()),
        "credential_lifetime_days": m.CREDENTIAL_TTL.days,
        "rotation": {
            "default_overlap_hours": int(m.DEFAULT_OVERLAP.total_seconds() // 3600),
            "max_overlap_days": m.MAX_OVERLAP.days,
            "how": "A new credential is issued at once. The old one keeps working for the overlap, then stops. Its last-used time is shown so you can see whether anything still uses it.",
        },
        "containment": {
            "applies_on_the_next_call": [
                "a revoked credential",
                "a suspended or retired application",
                "a removed scope",
                "a credential past its overlap",
                "a call from outside the address allowlist",
            ],
            "longest_an_issued_token_can_be_used_after_these_seconds": 0,
            "upper_bound_if_a_token_is_stolen_and_the_credential_is_not_revoked_seconds": int(
                m.TOKEN_TTL.total_seconds()
            ),
            "statement": "This API checks the credential, the application and the scopes on every call, not only when the token is issued. A revoked credential, a suspended application or a removed scope is therefore refused on the next call. A token that was stolen but whose credential is NOT revoked works until it expires, at most the access token lifetime.",
            "not_covered": "Systems that cache a token and do not call this API are outside this guarantee. This is a design statement. The numeric objective, if the owners have ratified one, and what was actually measured are in revocation_objective.",
        },
        "revocation_objective": objective(objective_seconds, measured_p99_seconds, measured_count),
        "secrets": "A client secret is shown once when it is created and kept only as a hash. It cannot be read back through the API or found in an audit record. A key-based credential never has a secret.",
        "proved_by": PROVED_BY,
    }


def objective(ratified: float | None, measured_p99: float | None, count: int) -> dict[str, Any]:
    """The containment objective as a pair: what the owners have ratified (nothing until they do) and what has been measured. A number is never published as a promise unless it is ratified, and a
    ratified number the measurements do not meet is shown as not met, not hidden."""
    if ratified is None:
        status, said = (
            "NOT_RATIFIED",
            "No numeric objective has been ratified. The measured figure below is what the platform actually did.",
        )
    elif measured_p99 is None or count < 1:
        status, said = (
            "NOT_MEASURED_YET",
            f"The owners ratified {ratified:g} seconds, and no revocation has been refused since the platform started measuring, so it is not yet shown to be met.",
        )
    elif measured_p99 <= ratified:
        status, said = (
            "MET",
            f"Ratified at {ratified:g} seconds. The measured 99th percentile is {measured_p99:g} seconds over {count} refusals.",
        )
    else:
        status, said = (
            "NOT_MET",
            f"Ratified at {ratified:g} seconds, but the measured 99th percentile is {measured_p99:g} seconds over {count} refusals.",
        )
    return {
        "ratified_seconds": ratified,
        "measured_p99_seconds": measured_p99,
        "measured_refusals": count,
        "status": status,
        "statement": said,
        "measured_by": "zid_verify_api_revocation_to_refusal_seconds: the time from a credential's revocation to each later refusal of one of its tokens",
        "caveat": "Measured on this platform's own refusals in the current process. It says nothing about systems that cache a token.",
    }
