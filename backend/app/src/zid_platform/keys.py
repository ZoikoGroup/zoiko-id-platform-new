"""Signing keys by purpose (ZID-AUTH-001 §17 "separate key set"; DEC-F5).

Each purpose has its own key ring and its own published key set, so a compromise or rotation of
one does not touch the others:
    token       access tokens, ID tokens, our client assertions to upstream IdPs
    set         Security Event Tokens (ZID-SIGNALS-001 §21 "separated where practical")
    federation  OpenID Federation entity statements
    credential  issued credentials and their status lists
    verifier    OID4VP request objects (and the HAIP verifier certificate)
Deployed cells receive KMS/HSM-backed signers from the key broker. Local and test environments use
development keys; ZID_DEV_SIGNING_KEYS (purpose -> PEM) lets separate local processes share them.
"""

from __future__ import annotations

import base64
from collections.abc import Mapping, Sequence
from dataclasses import dataclass

from cryptography import x509
from cryptography.hazmat.primitives.serialization import Encoding
from zid_crypto import KeyRing, LocalSigner, jwk_thumbprint

PURPOSES = ("token", "set", "federation", "credential", "verifier")


@dataclass(frozen=True, slots=True)
class KeyRings:
    token: KeyRing
    set: KeyRing
    federation: KeyRing
    credential: KeyRing
    verifier: KeyRing

    @classmethod
    def single(cls, ring: KeyRing) -> KeyRings:
        """One ring for every purpose (tests and callers that pass a single keyring)."""
        return cls(ring, ring, ring, ring, ring)

    @classmethod
    def development(cls, cell_id: str, pems: Mapping[str, str] | None = None) -> KeyRings:
        rings = {}
        for purpose in PURPOSES:
            kid = f"dev-{purpose}-{cell_id}"
            pem = (pems or {}).get(purpose)
            rings[purpose] = KeyRing(LocalSigner.from_pem(kid, pem) if pem else LocalSigner(kid))
        return cls(**rings)

    def verifier_x5c(self, chain: Sequence[str], *, development: bool) -> tuple[str, ...]:
        """HAIP x509_hash: the verifier certificate chain as x5c (base64 DER, leaf first).

        The leaf must certify the verifier signing key, or wallets would reject every request.
        Without a configured chain, local/test get a self-signed development certificate."""
        signer = self.verifier.active
        if not chain:
            if not development or not isinstance(signer, LocalSigner):
                raise RuntimeError("x509_hash needs ZID_VERIFIER_CERTIFICATE_CHAIN (DEC-F5)")
            der = signer.development_certificate("Zoiko iD development verifier")
            return (base64.b64encode(der).decode(),)
        certs = [x509.load_pem_x509_certificate(pem.encode()) for pem in chain]
        from joserfc.jwk import ECKey, OKPKey

        leaf_key = certs[0].public_key()
        try:
            leaf_jwk = ECKey.import_key(leaf_key).as_dict()  # type: ignore[arg-type]
        except (TypeError, ValueError):
            leaf_jwk = OKPKey.import_key(leaf_key).as_dict()  # type: ignore[arg-type]
        if jwk_thumbprint(leaf_jwk) != jwk_thumbprint(signer.public_jwk()):
            raise RuntimeError("verifier certificate does not certify the verifier signing key")
        return tuple(base64.b64encode(c.public_bytes(Encoding.DER)).decode() for c in certs)

    def distinct(self) -> bool:
        kids = {
            r.active.kid
            for r in (self.token, self.set, self.federation, self.credential, self.verifier)
        }
        return len(kids) == len(PURPOSES)
