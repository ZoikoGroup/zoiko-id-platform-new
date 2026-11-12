"""Authority and property dimensions from supplied evidence (ZID-INT-002 sections 06, 10, 11; Doc 01 DTV-10, DTV-24). Pure domain.

The document's separation, made executable: `LEI exists != mandate`, `official title != right to let`, `owner != agent`, and a country's rule never
becomes another country's pass. With no register of officers, no land register and no regulator source configured, the most this module can
say about a mandate, a title or a permission is VERIFIED_LIMITED: the document is as signed by a certificate this deployment trusts, and
nothing about whether that signatory was entitled. It never returns VERIFIED, and never ELIGIBLE.

Dimension outcomes use the document's evidence vocabulary (VERIFIED, VERIFIED_LIMITED, NOT_VERIFIED, INDETERMINATE, UNAVAILABLE). The overall
answer uses the separate eligibility vocabulary (ELIGIBLE, NOT_ELIGIBLE, REVIEW_REQUIRED, UNKNOWN) and is capped at REVIEW_REQUIRED.
"""

from __future__ import annotations

from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from typing import Any

LIMITS_OF_A_SIGNED_DOCUMENT = (
    "signatory_not_matched_to_an_officer_or_title_register",
    "scope_of_authority_for_this_act_not_confirmed",
)
AGENT_RELATIONSHIPS = frozenset({"authorized_agent", "property_manager"})
RELATIONSHIPS = frozenset(
    {"owner", "joint_owner", "authorized_agent", "property_manager", "tenant_sublet"}
)
DIMENSIONS = frozenset(
    {"identity", "ownership", "mandate", "permission_to_list", "tenant_legal_route"}
)
ENGLAND = ("GB", "GB-ENG")


class AuthorityError(ValueError):
    def __init__(self, code: str, detail: str = "") -> None:
        super().__init__(f"{code}: {detail}" if detail else code)
        self.code = code


@dataclass(frozen=True, slots=True)
class Dimension:
    dimension: str
    outcome: str
    reasons: tuple[str, ...] = ()
    limitations: tuple[str, ...] = ()
    evidence_refs: tuple[str, ...] = ()
    required: bool = True

    def public(self) -> dict[str, Any]:
        return {
            "dimension": self.dimension,
            "outcome": self.outcome,
            "required": self.required,
            "reasons": list(self.reasons),
            "limitations": list(self.limitations),
            "evidence_refs": list(self.evidence_refs),
        }


def from_evidence(
    dimension: str, assessments: Sequence[Mapping[str, Any]], *, absent_reason: str
) -> Dimension:
    """The dimension a set of supplied documents supports. Every document counts only if usable; one that fails its signature poisons the
    dimension (NOT_VERIFIED) rather than being quietly ignored in favour of another; and the best case is VERIFIED_LIMITED."""
    refs = tuple(str(a["evidence_ref"]) for a in assessments)
    if not assessments:
        return Dimension(dimension, "INDETERMINATE", (absent_reason,))
    if any(not a["usable"] for a in assessments):
        return Dimension(dimension, "INDETERMINATE", ("EVIDENCE_NOT_USABLE",), evidence_refs=refs)
    if any(a["signature_status"] == "NOT_VERIFIED" for a in assessments):
        reasons = sorted(
            {r for a in assessments for r in a["signature_reasons"]} or {"SIGNATURE_INVALID"}
        )
        return Dimension(dimension, "NOT_VERIFIED", tuple(reasons), evidence_refs=refs)
    if all(a["signed_and_unchanged"] for a in assessments):
        return Dimension(
            dimension,
            "VERIFIED_LIMITED",
            ("SIGNED_DOCUMENT_CHAINS_TO_A_CONFIGURED_ANCHOR",),
            LIMITS_OF_A_SIGNED_DOCUMENT,
            refs,
        )
    return Dimension(
        dimension, "INDETERMINATE", ("EVIDENCE_NOT_SIGNED_OR_NOT_TRUSTED",), evidence_refs=refs
    )


def _not_required(dimension: str, why: str) -> Dimension:
    return Dimension(dimension, "INDETERMINATE", (why,), required=False)


def lei_status(lei: str | None) -> Dimension:
    """A Legal Entity Identifier's own check digits (ISO 17442: the 20 characters read as one number, modulo 97, must leave 1). That is all
    this can say: a well-formed LEI is not an entity that exists or is active, and it is never a mandate."""
    if not lei:
        return Dimension(
            "legal_entity", "INDETERMINATE", ("NO_LEGAL_ENTITY_IDENTIFIER_GIVEN",), required=False
        )
    text = lei.strip().upper()
    if len(text) != 20 or not text.isalnum():
        return Dimension("legal_entity", "NOT_VERIFIED", ("LEI_MALFORMED",), required=False)
    number = int("".join(str(int(c, 36)) for c in text))
    if number % 97 != 1:
        return Dimension(
            "legal_entity", "NOT_VERIFIED", ("LEI_CHECK_DIGITS_INVALID",), required=False
        )
    return Dimension(
        "legal_entity",
        "INDETERMINATE",
        ("LEI_WELL_FORMED_EXISTENCE_NOT_LOOKED_UP_HERE",),
        (
            "a well-formed LEI is not proof that the entity exists or is active; use /v2/organizations/verify",
        ),
        required=False,
    )


def mandate_verification(
    assessments: Sequence[Mapping[str, Any]], lei: str | None = None
) -> dict[str, Any]:
    """`POST /v2/mandates/verify`: the legal-entity authority dimension. The entity's existence is a separate check (`/v2/organizations/verify`)
    and says nothing here: a registered company with no mandate on file is INDETERMINATE (test T30)."""
    mandate = from_evidence("mandate", assessments, absent_reason="MANDATE_EVIDENCE_ABSENT")
    return {
        "legal_entity": lei_status(lei).public(),
        "authority": mandate.public(),
        "authority_assurance": "SIGNED_DOCUMENT_ONLY"
        if mandate.outcome == "VERIFIED_LIMITED"
        else "NONE",
    }


def _overall(required: Sequence[Dimension]) -> str:
    outcomes = {d.outcome for d in required}
    if "NOT_VERIFIED" in outcomes:
        return "NOT_ELIGIBLE"
    if outcomes and outcomes <= {"VERIFIED_LIMITED", "VERIFIED"}:
        return "REVIEW_REQUIRED"  # a person decides: nothing here is a source's confirmation
    return "UNKNOWN"


def _legal_route(jurisdiction: Mapping[str, str]) -> Dimension:
    """The tenant's legal route is jurisdiction-specific and is never inferred. England's digital Right to Rent needs a registered provider
    that this platform is not (test T25); any other place has no rule here at all."""
    place = (jurisdiction.get("country", ""), jurisdiction.get("subdivision", ""))
    if place[0] == "GB" and place[1] in {"", "GB-ENG"}:
        return Dimension(
            "tenant_legal_route",
            "UNAVAILABLE",
            ("RIGHT_TO_RENT_REQUIRES_A_REGISTERED_PROVIDER",),
            ("Zoiko iD is not that provider; the landlord's own check duties are unchanged",),
        )
    return Dimension(
        "tenant_legal_route",
        "UNAVAILABLE",
        ("NO_RULE_FOR_THIS_JURISDICTION",),
        (
            "a rule for another country is never applied by default, and England's is not applied here",
        ),
    )


def property_check(
    *,
    relationship: str,
    requested: Sequence[str],
    jurisdiction: Mapping[str, str],
    title: Sequence[Mapping[str, Any]],
    mandate: Sequence[Mapping[str, Any]],
    permission: Sequence[Mapping[str, Any]],
) -> dict[str, Any]:
    """`POST /v2/rooms/property-checks`: the dimensions, each with its own outcome, and a capped overall answer. Official title does not
    permit an agent to list (test T24): an agent needs a mandate that stands on its own."""
    if relationship not in RELATIONSHIPS:
        raise AuthorityError("RELATIONSHIP_UNKNOWN", relationship)
    unknown = [d for d in requested if d not in DIMENSIONS]
    if unknown or not requested:
        raise AuthorityError("DIMENSION_UNKNOWN", ",".join(unknown) or "none requested")
    is_agent = relationship in AGENT_RELATIONSHIPS
    wants = set(requested)
    dims: list[Dimension] = []
    if "identity" in wants:
        dims.append(
            Dimension(
                "identity",
                "INDETERMINATE",
                ("IDENTITY_IS_CHECKED_BY_THE_IDENTITY_AND_CREDENTIAL_ROUTES",),
                required=False,
            )
        )
    if "ownership" in wants:
        dims.append(from_evidence("ownership", title, absent_reason="TITLE_EVIDENCE_ABSENT"))
    if "mandate" in wants:
        dims.append(
            from_evidence("mandate", mandate, absent_reason="MANDATE_EVIDENCE_ABSENT")
            if is_agent
            else _not_required("mandate", "RELATIONSHIP_NEEDS_NO_MANDATE")
        )
    if "permission_to_list" in wants:
        dims.append(
            from_evidence(
                "permission_to_list", permission, absent_reason="PERMISSION_EVIDENCE_ABSENT"
            )
            if relationship == "tenant_sublet"
            else _not_required("permission_to_list", "RELATIONSHIP_NEEDS_NO_SUBLETTING_PERMISSION")
        )
    if "tenant_legal_route" in wants:
        dims.append(_legal_route(jurisdiction))
    judged = [d for d in dims if d.required and d.dimension != "tenant_legal_route"]
    # an agent's listing authority also depends on a mandate even if the caller did not ask for that dimension
    if is_agent and "mandate" not in wants:
        judged.append(from_evidence("mandate", mandate, absent_reason="MANDATE_EVIDENCE_ABSENT"))
    return {
        "dimensions": [d.public() for d in dims],
        "listing_authority": _overall(judged),
        "listing_authority_note": "capped at REVIEW_REQUIRED: this check never confirms a right to let, and the marketplace decides",
    }
