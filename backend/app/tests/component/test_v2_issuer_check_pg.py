"""The `issuer` check of POST /v2/documents/validate, answered from the issuer registry (ZID-INT-002 section 09; T26).

A document's own claim is never the answer. The name is resolved in the registry the owners reviewed, and the signer is bound only through a
certificate recorded for that issuer: a good signature from a certificate nobody recorded is not issuer-authentic.
"""

from __future__ import annotations

import base64
import hashlib
import sys
from pathlib import Path

from test_v2_facade_pg import world

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "unit"))
from cryptography.hazmat.primitives.serialization import Encoding
from pdf_signing import pki, sign_pdf
from zid_platform.modules.credential.application import issuer_binding
from zid_platform.modules.credential.domain import trust_graph as tg
from zid_platform.modules.evidence.application.trust import TrustConfig

UNSIGNED_PDF = (
    b"%PDF-1.4\n1 0 obj\n<< /Type /Catalog >>\nendobj\ntrailer\n<< /Root 1 0 R >>\n%%EOF\n"
)


def b64(raw: bytes) -> str:
    return base64.b64encode(raw).decode()


def registry(sha: str | None, **extra) -> tg.Registry:
    return tg.parse(
        {
            "version": "t",
            "issuers": [
                {
                    "issuer_id": "exampleton",
                    "kind": "UNIVERSITY",
                    "authority": "OFFICIAL",
                    "countries": ["US"],
                    "claim_types": ["ACADEMIC_AWARD"],
                    "names": [{"name": "University of Exampleton"}],
                    "seal_signers": [{"sha256": sha}] if sha else [],
                    **extra,
                }
            ],
        }
    )


def test_the_issuer_check_is_answered_from_the_registry_and_never_from_the_document(
    rt_url: str, monkeypatch
) -> None:
    p = pki()
    signed = sign_pdf(p)
    fingerprint = hashlib.sha256(p.signer.cert.public_bytes(Encoding.DER)).hexdigest()

    def validate(http, mine, pdf: bytes, claim: dict | None):
        body = {
            "content_base64": b64(pdf),
            "declared_type": "application/pdf",
            "checks": ["issuer"],
        }
        if claim is not None:
            body["issuer_claim"] = claim
        r = http.post("/v2/documents/validate", headers=mine, json=body)
        assert r.status_code == 200, r.text
        return {x["check"]: x for x in r.json()["document_results"]}["issuer"]

    name = {"name": "University of Exampleton", "country": "US"}
    with world(rt_url) as (http, mine, _tenant, _admin):
        http.app.state.trust_config = TrustConfig(anchors=(p.root.cert,))

        # with no claim there is nothing to look up: exactly as before
        none = validate(http, mine, signed, None)
        assert none["status"] == "UNAVAILABLE" and none["reasons"] == ["NO_ISSUER_SOURCE"]

        # T26: the name resolves, the signature is good, but nobody recorded that certificate for that issuer
        monkeypatch.setattr(issuer_binding, "issuer_registry", lambda: registry(None))
        t26 = validate(http, mine, signed, name)
        assert t26["status"] == "INDETERMINATE"
        assert t26["reasons"] == ["ISSUER_KNOWN_BUT_DOCUMENT_NOT_BOUND_TO_IT"]
        assert t26["detail"]["match"] == "RESOLVED" and t26["detail"]["binding"] == "NOT_BOUND"

        # the owners record the certificate: now the document is bound to the issuer, and still only VERIFIED_LIMITED
        monkeypatch.setattr(issuer_binding, "issuer_registry", lambda: registry(fingerprint))
        ok = validate(http, mine, signed, name)
        assert ok["status"] == "VERIFIED_LIMITED" and ok["status"] != "VERIFIED"
        assert ok["reasons"] == ["ISSUER_RESOLVED", "SEAL_BOUND_TO_A_RECORDED_CERTIFICATE"]
        assert (
            ok["detail"]["issuer_id"] == "exampleton"
            and ok["detail"]["source"] == "issuer_registry"
        )

        # a certificate somebody else recorded does not bind this document
        monkeypatch.setattr(issuer_binding, "issuer_registry", lambda: registry("a" * 64))
        assert validate(http, mine, signed, name)["reasons"] == [
            "ISSUER_KNOWN_BUT_DOCUMENT_NOT_BOUND_TO_IT"
        ]

        # an unsigned document, or one signed by a certificate this deployment does not trust, is never bound
        monkeypatch.setattr(issuer_binding, "issuer_registry", lambda: registry(fingerprint))
        unsigned = validate(http, mine, UNSIGNED_PDF, name)
        assert (
            unsigned["status"] == "INDETERMINATE"
            and unsigned["detail"]["binding"] == "NO_TRUSTED_SIGNATURE"
        )
        http.app.state.trust_config = TrustConfig()
        assert validate(http, mine, signed, name)["detail"]["binding"] == "NO_TRUSTED_SIGNATURE"
        http.app.state.trust_config = TrustConfig(anchors=(p.root.cert,))

        # names: an unknown one is unknown, and a near miss is a lookalike that a person decides
        unknown = validate(http, mine, signed, {"name": "Totally Fake College", "country": "US"})
        assert unknown["reasons"] == ["ISSUER_NOT_IN_REGISTRY"]
        lookalike = validate(
            http, mine, signed, {"name": "University of Exampletown", "country": "US"}
        )
        assert lookalike["reasons"] == ["ISSUER_NAME_AMBIGUOUS_OR_LOOKALIKE"]
        assert lookalike["detail"]["candidates"] == ["exampleton"]

        # an issuer withdrawn from the registry is not verified
        monkeypatch.setattr(
            issuer_binding,
            "issuer_registry",
            lambda: registry(fingerprint, revoked_on="2020-01-01"),
        )
        revoked = validate(http, mine, signed, name)
        assert revoked["status"] == "NOT_VERIFIED" and revoked["reasons"] == ["ISSUER_REVOKED"]

        bad = http.post(
            "/v2/documents/validate",
            headers=mine,
            json={
                "content_base64": b64(signed),
                "declared_type": "application/pdf",
                "checks": ["issuer"],
                "issuer_claim": {"name": "X", "country": "usa"},
            },
        )
        assert bad.status_code == 422
