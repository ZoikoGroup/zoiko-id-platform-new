"""Public Protocol interface of the organization module. Other modules import ONLY this.

The authority module reads roots through `RootReader` to validate the top of a grant chain; it never
touches org.* tables or the organization domain directly.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime
from decimal import Decimal
from typing import Protocol

from sqlalchemy.ext.asyncio import AsyncConnection

from zid_platform.modules.organization.application import kyb_market_policy as kyb_policy
from zid_platform.modules.organization.application import proof as proof_service
from zid_platform.modules.organization.application.identity import (
    identifiers_of,
    legal_name_history,
    link_identifier,
    quarantine_source,
    release_source,
    resolve_organization,
    source_is_quarantined,
)
from zid_platform.modules.organization.application.kyb import (
    CaseView as KybCaseView,
)
from zid_platform.modules.organization.application.kyb import (
    awaiting_compliance as kyb_awaiting_compliance,
)
from zid_platform.modules.organization.application.kyb import (
    close_claim as kyb_close,
)
from zid_platform.modules.organization.application.kyb import (
    decide as kyb_decide,
)
from zid_platform.modules.organization.application.kyb import (
    drafts_before as kyb_drafts_before,
)
from zid_platform.modules.organization.application.kyb import (
    get_case as kyb_get_case,
)
from zid_platform.modules.organization.application.kyb import (
    history as kyb_history,
)
from zid_platform.modules.organization.application.kyb import (
    open_claims as kyb_open_claims,
)
from zid_platform.modules.organization.application.kyb import (
    refresh as kyb_refresh,
)
from zid_platform.modules.organization.application.kyb import (
    register_people as kyb_register_people,
)
from zid_platform.modules.organization.application.kyb import (
    request_verification as kyb_request_verification,
)
from zid_platform.modules.organization.application.kyb import (
    review as kyb_review,
)
from zid_platform.modules.organization.application.kyb import (
    search as kyb_search,
)
from zid_platform.modules.organization.application.kyb import (
    submit_claim as kyb_submit,
)
from zid_platform.modules.organization.application.kyb_assessment import GateError as KybGateError
from zid_platform.modules.organization.application.kyb_company import gate_action as kyb_gate_action
from zid_platform.modules.organization.application.registry import (
    EstablishedRoot,
    NameMatcher,
    establish_claim_root,
    establish_development_root,
    establish_registry_root,
    refresh_registry_roots,
    request_registry_refresh,
)
from zid_platform.modules.organization.domain import market_policy as kyb_policy_rules
from zid_platform.modules.organization.domain.dual_review import position as dual_position
from zid_platform.modules.organization.domain.kyb import (
    Applicant as KybApplicant,
)
from zid_platform.modules.organization.domain.kyb import (
    KybError,
    KybStatus,
    RegisterRead,
)
from zid_platform.modules.organization.domain.kyb import (
    Review as KybReview,
)
from zid_platform.modules.organization.domain.kyb import (
    check_applicant as kyb_check_applicant,
)
from zid_platform.modules.organization.domain.proof import (
    ProofError,
    ProofPolicy,
    check_proof,
    sanitize_name,
)
from zid_platform.modules.organization.domain.registry import RegistryError, check_identifier
from zid_platform.modules.organization.infrastructure.kyb_adapters import (
    CompaniesHouseKyb,
    CreditsafeKyb,
    GleifKyb,
    KybAdapter,
    SireneKyb,
    default_adapters,
)
from zid_platform.modules.organization.infrastructure.kyb_config import routing as kyb_routing
from zid_platform.modules.organization.infrastructure.kyb_store import (
    latest_case_id as latest_kyb_case_id,
)
from zid_platform.modules.organization.infrastructure.proof_config import (
    parse as parse_proof_policy,
)
from zid_platform.modules.organization.infrastructure.proof_config import proof_policy
from zid_platform.modules.organization.infrastructure.proof_scan import (
    Scanner,
    ScanStatus,
)
from zid_platform.modules.organization.infrastructure.proof_scan import (
    from_address as proof_scanner_from_address,
)
from zid_platform.modules.organization.infrastructure.registry_adapters import (
    CompaniesHouse,
    Gleif,
    RegistryAdapter,
    adapter_for,
)
from zid_platform.modules.organization.infrastructure.root_store import organization_exists

__all__ = [
    "CompaniesHouse",
    "CompaniesHouseKyb",
    "CreditsafeKyb",
    "EstablishedRoot",
    "Gleif",
    "GleifKyb",
    "KybAdapter",
    "KybApplicant",
    "KybCaseView",
    "KybError",
    "KybGateError",
    "KybReview",
    "KybStatus",
    "NameMatcher",
    "ProofError",
    "ProofPolicy",
    "RegisterRead",
    "RegistryAdapter",
    "RegistryError",
    "RootReader",
    "RootView",
    "ScanStatus",
    "Scanner",
    "SireneKyb",
    "adapter_for",
    "check_identifier",
    "check_proof",
    "default_adapters",
    "dual_position",
    "establish_claim_root",
    "establish_development_root",
    "establish_registry_root",
    "has_usable_root",
    "identifiers_of",
    "kyb_awaiting_compliance",
    "kyb_check_applicant",
    "kyb_close",
    "kyb_decide",
    "kyb_drafts_before",
    "kyb_gate_action",
    "kyb_get_case",
    "kyb_history",
    "kyb_open_claims",
    "kyb_policy",
    "kyb_policy_rules",
    "kyb_refresh",
    "kyb_register_people",
    "kyb_request_verification",
    "kyb_review",
    "kyb_routing",
    "kyb_search",
    "kyb_submit",
    "latest_kyb_case_id",
    "legal_name_history",
    "link_identifier",
    "organization_exists",
    "parse_proof_policy",
    "proof_policy",
    "proof_scanner_from_address",
    "proof_service",
    "quarantine_source",
    "read_root",
    "refresh_registry_roots",
    "release_source",
    "request_registry_refresh",
    "resolve_organization",
    "sanitize_name",
    "source_is_quarantined",
    "usable_root_for",
]


@dataclass(frozen=True, slots=True)
class RootView:
    """What other modules may know about an authority root (ZID-ROOT-001 §13, minimized)."""

    root_id: str
    tenant_id: str
    organization_id: str
    holder_principal_id: str
    assurance: int  # 0..3 = AR-0..AR-3
    usable: bool  # ACTIVE (STALE/CONFLICTED/SUSPENDED/REVOKED/EXPIRED/SUPERSEDED are not)
    stale: bool  # STALE or CONFLICTED: current validity can't be established
    valid_from: datetime
    valid_to: datetime | None
    fresh_until: datetime
    scope_actions: frozenset[str] | None
    value_limit: Decimal | None
    currency: str | None


class RootReader(Protocol):
    async def __call__(
        self, conn: AsyncConnection, tenant_id: str, root_id: str
    ) -> RootView | None: ...


async def company_claim(conn: AsyncConnection, tenant_id: str) -> tuple[str, datetime] | None:
    """The company's own verification claim for a screening: COMPANY_REGISTRY_MATCHED once the stored grade is K2 or higher, with the time it was assessed. Documents alone (K1) are not enough."""
    from zid_platform.modules.organization.application import kyb_assessment

    stored = await kyb_assessment.latest(conn, tenant_id)
    if stored is None or kyb_assessment.g.rank(stored.grade) < kyb_assessment.g.rank(kyb_assessment.g.Grade.K2):
        return None
    return "COMPANY_REGISTRY_MATCHED", stored.assessed_at


async def read_root(conn: AsyncConnection, tenant_id: str, root_id: str) -> RootView | None:
    """Default RootReader backed by the organization store."""
    from zid_platform.modules.organization.domain.roots import RootStatus
    from zid_platform.modules.organization.infrastructure.root_store import load_root

    r = await load_root(conn, root_id)
    if r is None:
        return None
    return RootView(
        root_id=r.root_id,
        tenant_id=tenant_id,
        organization_id=r.organization_id,
        holder_principal_id=r.holder_principal_id,
        assurance=int(r.assurance),
        usable=r.status is RootStatus.ACTIVE,
        stale=r.status in (RootStatus.STALE, RootStatus.CONFLICTED),
        valid_from=r.valid_from,
        valid_to=r.valid_to,
        fresh_until=r.fresh_until,
        scope_actions=r.scope_actions,
        value_limit=r.value_limit,
        currency=r.currency,
    )


async def usable_root_for(
    conn: AsyncConnection, tenant_id: str, organization_id: str, principal_id: str, now: datetime
) -> str | None:
    """The id of an ACTIVE, fresh authority root this person holds for the organization, else None. A registered company
    alone is not a person's authority: the root exists only when the person was matched to a current officer."""
    from zid_platform.modules.organization.infrastructure.root_store import load_roots

    for r in await load_roots(conn, organization_id):
        view = await read_root(conn, tenant_id, r.root_id)
        if (
            view is not None
            and view.usable
            and view.fresh_until > now
            and view.holder_principal_id == principal_id
        ):
            return view.root_id
    return None


async def has_usable_root(
    conn: AsyncConnection, tenant_id: str, organization_id: str, now: datetime
) -> bool:
    """Does the organization hold an ACTIVE, still-fresh authority root (onboarding gate)?"""
    from zid_platform.modules.organization.infrastructure.root_store import load_roots

    for r in await load_roots(conn, organization_id):
        view = await read_root(conn, tenant_id, r.root_id)
        if view is not None and view.usable and view.fresh_until > now:
            return True
    return False
