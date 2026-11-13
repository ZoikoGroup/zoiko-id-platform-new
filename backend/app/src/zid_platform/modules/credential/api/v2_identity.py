"""POST /v2/identity/credential-verify: the PERSON_IDENTITY dimension from a credential a wallet or eID scheme issued (Document 03 section 06; Document 01 section 01.1).

The vendor-free route to "who is this person": an issuer the deployment trusts FOR identity credentials vouched for the person's name and birth date, and
the person's wallet proved possession of the key the credential was issued to. Stateless: nothing is stored, and only the name and birth date are returned.

What the answer is, and is not (it always says so): an issuer's attestation, not a check against a population register; and key possession, not a live person.
Without holder binding the answer is INDETERMINATE, so a copied credential never identifies anyone. The credential TYPE must be one the owners accept as
evidence of identity (`config/identity-credentials`) AND the issuer must be in the typed trust table for that exact type: with neither configured, nothing passes.
"""

from __future__ import annotations

from datetime import UTC, datetime
from functools import lru_cache
from typing import Annotated, Any

import yaml
from fastapi import APIRouter, Depends, Request
from pydantic import BaseModel, ConfigDict, Field
from zid_context import current_context
from zid_db import tenant_transaction
from zid_errors import ZidError

from zid_platform.api_auth import Caller, authenticated_caller, request_engine
from zid_platform.modules.credential.api.trust import _jsonld_contexts
from zid_platform.modules.credential.application import (
    eudi_gate,
    identity_match,
    vc_verify,
    verifier,
)
from zid_platform.modules.credential.domain import eudi
from zid_platform.modules.credential.domain import person_identity as pi
from zid_platform.modules.credential.domain.claims import SubjectMatch
from zid_platform.modules.credential.domain.credentials import CredentialError
from zid_platform.modules.credential.infrastructure.registries import CONFIG
from zid_platform.protected import protected_action

router = APIRouter(tags=["credential-v2"])
CallerDep = Annotated[Caller, Depends(authenticated_caller)]
MAX_BYTES = 256 * 1024


class Expected(BaseModel):
    """Who the relying party expects to be presenting. Optional: with it, a different person is reported, never accepted."""

    model_config = ConfigDict(extra="forbid")
    family_name: str = Field(min_length=1, max_length=120)
    given_name: str = Field(min_length=1, max_length=120)
    birth_date: str | None = Field(default=None, pattern=r"^\d{4}-\d{2}-\d{2}$")


class V2IdentityIn(BaseModel):
    model_config = ConfigDict(extra="forbid")
    vc_jwt: str | None = Field(default=None, min_length=20, max_length=MAX_BYTES)
    holder_proof: str | None = Field(default=None, min_length=20, max_length=MAX_BYTES)
    credential: dict[str, Any] | None = None  # a JSON-LD credential with a Data Integrity proof
    holder_presentation: dict[str, Any] | None = None
    jurisdiction: str | None = Field(default=None, pattern=r"^[A-Z]{2}$")
    expected: Expected | None = None

    def model_post_init(self, _ctx: Any) -> None:
        if (self.vc_jwt is None) == (self.credential is None):
            raise ValueError("send exactly one of vc_jwt and credential")
        if self.vc_jwt is None and self.holder_proof is not None:
            raise ValueError(
                "holder_proof goes with vc_jwt; use holder_presentation with credential"
            )
        if self.credential is None and self.holder_presentation is not None:
            raise ValueError(
                "holder_presentation goes with credential; use holder_proof with vc_jwt"
            )


@lru_cache(maxsize=1)
def _policy() -> pi.IdentityPolicy:
    """The credential types accepted as evidence of identity. A missing or unreadable file accepts nothing."""
    try:
        doc = (
            yaml.safe_load(
                (CONFIG / "identity-credentials" / "identity-credentials.yaml").read_text(
                    encoding="utf-8"
                )
            )
            or {}
        )
        types = doc.get("credential_types") or []
        return pi.IdentityPolicy(frozenset(str(t) for t in types if isinstance(t, str)))
    except (OSError, yaml.YAMLError):
        return pi.IdentityPolicy()


def _ids() -> dict[str, str]:
    ctx = current_context()
    return {
        "request_id": ctx.request_id if ctx else "req_unknown",
        "correlation_id": ctx.correlation_id if ctx else "cor_unknown",
    }


def check_expected(identity: dict[str, str], expected: Expected) -> tuple[str, str]:
    """(verdict, reason) of the presenter against the person the relying party expected. A mismatch on a middling score or an unreadable script is
    AMBIGUOUS and goes to a person; only a clear difference is NOT_MATCHED."""
    holder = identity_match.Holder(legal_name=f"{expected.given_name} {expected.family_name}")
    decision, reasons = identity_match.match_holder(
        holder, f"{identity.get('given_name', '')} {identity.get('family_name', '')}"
    )
    if expected.birth_date and expected.birth_date != identity.get("birth_date"):
        return "NOT_MATCHED", "BIRTH_DATE_DIFFERS"
    reason = (
        reasons[0]
        if reasons
        else ("NAME_MATCHES" if decision is SubjectMatch.MATCHED else "NAME_DIFFERS")
    )
    return decision.value, reason


async def _pid_provider_refusal(
    request: Request,
    caller: Caller,
    attestation_type: str,
    out: vc_verify.VcOutcome,
    country: str | None,
) -> tuple[str, ...]:
    """An EU PID counts as evidence of identity only when an official-register attestation (CR-073) lists its issuer as a PID provider for that Member
    State on the day the credential was issued (Document 03 RCG-G11: a service is accepted only when the exact listed provider and service held the
    status on the relevant date). Anything missing is a refusal, never a default: no country, no issue date, no listing."""
    if not country:
        return ("PID_MEMBER_STATE_REQUIRED",)
    if out.issued_at is None or out.issuer_id is None:
        return ("PID_ISSUE_DATE_UNKNOWN",)
    async with tenant_transaction(request_engine(request), caller.ctx) as conn:
        klass, reasons = await eudi_gate.classify_presented(
            conn,
            attestation_type=attestation_type,
            issuer_entity=out.issuer_id,
            issuer_country=country,
            issued_at=out.issued_at,
        )
    if klass is eudi.AttestationClass.PID:
        return ()
    return tuple(reasons) or ("PID_PROVIDER_NOT_LISTED",)


@router.post("/v2/identity/credential-verify")
async def verify_identity_credential(
    body: V2IdentityIn, request: Request, caller: CallerDep
) -> dict[str, Any]:
    oauth = getattr(request.app.state, "oauth_settings", None)
    keyrings = getattr(request.app.state, "keyrings", None)
    if oauth is None or keyrings is None:
        raise ZidError("DEPENDENCY_UNAVAILABLE", "No verifier is configured.", http_status=503)
    settings = verifier.VerifierSettings(
        oauth.issuer,
        keyrings.verifier,
        getattr(request.app.state, "credential_transport", None),
        tuple(getattr(request.app.state, "verifier_x5c", ())),
    )
    now = datetime.now(UTC)
    async with tenant_transaction(request_engine(request), caller.ctx) as conn:
        await protected_action(
            conn, caller, action="credential.claim.verify", resource="credential-claim:identity"
        )
        try:
            if body.vc_jwt is not None:
                out = await vc_verify.verify_vc(
                    conn, settings, vc_jwt=body.vc_jwt, holder_proof=body.holder_proof,
                    jurisdiction=body.jurisdiction, now=now,
                )  # fmt: skip
            else:
                assert body.credential is not None
                out = await vc_verify.verify_vc_di(
                    conn, settings, _jsonld_contexts(), vc=body.credential,
                    jurisdiction=body.jurisdiction, now=now,
                    holder_presentation=body.holder_presentation,
                )  # fmt: skip
        except CredentialError as e:
            raise ZidError(
                e.code, e.code.replace("_", " ").capitalize() + ".", http_status=422
            ) from e
    r = out.result
    register_refusal: tuple[str, ...] = ()
    pid_types = [t for t in out.credential_types if eudi.PID_TYPE.match(t)]
    if pid_types and out.issuer_trusted:
        register_refusal = await _pid_provider_refusal(
            request, caller, pid_types[0], out, body.jurisdiction
        )
    outcome = pi.decide(
        pi.PersonCredentialFacts(
            integrity=r.integrity,
            issuer_trusted_for_type=out.issuer_trusted,
            holder_bound=r.holder_bound,
            status=r.credential_status,
            credential_types=out.credential_types,
            claims=out.subject_claims,
            issuer_known=out.issuer_known,
            register_refusal=register_refusal,
        ),
        _policy(),
        today=now.date(),
    )
    result = outcome.public()
    if body.expected is not None:
        if outcome.person_identity_established:
            verdict, why = check_expected(dict(outcome.identity), body.expected)
            result["expected_person"] = {"match": verdict, "reason": why}
            if verdict != "MATCHED":
                result.update(
                    status="NOT_VERIFIED" if verdict == "NOT_MATCHED" else "INDETERMINATE",
                    person_identity_established=False,
                    assurance_ceiling="SELF_DECLARED",
                    reasons=[*outcome.reasons, f"EXPECTED_PERSON_{verdict}"],
                )
        else:
            result["expected_person"] = {
                "match": "NOT_EVALUATED",
                "reason": "IDENTITY_NOT_ESTABLISHED",
            }
    return {**_ids(), **result, "credential_kind": out.kind.value, "issuer_ref": out.issuer_id}
