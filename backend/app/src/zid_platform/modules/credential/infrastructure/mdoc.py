"""ISO/IEC 18013-5 mdoc DeviceResponse verification for OID4VP (ZID-VC-001 §11; HAIP 1.0).

Adapter MDOC_ADAPTER (iso18013-5:2021 + OID4VP 1.0 Appendix B handover):
- issuerAuth: COSE_Sign1 (ES256) by the document signer certificate in x5chain, which must be
  issued directly by a pinned IACA root and be inside its validity period;
- MobileSecurityObject: exact docType, validity window, SHA-256 value digests - every disclosed
  IssuerSignedItem must hash to its digest; nothing undisclosed is ever visible;
- deviceAuth: COSE_Sign1 by the MSO's device key over DeviceAuthentication with the OID4VP
  session transcript (client_id, nonce, response_uri) - binds the response to this transaction.

Status (Token Status List, CWT form): the MSO's status.status_list {idx, uri} points at a
COSE_Sign1 status list token typed application/statuslist+cwt, signed by a certificate in its
x5chain issued by the same pinned IACA roots; sub must equal the uri and exp must not have passed.
Anything else is UNKNOWN, never VALID.
"""

from __future__ import annotations

import hashlib
from collections.abc import Mapping, Sequence
from datetime import UTC, datetime
from typing import Any

import cbor2
from cryptography import x509
from cryptography.exceptions import InvalidSignature
from cryptography.hazmat.primitives import hashes
from cryptography.hazmat.primitives.asymmetric import ec
from cryptography.hazmat.primitives.asymmetric.utils import encode_dss_signature

from zid_platform.modules.credential.domain.credentials import CredentialError, b64d

ES256 = -7
STATUS_LIST_CWT_TYP = "application/statuslist+cwt"
CWT_SUB, CWT_EXP, CWT_IAT, CWT_STATUS_LIST = 2, 4, 6, 65533
COSE_TYP = 16


def session_transcript(
    *, client_id: str, nonce: str, response_uri: str, jwk_thumbprint: bytes | None = None
) -> list[Any]:
    """OID4VP 1.0 B.2.6.1: [null, null, ["OpenID4VPHandover", sha256(cbor(handover info))]]."""
    info = [client_id, nonce, jwk_thumbprint, response_uri]
    return [None, None, ["OpenID4VPHandover", hashlib.sha256(cbor2.dumps(info)).digest()]]


def _verify_es256(public_key: ec.EllipticCurvePublicKey, signature: bytes, data: bytes) -> None:
    if len(signature) != 64:
        raise CredentialError("MDOC_SIGNATURE_INVALID", "length")
    der = encode_dss_signature(
        int.from_bytes(signature[:32], "big"), int.from_bytes(signature[32:], "big")
    )
    try:
        public_key.verify(der, data, ec.ECDSA(hashes.SHA256()))
    except InvalidSignature as e:
        raise CredentialError("MDOC_SIGNATURE_INVALID") from e


def _cose_key(key: Mapping[int, Any]) -> ec.EllipticCurvePublicKey:
    if key.get(1) != 2 or key.get(-1) != 1:
        raise CredentialError("MDOC_DEVICE_KEY_UNSUPPORTED")
    x, y = key.get(-2), key.get(-3)
    if not isinstance(x, bytes) or not isinstance(y, bytes):
        raise CredentialError("MDOC_DEVICE_KEY_UNSUPPORTED")
    return ec.EllipticCurvePublicNumbers(
        int.from_bytes(x, "big"), int.from_bytes(y, "big"), ec.SECP256R1()
    ).public_key()


def _sign1(obj: Any) -> tuple[bytes, dict[Any, Any], bytes | None, bytes]:
    if isinstance(obj, cbor2.CBORTag) and obj.tag == 18:
        obj = obj.value
    if not isinstance(obj, list | tuple) or len(obj) != 4:  # cbor2 yields tuples inside tags
        raise CredentialError("MDOC_MALFORMED", "COSE_Sign1")
    protected, unprotected, payload, signature = obj
    if not isinstance(protected, bytes) or not isinstance(signature, bytes):
        raise CredentialError("MDOC_MALFORMED", "COSE_Sign1")
    return protected, dict(unprotected or {}), payload, signature


def _tag24(obj: Any) -> bytes:
    if not isinstance(obj, cbor2.CBORTag) or obj.tag != 24 or not isinstance(obj.value, bytes):
        raise CredentialError("MDOC_MALFORMED", "tag 24")
    return obj.value


def _time(value: Any) -> datetime:
    if isinstance(value, cbor2.CBORTag):
        value = value.value
    if isinstance(value, datetime):
        return value.astimezone(UTC)
    if isinstance(value, str):
        return datetime.fromisoformat(value).astimezone(UTC)
    raise CredentialError("MDOC_MALFORMED", "validity")


def _document_signer(
    protected: Mapping[int, Any],
    unprotected: Mapping[int, Any],
    roots: Sequence[str],
    now: datetime,
) -> ec.EllipticCurvePublicKey:
    chain = unprotected.get(33) or protected.get(33)
    leaf_der = chain[0] if isinstance(chain, list) else chain
    if not isinstance(leaf_der, bytes):
        raise CredentialError("MDOC_ISSUER_UNTRUSTED", "x5chain missing")
    leaf = x509.load_der_x509_certificate(leaf_der)
    if not leaf.not_valid_before_utc <= now <= leaf.not_valid_after_utc:
        raise CredentialError("MDOC_ISSUER_UNTRUSTED", "document signer certificate expired")
    for pem in roots:
        root = x509.load_pem_x509_certificate(pem.encode())
        try:
            leaf.verify_directly_issued_by(root)
        except (ValueError, TypeError, InvalidSignature):
            continue
        if not root.not_valid_before_utc <= now <= root.not_valid_after_utc:
            continue
        key = leaf.public_key()
        if not isinstance(key, ec.EllipticCurvePublicKey):
            raise CredentialError("MDOC_ISSUER_UNTRUSTED", "document signer key type")
        return key
    raise CredentialError("MDOC_ISSUER_UNTRUSTED", "not issued by a pinned IACA root")


def status_reference(mso: Mapping[str, Any]) -> tuple[int, str] | None:
    ref = (mso.get("status") or {}).get("status_list") or {}
    idx, uri = ref.get("idx"), ref.get("uri")
    if isinstance(idx, int) and not isinstance(idx, bool) and isinstance(uri, str):
        return idx, uri
    return None


def verify_status_list_cwt(
    data: bytes, *, uri: str, iaca_roots: Sequence[str], now: datetime
) -> tuple[int, bytes]:
    """(bits, deflated list) from a status list CWT signed under a pinned IACA root."""
    try:
        token = cbor2.loads(data)
    except (ValueError, cbor2.CBORDecodeError) as e:
        raise CredentialError("STATUS_LIST_INVALID") from e
    if isinstance(token, cbor2.CBORTag) and token.tag == 61:  # optional CWT tag
        token = token.value
    protected_b, unprotected, payload, signature = _sign1(token)
    protected = cbor2.loads(protected_b) if protected_b else {}
    if protected.get(1) != ES256:
        raise CredentialError("STATUS_LIST_INVALID", "alg")
    if protected.get(COSE_TYP) != STATUS_LIST_CWT_TYP:
        raise CredentialError("STATUS_LIST_INVALID", "typ")
    signer = _document_signer(protected, unprotected, iaca_roots, now)
    if not isinstance(payload, bytes):
        raise CredentialError("STATUS_LIST_INVALID", "payload")
    _verify_es256(signer, signature, cbor2.dumps(["Signature1", protected_b, b"", payload]))
    claims = cbor2.loads(payload)
    if not isinstance(claims, dict) or claims.get(CWT_SUB) != uri:
        raise CredentialError("STATUS_LIST_INVALID", "sub")
    exp = claims.get(CWT_EXP)
    if isinstance(exp, int | float) and now.timestamp() >= exp:
        raise CredentialError("STATUS_LIST_INVALID", "expired")
    sl = claims.get(CWT_STATUS_LIST)
    if not isinstance(sl, dict) or not isinstance(sl.get("lst"), bytes):
        raise CredentialError("STATUS_LIST_INVALID", "status_list")
    bits = sl.get("bits")
    if not isinstance(bits, int):
        raise CredentialError("STATUS_LIST_INVALID", "bits")
    return bits, sl["lst"]


def verify_device_response(
    encoded: str,
    *,
    doc_type: str,
    iaca_roots: Sequence[str],
    transcript: list[Any],
    requested: Sequence[str],
    holder_binding_required: bool,
    now: datetime,
) -> tuple[dict[str, Any], Mapping[str, Any]]:
    """Returns (disclosed claims for the requested element identifiers, the MSO)."""
    try:
        response = cbor2.loads(b64d(encoded))
    except (ValueError, cbor2.CBORDecodeError) as e:
        raise CredentialError("MDOC_MALFORMED") from e
    documents = response.get("documents") if isinstance(response, dict) else None
    if not isinstance(documents, list) or len(documents) != 1:
        raise CredentialError("MDOC_MALFORMED", "exactly one document")
    doc = documents[0]
    if doc.get("docType") != doc_type:
        raise CredentialError("MDOC_DOCTYPE_MISMATCH")
    issuer_signed = doc.get("issuerSigned") or {}
    protected_b, unprotected, payload, signature = _sign1(issuer_signed.get("issuerAuth"))
    protected = cbor2.loads(protected_b) if protected_b else {}
    if protected.get(1) != ES256:
        raise CredentialError("MDOC_ALG_NOT_ALLOWED")
    signer = _document_signer(protected, unprotected, iaca_roots, now)
    if not isinstance(payload, bytes):
        raise CredentialError("MDOC_MALFORMED", "MSO payload")
    _verify_es256(signer, signature, cbor2.dumps(["Signature1", protected_b, b"", payload]))
    mso = cbor2.loads(_tag24(cbor2.loads(payload)))
    if mso.get("docType") != doc_type or mso.get("digestAlgorithm") != "SHA-256":
        raise CredentialError("MDOC_MSO_INVALID")
    validity = mso.get("validityInfo") or {}
    if not _time(validity.get("validFrom")) <= now < _time(validity.get("validUntil")):
        raise CredentialError("MDOC_EXPIRED")
    digests = mso.get("valueDigests") or {}
    claims: dict[str, Any] = {}
    for namespace, items in (issuer_signed.get("nameSpaces") or {}).items():
        for tagged in items:
            item_bytes = _tag24(tagged)
            item = cbor2.loads(item_bytes)
            expected = (digests.get(namespace) or {}).get(item.get("digestID"))
            actual = hashlib.sha256(cbor2.dumps(cbor2.CBORTag(24, item_bytes))).digest()
            if expected != actual:
                raise CredentialError("MDOC_DIGEST_MISMATCH", f"{namespace}")
            name = item.get("elementIdentifier")
            if name in requested:
                claims[name] = item.get("elementValue")
    device_signed = doc.get("deviceSigned")
    if holder_binding_required:
        if not isinstance(device_signed, dict):
            raise CredentialError("HOLDER_BINDING_MISSING")
        device_key = _cose_key((mso.get("deviceKeyInfo") or {}).get("deviceKey") or {})
        ns_bytes = device_signed.get("nameSpaces")
        auth = (device_signed.get("deviceAuth") or {}).get("deviceSignature")
        d_protected, _, d_payload, d_signature = _sign1(auth)
        if d_payload is not None:
            raise CredentialError("MDOC_MALFORMED", "device signature must be detached")
        device_auth = cbor2.dumps(
            cbor2.CBORTag(24, cbor2.dumps(["DeviceAuthentication", transcript, doc_type, ns_bytes]))
        )
        _verify_es256(
            device_key, d_signature, cbor2.dumps(["Signature1", d_protected, b"", device_auth])
        )
    return claims, mso
