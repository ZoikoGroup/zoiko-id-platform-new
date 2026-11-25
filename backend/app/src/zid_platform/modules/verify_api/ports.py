"""What other modules may use from the Verify API module (the only import surface; test_architecture)."""

from zid_platform.modules.verify_api.api.cases import fail as case_fail
from zid_platform.modules.verify_api.api.go_live import fail as go_live_fail
from zid_platform.modules.verify_api.api.verify_api import VerifyApiDep, verify_api_caller
from zid_platform.modules.verify_api.application import (
    cases_service,
    go_live_service,
    logo_service,
    pricing_service,
    review_service,
)
from zid_platform.modules.verify_api.application.service import VerifyApiCaller
from zid_platform.modules.verify_api.domain.model import PartnerError
from zid_platform.modules.verify_api.infrastructure.go_live_store import operator_scope

__all__ = [
    "PartnerError",
    "VerifyApiCaller",
    "VerifyApiDep",
    "case_fail",
    "cases_service",
    "go_live_fail",
    "go_live_service",
    "logo_service",
    "operator_scope",
    "pricing_service",
    "review_service",
    "verify_api_caller",
]
