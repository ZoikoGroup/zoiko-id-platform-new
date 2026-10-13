"""XML Encryption 1.1 for SAML EncryptedAssertion, decrypt side only (ZID-AUTH-001 §10).

Deliberately narrow:
- content: AES-GCM only. CBC modes are refused: XML Encryption with CBC is open to padding/format
  oracles (Jager & Somorovsky, "How to Break XML Encryption"), and GCM authenticates the plaintext.
- key transport: RSA-OAEP only (rsa-oaep-mgf1p, or xmlenc11 rsa-oaep with an explicit MGF).
  RSA PKCS#1 v1.5 key transport is refused (Bleichenbacher). OAEP with SHA-1 stays allowed: its
  security does not rest on collision resistance and widely deployed IdPs still send it.
- every failure is the same SAML_DECRYPTION_FAILED, so a caller learns nothing about which step
  failed. Decryption never authenticates the sender; the caller still requires an IdP signature.
"""

from __future__ import annotations

import base64
from typing import Any

from cryptography.exceptions import InvalidTag
from cryptography.hazmat.primitives import hashes, serialization
from cryptography.hazmat.primitives.asymmetric import padding, rsa
from cryptography.hazmat.primitives.ciphers.aead import AESGCM
from lxml import etree

from zid_platform.modules.federation.domain.connection import FederationError

XENC = "http://www.w3.org/2001/04/xmlenc#"
XENC11 = "http://www.w3.org/2009/xmlenc11#"
DS = "http://www.w3.org/2000/09/xmldsig#"
NS = {"xenc": XENC, "xenc11": XENC11, "ds": DS}

CONTENT_ALGORITHMS = {
    XENC11 + "aes128-gcm": 16,
    XENC11 + "aes192-gcm": 24,
    XENC11 + "aes256-gcm": 32,
}
KEY_TRANSPORT = frozenset({XENC + "rsa-oaep-mgf1p", XENC11 + "rsa-oaep"})
DIGESTS: dict[str, Any] = {
    DS + "sha1": hashes.SHA1,
    XENC + "sha256": hashes.SHA256,
    "http://www.w3.org/2001/04/xmldsig-more#sha384": hashes.SHA384,
    XENC + "sha512": hashes.SHA512,
}
MGFS: dict[str, Any] = {
    XENC11 + "mgf1sha1": hashes.SHA1,
    XENC11 + "mgf1sha256": hashes.SHA256,
    XENC11 + "mgf1sha384": hashes.SHA384,
    XENC11 + "mgf1sha512": hashes.SHA512,
}
GCM_IV = 12


def _fail(detail: str = "") -> FederationError:
    return FederationError("SAML_DECRYPTION_FAILED", detail)


def load_key(pem: str) -> rsa.RSAPrivateKey:
    try:
        key = serialization.load_pem_private_key(pem.encode(), password=None)
    except (ValueError, TypeError) as e:
        raise _fail("key") from e
    if not isinstance(key, rsa.RSAPrivateKey) or key.key_size < 2048:
        raise _fail("key")
    return key


def _cipher_value(node: etree._Element) -> bytes:
    found = node.findall("xenc:CipherData/xenc:CipherValue", NS)
    if len(found) != 1 or not found[0].text:
        raise _fail()
    try:
        return base64.b64decode("".join(found[0].text.split()), validate=True)
    except ValueError as e:
        raise _fail() from e


def _unwrap(encrypted_key: etree._Element, key: rsa.RSAPrivateKey) -> bytes:
    method = encrypted_key.find("xenc:EncryptionMethod", NS)
    alg = method.get("Algorithm") if method is not None else None
    if method is None or alg not in KEY_TRANSPORT:
        raise _fail("key transport")
    digest_node = method.find("ds:DigestMethod", NS)
    digest = (
        DIGESTS.get(digest_node.get("Algorithm", "")) if digest_node is not None else hashes.SHA1
    )
    mgf: Any = hashes.SHA1
    if alg == XENC11 + "rsa-oaep":
        mgf_node = method.find("xenc11:MGF", NS)
        if mgf_node is not None:
            mgf = MGFS.get(mgf_node.get("Algorithm", ""))
    if digest is None or mgf is None:
        raise _fail("key transport")
    try:
        return key.decrypt(
            _cipher_value(encrypted_key),
            padding.OAEP(mgf=padding.MGF1(algorithm=mgf()), algorithm=digest(), label=None),
        )
    except ValueError as e:
        raise _fail() from e


def decrypt_assertion(encrypted_assertion: etree._Element, key: rsa.RSAPrivateKey) -> bytes:
    """The plaintext of one saml:EncryptedAssertion (still to be parsed defensively)."""
    data = encrypted_assertion.findall("xenc:EncryptedData", NS)
    if len(data) != 1:
        raise _fail()
    method = data[0].find("xenc:EncryptionMethod", NS)
    size = CONTENT_ALGORITHMS.get(method.get("Algorithm", "")) if method is not None else None
    if size is None:
        raise _fail("content encryption")  # CBC and anything unknown
    keys = data[0].findall("ds:KeyInfo/xenc:EncryptedKey", NS) or encrypted_assertion.findall(
        "xenc:EncryptedKey", NS
    )
    if len(keys) != 1:
        raise _fail("exactly one EncryptedKey")
    cek = _unwrap(keys[0], key)
    if len(cek) != size:
        raise _fail()
    blob = _cipher_value(data[0])
    if len(blob) < GCM_IV + 16:
        raise _fail()
    try:
        return AESGCM(cek).decrypt(blob[:GCM_IV], blob[GCM_IV:], None)
    except InvalidTag as e:
        raise _fail() from e
