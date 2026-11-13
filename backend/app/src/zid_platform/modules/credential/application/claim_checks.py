"""Claim checks: verify a credential claim against a source and record the assertion (GID-037; CR-069).

Order matters and every step fails closed:
1. the claim is validated against its class (unknown class, field or value is refused);
2. a service pack must allow this country, service and profession in this environment (no pack, no check);
3. the source is asked; an exception or a timeout is SOURCE_UNAVAILABLE, never a failure of the person and never a pass;
4. the pure rule in `domain/claims.py` decides; nothing the holder wrote can raise it;
5. the assertion is stored with a digest of the claim (never its values) and an event carries ids and the decision only.
A simulator source exists for local and test environments only; a deployed environment with no real source has no
LIVE pack, so the check is refused at step 2 and the simulator is never reached.
"""

from __future__ import annotations

import hashlib
import json
import uuid
from collections.abc import Mapping, Sequence
from dataclasses import dataclass, replace
from datetime import UTC, date, datetime, timedelta
from typing import Any, Protocol

from sqlalchemy.ext.asyncio import AsyncConnection
from zid_outbox import emit

from zid_platform.modules.credential.application import assertion_contract as ac
from zid_platform.modules.credential.application import resilience as rs
from zid_platform.modules.credential.application.identity_match import Holder, match_holder
from zid_platform.modules.credential.domain import sources as src
from zid_platform.modules.credential.domain import standing
from zid_platform.modules.credential.domain import trust_graph as tg
from zid_platform.modules.credential.domain.claims import (
    SERVICE_CODES,
    Assertion,
    Authenticity,
    ClaimError,
    ClaimType,
    CredentialStatus,
    Decision,
    Eligibility,
    EvidenceTier,
    SourceResult,
    SubjectMatch,
    assertion_from_facts,
    unqueried_assertion,
    validate_claim,
)
from zid_platform.modules.credential.domain.evidence_quality import EvidenceKind, explain
from zid_platform.modules.credential.domain.reasons import describe_all
from zid_platform.modules.credential.infrastructure import claim_store as store
from zid_platform.modules.credential.infrastructure import trust_store
from zid_platform.modules.evidence.ports import EvidenceClass, ReceiptFacts, record_decision_receipt
from zid_platform.modules.verification.ports import (
    SANDBOX_ENVIRONMENTS,
    CertRegistry,
    PackRegistry,
    ServicePack,
    resolve,
)


@dataclass(frozen=True, slots=True)
class SourceAnswer:
    """What a source said. `fields` are the values the source itself confirmed."""

    source_id: str
    result: SourceResult
    checked_at: datetime
    fields: Mapping[str, Any]
    evidence: EvidenceTier = EvidenceTier.PRIMARY_SOURCE_CONFIRMED
    authenticity: Authenticity = Authenticity.CONFIRMED
    status: CredentialStatus = CredentialStatus.ACTIVE
    subject: SubjectMatch = SubjectMatch.MATCHED
    obtainable: bool = True
    reference: str | None = None


class ClaimSource(Protocol):
    source_id: str

    async def lookup(
        self,
        claim: ClaimType,
        claimed: Mapping[str, Any],
        jurisdiction: Mapping[str, str],
        now: datetime,
    ) -> SourceAnswer: ...


class SimulatorSource:
    """A deterministic stand-in for a registrar or regulator: local and test only. A claimed institution, regulator,
    employer or issuer that starts with `SIM-` picks the answer, so every outcome can be exercised without a contract."""

    source_id = "simulator"

    def __init__(self, environment: str, source_id: str = "simulator") -> None:
        if environment not in SANDBOX_ENVIRONMENTS:
            raise RuntimeError("the claim-source simulator is for local and test environments only")
        self.source_id = source_id

    def _disciplinary(self, tag: str, base: Mapping[str, Any]) -> SourceAnswer:
        """A regulator's sanctions register: no record unless a tag asks for one."""
        if tag == "SIM-DOWN":
            return SourceAnswer(result=SourceResult.UNAVAILABLE, fields={}, **base)
        if tag in ("SIM-ADVERSE", "SIM-APPEAL", "SIM-SAMENAME", "SIM-NOTFINAL"):
            return SourceAnswer(
                result=SourceResult.RESPONDED,
                fields={
                    "disposition": "suspension",
                    "final": tag != "SIM-NOTFINAL",
                    "appeal_pending": tag == "SIM-APPEAL",
                },
                subject=SubjectMatch.AMBIGUOUS if tag == "SIM-SAMENAME" else SubjectMatch.MATCHED,
                **base,
            )
        return SourceAnswer(result=SourceResult.NO_RECORD, fields={}, **base)

    async def lookup(
        self,
        claim: ClaimType,
        claimed: Mapping[str, Any],
        jurisdiction: Mapping[str, str],
        now: datetime,
    ) -> SourceAnswer:
        tag = next(
            (str(v) for v in claimed.values() if isinstance(v, str) and v.startswith("SIM-")),
            "SIM-OK",
        )
        base: dict[str, Any] = {
            "source_id": self.source_id,
            "checked_at": now,
            "reference": f"sim-{tag}",
        }
        if claim is ClaimType.DISCIPLINARY_FINDING:
            return self._disciplinary(tag, base)
        confirmed: dict[str, Any] = {k: v for k, v in claimed.items() if k != "award_status"}
        if claim is ClaimType.PROFESSIONAL_LICENSE:
            confirmed.setdefault("good_standing", "NOT_GOOD" if tag == "SIM-SUSPENDED" else "GOOD")
        if claim is ClaimType.ACADEMIC_AWARD:
            confirmed["award_status"] = "ATTENDED" if tag == "SIM-ATTENDED" else "CONFERRED"
        if tag == "SIM-DOWN":
            return SourceAnswer(result=SourceResult.UNAVAILABLE, fields={}, **base)
        if tag == "SIM-NONE":
            return SourceAnswer(result=SourceResult.NO_RECORD, fields={}, **base)
        if tag == "SIM-GONE":
            return SourceAnswer(result=SourceResult.NO_RECORD, fields={}, obtainable=False, **base)
        if tag == "SIM-UNTRUSTED":
            return SourceAnswer(
                result=SourceResult.RESPONDED,
                fields=confirmed,
                authenticity=Authenticity.NOT_CONFIRMED,
                **base,
            )
        if tag == "SIM-SUSPENDED":
            return SourceAnswer(
                result=SourceResult.RESPONDED,
                fields=confirmed,
                status=CredentialStatus.SUSPENDED,
                **base,
            )
        if tag == "SIM-OTHERPERSON":
            return SourceAnswer(
                result=SourceResult.RESPONDED,
                fields=confirmed,
                subject=SubjectMatch.NOT_MATCHED,
                **base,
            )
        if tag == "SIM-SAMENAME":
            return SourceAnswer(
                result=SourceResult.RESPONDED,
                fields=confirmed,
                subject=SubjectMatch.AMBIGUOUS,
                **base,
            )
        return SourceAnswer(result=SourceResult.RESPONDED, fields=confirmed, **base)


def claimed_digest(claim: ClaimType, fields: Mapping[str, Any]) -> bytes:
    """A commitment to what was claimed. The values themselves are not stored."""
    raw = json.dumps(
        {"t": claim.value, "f": fields}, sort_keys=True, separators=(",", ":"), default=str
    )
    return hashlib.sha256(raw.encode()).digest()


@dataclass(frozen=True, slots=True)
class Gate:
    """The extra, opt-in controls of a deployment: the issuer registry, the source contracts and the standing parameters.
    Without a gate a check still needs a pack and a source, but no registry or contract is consulted."""

    registry: tg.Registry | None = None
    contracts: Sequence[src.SourceContract] | None = None
    purpose: str = "PROFESSIONAL_ENGAGEMENT"
    processing_region: str = "local"
    subject_region: str | None = None
    consent_ref: str | None = None
    legal_basis_ref: str | None = None
    authorized_user: bool = False
    reviewer_assigned: bool = False
    cpd_required_credits: float | None = None
    cpd_approved_providers: frozenset[str] = frozenset()
    # the fields an employer permits to be disclosed; None means no limit is configured, so nothing about the job is kept
    employment_permitted_fields: frozenset[str] | None = None
    # when given, a pack that names a regulatory scheme is unavailable unless some entity holds it in force (DTV-07)
    certifications: CertRegistry | None = None
    # who asked, for which consumer, and how to sign the finished assertion (section 08.1); the API always supplies a signer
    actor: str | None = None
    consumer: str = "direct"
    signer: ac.Signer | None = None
    # retry, rate limit and circuit breaker for source calls (GID-060); None means one plain attempt. Share ONE instance per process.
    resilience: rs.Resilience | None = None
    freshness_days: int = 365
    recheck_lead_days: int = 30


async def _finalize(
    conn: AsyncConnection,
    *,
    tenant_id: str,
    subject: str,
    gate: Gate,
    claim_value: str,
    assertion: Assertion,
    extras: Mapping[str, Any],
    answer: SourceAnswer,
    pack: ServicePack,
    jurisdiction: Mapping[str, str],
    digest: bytes,
    now: datetime,
) -> tuple[str, dict[str, Any]]:
    """Give the check its id, write its signed evidence receipt in this transaction, complete it into the section 08.1 contract and
    sign it. The receipt commits to the inputs (a digest of the claim, never its values) and the outcome; it is what an auditor opens."""
    check_id = str(uuid.uuid4())
    decision = assertion.decision.value
    reasons = tuple(assertion.reason_codes) or (
        ("CLAIM_VERIFIED",) if decision == "VERIFIED" else ("NO_REASON_RECORDED",)
    )
    actor = gate.actor or subject
    receipt_id = await record_decision_receipt(
        conn,
        tenant_id,
        ReceiptFacts(
            decision_id=check_id,
            decision_type="CREDENTIAL",
            evidence_class=EvidenceClass.E2,
            action="credential.claim.verify",
            resource_class="credential-claim",
            outcome=decision,
            reason_codes=reasons,
            risk_class="R2",
            effective_at=now,
            actor_principal_id=actor,
            represented_principal_id=subject if subject != actor else None,
            resource_ref=f"credential-claim:{claim_value}",
            assurance_summary={
                "tier": assertion.assurance_tier.name,
                "source_result": answer.result.value,
            },
        ),
        floor=EvidenceClass.E2,
        material={
            "claim_type": claim_value,
            "claimed_digest": digest.hex(),
            "jurisdiction": dict(jurisdiction),
            "pack": f"{pack.pack_id}@{pack.version}",
            "source": answer.source_id,
            "source_result": answer.result.value,
            "tier": assertion.assurance_tier.name,
            "decision": decision,
        },
    )
    body = ac.complete(
        {**assertion.to_contract(), **extras},
        check_id=check_id,
        subject=subject,
        consumer=gate.consumer,
        consent_ref=gate.consent_ref,
        receipts=[receipt_id] if receipt_id else [],
        pack_id=pack.pack_id,
        pack_version=pack.version,
        source_result=answer.result.value,
        source_id=answer.source_id,
    )
    return check_id, ac.seal(body, gate.signer, now)


async def _ask(
    gate: Gate,
    source: ClaimSource,
    claim: ClaimType,
    asked: Mapping[str, Any],
    jurisdiction: Mapping[str, str],
    now: datetime,
) -> tuple[SourceAnswer, dict[str, Any] | None]:
    """Ask the source, with the deployment's resilience policy when there is one. A source that cannot be asked is UNAVAILABLE
    (never a verdict on the person), and the reason is a code and an attempt count, never the source's own error text."""
    sid = getattr(source, "source_id", "unknown")

    def unavailable(reference: str | None) -> SourceAnswer:
        return SourceAnswer(
            source_id=sid,
            result=SourceResult.UNAVAILABLE,
            checked_at=now,
            fields={},
            reference=reference,
        )

    if gate.resilience is None:
        try:
            return await source.lookup(claim, asked, jurisdiction, now), None
        except Exception:  # noqa: BLE001 - any source fault is "could not ask"
            return unavailable(None), {"fault": rs.Fault.PERMANENT.value, "attempts": 1}
    outcome = await gate.resilience.call(
        sid, lambda: source.lookup(claim, asked, jurisdiction, now)
    )
    if outcome.ok and outcome.value is not None:
        return outcome.value, None
    return unavailable(outcome.fault.value if outcome.fault else None), rs.describe(outcome)


@dataclass(frozen=True, slots=True)
class CheckResult:
    check_id: str
    pack: ServicePack
    assertion: Assertion
    extras: Mapping[str, Any]

    def to_contract(self) -> dict[str, Any]:
        """The complete, signed section 08.1 contract (what is stored and what a reader receives)."""
        return dict(self.extras)


ISSUER_NAME_FIELDS = ("institution", "regulator", "issuer", "insurer")
KINDS_BY_TIER = {
    EvidenceTier.SELF_DECLARED: [EvidenceKind.HOLDER_STATEMENT],
    EvidenceTier.DOCUMENT_INTEGRITY: [EvidenceKind.SIGNATURE_VALID],
    EvidenceTier.ISSUER_AUTHENTICATED: [EvidenceKind.SIGNATURE_FROM_REGISTERED_ISSUER],
    EvidenceTier.PRIMARY_SOURCE_CONFIRMED: [EvidenceKind.PRIMARY_SOURCE_RESPONSE],
}


def _issuer_name(claimed: Mapping[str, Any]) -> str | None:
    return next((str(claimed[k]) for k in ISSUER_NAME_FIELDS if claimed.get(k)), None)


def _listify(value: Any) -> list[str]:
    if value is None:
        return []
    return (
        [str(v) for v in value]
        if isinstance(value, list | tuple | set | frozenset)
        else [str(value)]
    )


def _expiry(claim: ClaimType, fields: Mapping[str, Any]) -> datetime | None:
    key = {
        ClaimType.PROFESSIONAL_LICENSE: "effective_until",
        ClaimType.INDUSTRY_CERTIFICATE: "renewal_due",
        ClaimType.INSURANCE_ASSERTION: "expires_on",
    }.get(claim)
    raw = fields.get(key) if key else None
    if not raw:
        return None
    try:
        return datetime.fromisoformat(str(raw)[:10]).replace(tzinfo=UTC)
    except ValueError:
        return None


def _extras(
    claim: ClaimType,
    answer: SourceAnswer,
    standing_restrictions: Sequence[str],
    issuer_ref: str | None,
    entity_is_subject: bool | None,
    accreditation: str | None,
) -> dict[str, Any]:
    """Facts later decisions read, derived from what the SOURCE confirmed (never the holder's words)."""
    out: dict[str, Any] = {"issuer_ref": issuer_ref}
    f = answer.fields
    if claim is ClaimType.PROFESSIONAL_LICENSE:
        out["permitted_services"] = _listify(f.get("activity_scope"))
        out["regulator"] = f.get(
            "regulator"
        )  # what the regulator's own answer says, not the holder's claim
        out["restrictions"] = list(standing_restrictions)
    if claim is ClaimType.INSURANCE_ASSERTION:
        out["insurance_facts"] = {
            "entity_is_subject": entity_is_subject,
            "region": _listify(f.get("region")),
            "expires_on": f.get("expires_on"),
            "activities": _listify(f.get("activities")),
            "excluded_work": _listify(f.get("excluded_work")),
            # what a practice rule (CR-076) reads; absent from the source means absent here, never filled in
            "coverage_limit": f.get("coverage_limit"),
            "currency": f.get("currency"),
            "practice_class": _listify(f.get("practice_class")),
            "cover_basis": f.get("cover_basis"),
            "retroactive_date": f.get("retroactive_date"),
        }
    if accreditation is not None:
        out["accreditation"] = accreditation
    return out


async def run_check(
    conn: AsyncConnection,
    *,
    tenant_id: str,
    subject_principal_id: str,
    kind: str,
    claimed: Mapping[str, Any],
    jurisdiction: Mapping[str, str],
    profession: str | None,
    packs: PackRegistry,
    source: ClaimSource,
    now: datetime,
    environment: str,
    gate: Gate | None = None,
    holder: Holder | None = None,
) -> CheckResult:
    claim = validate_claim(kind, claimed)
    service = SERVICE_CODES.get(claim)
    if service is None:  # DISCIPLINARY_FINDING has its own gated path, not a holder-initiated check
        raise ClaimError("CLAIM_CLASS_NOT_CHECKABLE", claim.value)
    gate = gate or Gate()
    pack = resolve(
        packs,
        country=jurisdiction.get("country", ""),
        service_code=service,
        profession=profession,
        subdivision=jurisdiction.get("subdivision"),
        now=now,
        environment=environment,
        certifications=gate.certifications,
    )
    issuer_ref: str | None = None
    accreditation: str | None = None
    stop: tuple[Decision, tuple[str, ...], Authenticity] | None = None
    name = _issuer_name(claimed)
    if gate.registry is not None and name:
        res = tg.resolve(gate.registry, name, country=jurisdiction.get("country"), on=now.date())
        if res.match is tg.Match.LOOKALIKE:
            stop = (
                Decision.REVIEW,
                ("ISSUER_LOOKALIKE",),
                Authenticity.UNKNOWN,
            )  # a person decides
        elif res.match is tg.Match.UNKNOWN:
            stop = (Decision.NOT_VERIFIED, ("ISSUER_NOT_TRUSTED",), Authenticity.NOT_CONFIRMED)
        else:
            issuer_ref = res.issuer_id
            try:
                tg.trusted_for(
                    gate.registry,
                    issuer_ref,
                    claim_type=claim.value,
                    country=jurisdiction.get("country"),
                    on=now.date(),
                )
            except tg.TrustGraphError:
                stop = (Decision.NOT_VERIFIED, ("ISSUER_NOT_TRUSTED",), Authenticity.NOT_CONFIRMED)
            else:
                award_day = claimed.get("conferral_date")
                if claim is ClaimType.ACADEMIC_AWARD and award_day and issuer_ref:
                    try:
                        when = date.fromisoformat(str(award_day)[:10])
                        state, _ = tg.accreditation_at_award(
                            gate.registry.issuers[issuer_ref], str(claimed["program"]), when
                        )
                        accreditation = state.value
                    except ValueError:
                        accreditation = None
    answer: SourceAnswer
    employment_disclosed: dict[str, Any] | None = None
    source_fault: dict[str, Any] | None = None
    if stop is not None:
        decision, reasons, authenticity = stop
        assertion = unqueried_assertion(
            claim, decision, reasons, authenticity=authenticity, jurisdiction=jurisdiction
        )
        answer = SourceAnswer(
            source_id="not-queried",
            result=SourceResult.NOT_QUERIED,
            checked_at=now,
            fields={},
            evidence=EvidenceTier.SELF_DECLARED,
        )
        standing_restrictions: list[str] = []
        entity_is_subject: bool | None = None
    else:
        if gate.contracts is not None:
            mine = [c for c in gate.contracts if c.source_id == getattr(source, "source_id", "")]
            req = src.Request(
                claim_type=claim.value,
                country=jurisdiction.get("country", ""),
                purpose=gate.purpose,
                processing_region=gate.processing_region,
                subject_region=gate.subject_region,
                on=now.date(),
                consent_ref=gate.consent_ref,
                legal_basis_ref=gate.legal_basis_ref,
                authorized_user=gate.authorized_user,
                reviewer_assigned=gate.reviewer_assigned,
            )
            chosen, refused = src.choose(mine, req)
            if chosen is None:  # never queried: nothing is asked, cached or reused
                raise src.SourceError(next(iter(refused.values()), "SOURCE_NO_CONTRACT"))
        answer, source_fault = await _ask(gate, source, claim, claimed, jurisdiction, now)
        status = answer.status
        standing_restrictions = []
        if (
            claim is ClaimType.EMPLOYMENT_ASSERTION
            and answer.result is SourceResult.RESPONDED
            and gate.employment_permitted_fields is not None
        ):
            # only what the employer permits is kept: the rest is dropped before anything is stored or returned
            employment_disclosed = standing.employment_disclosure(
                answer.fields, permitted_fields=gate.employment_permitted_fields
            )
            answer = replace(answer, fields=employment_disclosed)
        if answer.result is SourceResult.RESPONDED:
            if claim is ClaimType.PROFESSIONAL_LICENSE:
                s = standing.license_standing(answer.fields, on=now.date())
                standing_restrictions = list(s.restrictions)
                if s.status is not CredentialStatus.ACTIVE or status is CredentialStatus.ACTIVE:
                    status = (
                        s.status
                    )  # the dates and standing the regulator reported can only make it worse
            elif claim is ClaimType.INDUSTRY_CERTIFICATE:
                s = standing.certificate_status(answer.fields, on=now.date())
                if s.status is not CredentialStatus.ACTIVE:
                    status = s.status
        subject = answer.subject
        source_name = (
            answer.fields.get("subject_name") if answer.result is SourceResult.RESPONDED else None
        )
        if holder is not None and source_name:
            subject, _ = match_holder(
                holder, str(source_name), source_birth_year=answer.fields.get("birth_year")
            )
        entity_is_subject = None
        if claim is ClaimType.INSURANCE_ASSERTION and holder is not None:
            entity_is_subject = " ".join(
                str(answer.fields.get("insured_entity", "")).casefold().split()
            ) == " ".join(holder.legal_name.casefold().split())
        assertion = assertion_from_facts(
            claim,
            source_fields=answer.fields,
            evidence=answer.evidence,
            source=answer.result,
            authenticity=answer.authenticity,
            status=status,
            subject=subject,
            source_obtainable=answer.obtainable,
            jurisdiction=dict(jurisdiction),
        )
        if claim is ClaimType.CPD_RECORD and assertion.decision is Decision.VERIFIED:
            required = gate.cpd_required_credits
            result, reasons = (
                standing.cpd_standing(
                    answer.fields,
                    required_credits=required,
                    approved_providers=gate.cpd_approved_providers,
                    evidence=answer.evidence,
                )
                if required is not None
                else (standing.CpdResult.INDETERMINATE, ("CPD_REQUIREMENT_NOT_CONFIGURED",))
            )
            if result is standing.CpdResult.INDETERMINATE:
                assertion = replace(
                    assertion, decision=Decision.INDETERMINATE, reason_codes=reasons
                )
    expires = _expiry(claim, answer.fields)
    kinds = KINDS_BY_TIER[assertion.assurance_tier]
    explanation = explain(
        claim,
        assertion.assurance_tier,
        kinds,
        decision=assertion.decision.value,
        checked_at=answer.checked_at,
    )
    extras = _extras(
        claim, answer, standing_restrictions, issuer_ref, entity_is_subject, accreditation
    )
    if employment_disclosed is not None:
        extras["employment"] = employment_disclosed
    extras["expires_at"] = expires.isoformat() if expires else None
    extras["source_checked_at"] = answer.checked_at.isoformat()
    extras["explanation"] = explanation.as_dict()
    extras["reason_text"] = describe_all(list(assertion.reason_codes))  # words, not just codes
    if source_fault:
        extras["source_fault"] = (
            source_fault  # a code and a count: why the source could not be asked
        )
    digest = claimed_digest(claim, claimed)
    check_id, final = await _finalize(
        conn,
        tenant_id=tenant_id,
        subject=subject_principal_id,
        gate=gate,
        claim_value=claim.value,
        assertion=assertion,
        extras=extras,
        answer=answer,
        pack=pack,
        jurisdiction=jurisdiction,
        digest=digest,
        now=now,
    )
    await store.insert_check(
        conn,
        check_id=check_id,
        tenant_id=tenant_id,
        subject_principal_id=subject_principal_id,
        claim_type=claim.value,
        decision=assertion.decision.value,
        assurance_tier=assertion.assurance_tier.name,
        pack_id=pack.pack_id,
        pack_version=pack.version,
        jurisdiction=dict(jurisdiction),
        claimed_digest=digest,
        source_id=answer.source_id,
        source_reference=answer.reference,
        source_result=answer.result.value,
        source_checked_at=answer.checked_at,
        assertion=final,
    )
    fresh = now + timedelta(days=gate.freshness_days)
    next_check = min(fresh, expires - timedelta(days=gate.recheck_lead_days)) if expires else fresh
    await trust_store.insert_monitor(
        conn,
        check_id=check_id,
        tenant_id=tenant_id,
        claim_type=claim.value,
        status=assertion.credential_status.value,
        expires_at=expires,
        next_check_at=max(next_check, now),
    )
    await emit(
        conn,
        name="credential.claim.checked",
        tenant_id=tenant_id,
        aggregate_type="claim_check",
        aggregate_id=check_id,
        aggregate_version=1,
        data={
            "check_id": check_id,
            "claim_type": claim.value,
            "decision": assertion.decision.value,
        },
    )
    return CheckResult(check_id, pack, assertion, final)


DISCIPLINARY_SERVICE = "disciplinary.verify"
_DISCIPLINARY_DECISION = {
    standing.Disciplinary.ADVERSE_FINDING: Decision.VERIFIED,  # the FINDING is verified; it is the only adverse result
    standing.Disciplinary.ADVERSE_UNDER_APPEAL: Decision.REVIEW,
    standing.Disciplinary.REVIEW: Decision.REVIEW,
    standing.Disciplinary.NO_ADVERSE_FINDING: Decision.NOT_VERIFIED,  # no finding at this source; nothing more is claimed
    standing.Disciplinary.NO_MATCH: Decision.NOT_VERIFIED,
}


async def run_disciplinary_check(
    conn: AsyncConnection,
    *,
    tenant_id: str,
    subject_principal_id: str,
    regulator: str,
    reference: str | None,
    jurisdiction: Mapping[str, str],
    packs: PackRegistry,
    source: ClaimSource,
    now: datetime,
    environment: str,
    gate: Gate,
    holder: Holder | None = None,
) -> CheckResult:
    """Ask a regulator whether it holds an adverse finding about the subject (GID-046, GID-057).

    Always sensitive: it needs a service pack, a registered regulator trusted for findings, and a source contract whose
    sensitive-category conditions are met (a consent or legal basis, an authorized user, an assigned reviewer). A name-only
    match is a REVIEW, a finding under appeal says so, and no record at this source is not a clearance of anything else.
    """
    if gate.contracts is None or gate.registry is None:
        raise src.SourceError(
            "SOURCE_NO_CONTRACT"
        )  # a sensitive source is never asked without its contract and registry
    pack = resolve(
        packs,
        country=jurisdiction.get("country", ""),
        service_code=DISCIPLINARY_SERVICE,
        profession=None,
        subdivision=jurisdiction.get("subdivision"),
        now=now,
        environment=environment,
        certifications=gate.certifications,
    )
    res = tg.resolve(gate.registry, regulator, country=jurisdiction.get("country"), on=now.date())
    try:
        if res.match is not tg.Match.RESOLVED:
            raise tg.TrustGraphError("ISSUER_NOT_TRUSTED", "regulator")
        tg.trusted_for(
            gate.registry,
            res.issuer_id,
            claim_type=ClaimType.DISCIPLINARY_FINDING.value,
            country=jurisdiction.get("country"),
            on=now.date(),
        )
    except tg.TrustGraphError as e:
        raise ClaimError("REGULATOR_NOT_TRUSTED", regulator[:64]) from e
    req = src.Request(
        claim_type=ClaimType.DISCIPLINARY_FINDING.value,
        country=jurisdiction.get("country", ""),
        purpose=gate.purpose,
        processing_region=gate.processing_region,
        subject_region=gate.subject_region,
        on=now.date(),
        consent_ref=gate.consent_ref,
        legal_basis_ref=gate.legal_basis_ref,
        authorized_user=gate.authorized_user,
        reviewer_assigned=gate.reviewer_assigned,
    )
    mine = [c for c in gate.contracts if c.source_id == getattr(source, "source_id", "")]
    chosen, refused = src.choose(mine, req)
    if chosen is None:
        raise src.SourceError(next(iter(refused.values()), "SOURCE_NO_CONTRACT"))
    asked: dict[str, Any] = {"regulator": regulator}
    if reference:
        asked["reference"] = reference
    answer, source_fault = await _ask(
        gate, source, ClaimType.DISCIPLINARY_FINDING, asked, jurisdiction, now
    )
    subject = answer.subject
    name = answer.fields.get("subject_name") if answer.result is SourceResult.RESPONDED else None
    if holder is not None and name:
        subject, _ = match_holder(
            holder, str(name), source_birth_year=answer.fields.get("birth_year")
        )
    extras: dict[str, Any] = {"issuer_ref": res.issuer_id, "expires_at": None}
    if answer.result is SourceResult.UNAVAILABLE:
        assertion = assertion_from_facts(
            ClaimType.DISCIPLINARY_FINDING,
            source_fields={},
            evidence=answer.evidence,
            source=SourceResult.UNAVAILABLE,
            authenticity=answer.authenticity,
            status=CredentialStatus.UNKNOWN,
            subject=subject,
            jurisdiction=dict(jurisdiction),
        )
        extras["disciplinary"] = "SOURCE_UNAVAILABLE"
    else:
        outcome, reasons = standing.disciplinary_result(
            subject=subject,
            evidence=answer.evidence,
            record_found=answer.result is SourceResult.RESPONDED,
            final=bool(answer.fields.get("final")),
            appeal_pending=bool(answer.fields.get("appeal_pending")),
        )
        adverse = outcome is standing.Disciplinary.ADVERSE_FINDING
        assertion = Assertion(
            claim_type=ClaimType.DISCIPLINARY_FINDING,
            decision=_DISCIPLINARY_DECISION[outcome],
            credential_status=CredentialStatus.ACTIVE if adverse else CredentialStatus.UNKNOWN,
            issuer_authenticity=answer.authenticity,
            primary_source_verified=adverse,
            professional_eligibility=Eligibility.NOT_EVALUATED,
            current_consumer_entitlement=Eligibility.NOT_EVALUATED,
            assurance_tier=answer.evidence,
            subject_match=subject,
            reason_codes=reasons,
            jurisdiction=dict(jurisdiction),
        )
        extras["disciplinary"] = outcome.value
    extras["source_checked_at"] = answer.checked_at.isoformat()
    extras["explanation"] = explain(
        ClaimType.DISCIPLINARY_FINDING,
        assertion.assurance_tier,
        KINDS_BY_TIER[assertion.assurance_tier],
        decision=assertion.decision.value,
        checked_at=answer.checked_at,
    ).as_dict()
    extras["reason_text"] = describe_all(list(assertion.reason_codes))
    if source_fault:
        extras["source_fault"] = source_fault
    digest = claimed_digest(ClaimType.DISCIPLINARY_FINDING, {"regulator": regulator})
    check_id, final = await _finalize(
        conn,
        tenant_id=tenant_id,
        subject=subject_principal_id,
        gate=gate,
        claim_value=ClaimType.DISCIPLINARY_FINDING.value,
        assertion=assertion,
        extras=extras,
        answer=answer,
        pack=pack,
        jurisdiction=jurisdiction,
        digest=digest,
        now=now,
    )
    await store.insert_check(
        conn,
        check_id=check_id,
        tenant_id=tenant_id,
        subject_principal_id=subject_principal_id,
        claim_type=ClaimType.DISCIPLINARY_FINDING.value,
        decision=assertion.decision.value,
        assurance_tier=assertion.assurance_tier.name,
        pack_id=pack.pack_id,
        pack_version=pack.version,
        jurisdiction=dict(jurisdiction),
        claimed_digest=digest,
        source_id=answer.source_id,
        source_reference=answer.reference,
        source_result=answer.result.value,
        source_checked_at=answer.checked_at,
        assertion=final,
    )
    await trust_store.insert_monitor(
        conn,
        check_id=check_id,
        tenant_id=tenant_id,
        claim_type=ClaimType.DISCIPLINARY_FINDING.value,
        status=assertion.credential_status.value,
        expires_at=None,
        next_check_at=now
        + timedelta(days=min(gate.freshness_days, 90)),  # adverse sources go stale sooner
    )
    await emit(
        conn,
        name="credential.claim.checked",
        tenant_id=tenant_id,
        aggregate_type="claim_check",
        aggregate_id=check_id,
        aggregate_version=1,
        data={
            "check_id": check_id,
            "claim_type": ClaimType.DISCIPLINARY_FINDING.value,
            "decision": assertion.decision.value,
        },
    )
    return CheckResult(check_id, pack, assertion, final)
