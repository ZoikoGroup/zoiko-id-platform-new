"""The environment check on a session (ZID-EXT-IDV-001 Q06): a caller of one environment is refused a session of the other, even if some other check were ever to let the row through."""

from __future__ import annotations

from datetime import UTC, datetime

import pytest
from zid_platform.modules.verify_api.application import sessions_service
from zid_platform.modules.verify_api.application.service import VerifyApiCaller
from zid_platform.modules.verify_api.domain.model import PartnerError


def caller(environment: str) -> VerifyApiCaller:
    return VerifyApiCaller(
        tenant_id="t",
        application_id="a",
        credential_id="c",
        environment=environment,
        scopes=("verifications:read",),
        expires_at=datetime(2030, 1, 1, tzinfo=UTC),
    )


def test_a_caller_may_touch_only_a_session_of_its_own_environment() -> None:
    sessions_service._own_environment(caller("SANDBOX"), {"environment": "SANDBOX"})
    sessions_service._own_environment(caller("PRODUCTION"), {"environment": "PRODUCTION"})
    for mine, theirs in (("SANDBOX", "PRODUCTION"), ("PRODUCTION", "SANDBOX")):
        with pytest.raises(PartnerError) as e:
            sessions_service._own_environment(caller(mine), {"environment": theirs})
        assert e.value.code == "ENVIRONMENT_MISMATCH"
