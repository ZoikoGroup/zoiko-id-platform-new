"""EUDI Wallet checks in the OID4VP verifier (ZID-RCG-003 RCG-G11; CR-080).

    check_create          before a request to an EUDI Wallet is made: Zoiko's registration in that Member State, the intended use, the attributes,
                          and the access certificate the verifier signs with
    check_request_object  after the request object is signed: the interoperability profile
    classify_presented    what a presented attestation is (PID, QEAA, PuB-EAA, EAA), with QEAA only when an official-register attestation shows the
                          issuer qualified on the issuance date

Applies only to EUDI attestation types (a PID or an `eu.europa.ec.eudi.` / `urn:eudi:` type); every other request is untouched. Switch
`ZID_EUDI_WALLET` (configured at start-up): shadow (default) reports, enforce refuses, off skips.
"""

from __future__ import annotations

import base64
import json
import logging
from collections.abc import Iterable
from datetime import datetime
from typing import Any

from sqlalchemy.ext.asyncio import AsyncConnection

from zid_platform.modules.credential.domain import eudi
from zid_platform.modules.credential.domain.credentials import CredentialError
from zid_platform.modules.credential.infrastructure.eudi_registrations import eudi_registrations

_LOG = logging.getLogger("zid.regulatory")
_MODE = "shadow"
QEAA_SCHEME = "EU_TRUSTED_LIST"  # the official-register scheme an issuer's qualified status is attested under (CR-073)


def configure(mode: str) -> None:
    global _MODE
    if mode not in ("off", "shadow", "enforce"):
        raise ValueError("ZID_EUDI_WALLET is off, shadow or enforce")
    _MODE = mode


def mode() -> str:
    return _MODE


def _refuse(what: str, reasons: list[str], detail: str) -> None:
    _LOG.warning(
        "eudi wallet %s %s: %s (%s)",
        what,
        "refused" if _MODE == "enforce" else "would refuse (shadow)",
        ",".join(reasons),
        detail,
    )
    if _MODE == "enforce":
        raise CredentialError(reasons[0], detail)


def check_create(
    *,
    credential_type: str,
    member_state: str,
    intended_use: str,
    claims: Iterable[str],
    client_id: str,
    at: datetime,
    registrations: Iterable[eudi.Registration] | None = None,
) -> None:
    if _MODE == "off" or not eudi.is_eudi_type(credential_type):
        return
    reasons = eudi.check_request(
        eudi_registrations() if registrations is None else registrations,
        member_state=member_state,
        intended_use=intended_use,
        attestation_type=credential_type,
        claims=list(claims),
        client_id=client_id,
        at=at,
    )
    if reasons:
        _refuse("request", reasons, f"{member_state}/{intended_use}/{credential_type}")


def _part(segment: str) -> dict[str, Any]:
    return json.loads(base64.urlsafe_b64decode(segment + "=" * (-len(segment) % 4)))  # type: ignore[no-any-return]


def check_request_object(credential_type: str, request_jwt: str, *, now_epoch: int) -> None:
    if _MODE == "off" or not eudi.is_eudi_type(credential_type):
        return
    header_b64, payload_b64, _ = request_jwt.split(".")
    reasons = eudi.conformance(_part(header_b64), _part(payload_b64), now_epoch=now_epoch)
    if reasons:
        _refuse("request object", ["WALLET_REQUEST_NOT_CONFORMANT", *reasons], credential_type)


async def classify_presented(
    conn: AsyncConnection,
    *,
    attestation_type: str,
    issuer_entity: str,
    issuer_country: str,
    issued_at: datetime,
    issuer_is_public_body: bool = False,
) -> tuple[eudi.AttestationClass, tuple[str, ...]]:
    """The class of a presented attestation. Qualified status (QEAA) and a PID provider's listing both come from official-register attestations
    (CR-073), judged at the issuance date: a later withdrawal never rewrites what an attestation was when it was issued."""
    from zid_platform.modules.verification.ports import attestation_model, attestations

    service = "PID" if eudi.PID_TYPE.match(attestation_type) else "QEAA"
    decision = await attestations.decide_scope(
        conn,
        attestation_model.Scope(issuer_entity, QEAA_SCHEME, issuer_country.upper(), service),
        at=issued_at,
    )
    if service == "PID":
        return eudi.classify(
            attestation_type,
            issuer_is_public_body=issuer_is_public_body,
            qualified_at_issuance=None,
            pid_provider_listed=decision.certified,
        )
    return eudi.classify(
        attestation_type,
        issuer_is_public_body=issuer_is_public_body,
        qualified_at_issuance=decision.certified,
    )
