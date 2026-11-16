"""CR-094: what an API-based agent's token is worth. Possession of its own key, proven per request, is Z1 and no more."""

from __future__ import annotations

import pytest
from zid_platform.modules.authentication.domain.assurance import AAL, REQUIRED_BY_RISK
from zid_platform.modules.decision.domain.pdp import AUTHN_FLOOR, RiskClass
from zid_platform.modules.session.application.authenticate import _is_api_agent_credential

WORKLOAD = frozenset({"workload"})


@pytest.mark.parametrize(
    ("client_id", "scope", "proof", "expected"),
    [
        ("api-agent:zid-api-abc", WORKLOAD, True, True),
        (
            "api-agent:zid-api-abc",
            WORKLOAD,
            False,
            False,
        ),  # no verified proof of the bound key on this request
        ("api-agent:zid-api-abc", frozenset({"admin"}), True, False),  # not a workload token
        ("agent-runtime", WORKLOAD, True, False),  # another client's token never gets it
        ("API-AGENT:zid-api-abc", WORKLOAD, True, False),  # exact prefix only
        ("", WORKLOAD, True, False),
    ],
)
def test_only_a_proven_api_agent_credential_counts(
    client_id: str, scope: frozenset[str], proof: bool, expected: bool
) -> None:
    assert _is_api_agent_credential(client_id, scope, proof) is expected


def test_z1_clears_the_r1_floor_and_nothing_above_it() -> None:
    assert AUTHN_FLOOR[RiskClass.R0][0] <= AAL.Z1 and AUTHN_FLOOR[RiskClass.R1][0] <= AAL.Z1
    assert AUTHN_FLOOR[RiskClass.R2][0] > AAL.Z1 and AUTHN_FLOOR[RiskClass.R3][0] > AAL.Z1
    assert REQUIRED_BY_RISK[2] > AAL.Z1 and REQUIRED_BY_RISK[3] > AAL.Z1
