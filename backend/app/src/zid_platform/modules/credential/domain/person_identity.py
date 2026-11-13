"""Who a person is, from a credential a wallet or eID scheme issued (Document 03 section 06 PERSON_IDENTITY; Document 01 section 01.1). Pure.

This is the vendor-free route to the PERSON_IDENTITY dimension: an issuer the deployment trusts FOR identity credentials vouched for a person's name and
birth date, and the person's wallet proved possession of the key the credential was issued to. It decides from facts the existing credential verifier
already established; it adds no cryptography. The rules fail closed:

- NO issuer is trusted for everything: the issuer must be in the typed trust table for this exact credential type, and the type must be one the owners
  accept as evidence of identity at all (`config/identity-credentials`);
- a copied credential is not a person: without holder binding the answer is INDETERMINATE, never a pass;
- a status that cannot be read is not acceptance, and a revoked or suspended credential is NOT_VERIFIED;
- the name and birth date are read only from a credential whose proof verified, and only those fields are returned (the relying party asks for more
  through a presentation request, it does not get them here);
- the best answer is VERIFIED_LIMITED and it always says what it does not show: that the signature is an issuer's attestation and not a check against
  the population register (no primary-source confirmation), and that holding the wallet key is not a live person: nobody here saw a face.
"""

from __future__ import annotations

import re
from collections.abc import Mapping
from dataclasses import dataclass, field
from datetime import date

from zid_platform.modules.credential.domain.claims import CredentialStatus

REQUIRED = ("family_name", "given_name", "birth_date")
ALIASES = {
    "family_name": ("family_name", "familyName", "surname"),
    "given_name": ("given_name", "givenName", "firstName", "first_name"),
    "birth_date": ("birth_date", "birthDate", "birthdate"),
}
_ISO_DATE = re.compile(r"^(\d{4})-(\d{2})-(\d{2})$")
LIMITATIONS = (
    "ISSUER_ATTESTATION: an issuer this deployment trusts for identity credentials said so. It is not a check against a population register.",
    "LIVE_PERSON_NOT_ASSESSED: holding the wallet's key is not proof that the person using it is the person named. Nobody here compared a face.",
    "Only the name and birth date are returned. Anything more has to be asked for through a presentation request.",
)


@dataclass(frozen=True, slots=True)
class IdentityPolicy:
    credential_types: frozenset[str] = frozenset()


@dataclass(frozen=True, slots=True)
class PersonCredentialFacts:
    integrity: bool  # the proof verified under the issuer's pinned key
    issuer_trusted_for_type: (
        bool  # the typed trust table vouches for this issuer for this credential type
    )
    holder_bound: bool  # the presenter proved possession of the key the credential was issued to
    status: CredentialStatus
    credential_types: tuple[str, ...]
    claims: Mapping[str, object] | None  # credentialSubject, read only when the proof verified
    issuer_known: bool = (
        True  # in the typed trust table at all; False means the signature could not even be tried
    )
    # why an official register does not vouch for this issuer where one must (an EU PID provider); empty means nothing stands in the way
    register_refusal: tuple[str, ...] = ()


@dataclass(frozen=True, slots=True)
class PersonIdentityOutcome:
    status: str  # VERIFIED_LIMITED | INDETERMINATE | NOT_VERIFIED
    reasons: tuple[str, ...]
    identity: Mapping[str, str] = field(default_factory=dict)

    @property
    def person_identity_established(self) -> bool:
        return self.status == "VERIFIED_LIMITED"

    def public(self) -> dict[str, object]:
        return {
            "dimension": "PERSON_IDENTITY",
            "status": self.status,
            "reasons": list(self.reasons),
            "person_identity_established": self.person_identity_established,
            "holder_bound": "HOLDER_NOT_BOUND" not in self.reasons,
            "primary_source_verified": False,
            "assurance_ceiling": "ISSUER_AUTHENTICATED"
            if self.person_identity_established
            else "SELF_DECLARED",
            "identity": dict(self.identity),
            "limitations": list(LIMITATIONS),
        }


def _text(claims: Mapping[str, object], name: str) -> str | None:
    for key in ALIASES[name]:
        value = claims.get(key)
        if isinstance(value, str) and value.strip():
            return value.strip()
    return None


def _valid_birth_date(value: str, today: date) -> bool:
    m = _ISO_DATE.match(value)
    if not m:
        return False
    try:
        d = date(int(m.group(1)), int(m.group(2)), int(m.group(3)))
    except ValueError:
        return False
    return date(1900, 1, 1) <= d <= today


def decide(
    facts: PersonCredentialFacts, policy: IdentityPolicy, *, today: date
) -> PersonIdentityOutcome:
    """The PERSON_IDENTITY dimension from the facts of one presented credential."""
    if not facts.integrity:
        if not facts.issuer_known:
            # no pinned key to try: this is "cannot verify", not "forged"
            return PersonIdentityOutcome("INDETERMINATE", ("ISSUER_NOT_TRUSTED_FOR_IDENTITY",))
        return PersonIdentityOutcome("NOT_VERIFIED", ("CREDENTIAL_SIGNATURE_NOT_VALID",))
    if facts.status in (CredentialStatus.REVOKED, CredentialStatus.SUSPENDED):
        return PersonIdentityOutcome("NOT_VERIFIED", (f"CREDENTIAL_{facts.status.value}",))
    if not (set(facts.credential_types) & policy.credential_types):
        return PersonIdentityOutcome(
            "INDETERMINATE", ("CREDENTIAL_TYPE_NOT_ACCEPTED_AS_EVIDENCE_OF_IDENTITY",)
        )
    if not facts.issuer_trusted_for_type:
        return PersonIdentityOutcome("INDETERMINATE", ("ISSUER_NOT_TRUSTED_FOR_IDENTITY",))
    if facts.register_refusal:
        return PersonIdentityOutcome("INDETERMINATE", facts.register_refusal)
    if facts.status is not CredentialStatus.ACTIVE:
        return PersonIdentityOutcome("INDETERMINATE", ("CREDENTIAL_STATUS_NOT_CONFIRMED",))
    if not facts.holder_bound:
        return PersonIdentityOutcome("INDETERMINATE", ("HOLDER_NOT_BOUND",))
    claims = facts.claims or {}
    found = {name: _text(claims, name) for name in REQUIRED}
    missing = [n for n, v in found.items() if v is None]
    if missing:
        return PersonIdentityOutcome(
            "INDETERMINATE", tuple(f"IDENTITY_CLAIM_MISSING_{n.upper()}" for n in missing)
        )
    birth = found["birth_date"] or ""
    if not _valid_birth_date(birth, today):
        return PersonIdentityOutcome("INDETERMINATE", ("BIRTH_DATE_NOT_A_VALID_DATE",))
    return PersonIdentityOutcome(
        "VERIFIED_LIMITED",
        (
            "IDENTITY_FROM_HOLDER_BOUND_CREDENTIAL",
            "ISSUER_AUTHENTICATED_NOT_PRIMARY_SOURCE",
            "LIVE_PERSON_NOT_ASSESSED",
        ),
        {k: v for k, v in found.items() if v is not None},
    )
