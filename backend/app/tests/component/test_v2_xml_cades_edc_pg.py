"""Signed XML, detached CAdES and sealed EDC through the real app and Postgres (DTV-02, GID-051).

The unit tests prove the validators; this proves the routes: authentication, the action grant, the request contract, and that what comes
back is what the deployment's trust configuration allows (the shipped configuration trusts nothing).
"""

from __future__ import annotations

import base64
import sys
from pathlib import Path

from test_v2_facade_pg import world

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "unit"))
from cryptography.hazmat.primitives import hashes
from cryptography.hazmat.primitives.serialization import Encoding, pkcs7
from pdf_signing import pki
from xades_signing import sign_xml
from zid_platform.modules.evidence.application.trust import TrustConfig

PNG = b"\x89PNG\r\n\x1a\n" + b"\x00" * 64
EDC_BODY = (
    '<learningAchievement><title><text lang="en">BSc Chemistry</text></title></learningAchievement>'
    "<issuer><legalName><text>University of Exampleton</text></legalName></issuer>"
    "<issued>2024-07-12T00:00:00Z</issued>"
    "<credentialSubject><givenName>Morgan</givenName><birthDate>1990-02-03</birthDate></credentialSubject>"
)


def b64(raw: bytes) -> str:
    return base64.b64encode(raw).decode()


def detached(p, content: bytes) -> bytes:
    b = pkcs7.PKCS7SignatureBuilder().set_data(content)
    b = b.add_signer(p.signer.cert, p.signer.key, hashes.SHA256())
    return b.sign(Encoding.DER, [pkcs7.PKCS7Options.DetachedSignature, pkcs7.PKCS7Options.Binary])


def results(resp) -> dict[str, dict]:
    assert resp.status_code == 200, resp.text
    return {r["check"]: r for r in resp.json()["document_results"]}


def test_signed_xml_is_validated_through_the_api_against_the_configured_anchors(
    rt_url: str,
) -> None:
    p = pki()
    xml = sign_xml(p.signer)
    body = {
        "content_base64": b64(xml),
        "declared_type": "application/xml",
        "checks": ["signature", "seal"],
    }
    with world(rt_url) as (http, mine, _tenant, _admin):
        assert http.post("/v2/documents/validate", json=body).status_code == 401

        nothing = results(http.post("/v2/documents/validate", headers=mine, json=body))
        assert nothing["signature"]["status"] == "INDETERMINATE", (
            "the shipped configuration trusts nothing"
        )

        http.app.state.trust_config = TrustConfig(anchors=(p.root.cert,))
        ok = results(http.post("/v2/documents/validate", headers=mine, json=body))
        assert ok["signature"]["status"] == "VERIFIED_LIMITED"
        assert ok["seal"]["detail"]["qualified_status"] == "NOT_ASSESSED"

        changed = {**body, "content_base64": b64(xml.replace(b"hello", b"HELLO"))}
        bad = results(http.post("/v2/documents/validate", headers=mine, json=changed))
        assert bad["signature"]["status"] == "NOT_VERIFIED"

        stranger = pki()
        http.app.state.trust_config = TrustConfig(anchors=(stranger.root.cert,))
        other = results(http.post("/v2/documents/validate", headers=mine, json=body))
        assert other["signature"]["status"] == "INDETERMINATE"
        assert "ISSUER_NOT_TRUSTED" in other["signature"]["reasons"]

        # XML is a validation type only: it can never be uploaded and kept
        up = http.post(
            "/v2/evidence/uploads",
            headers=mine,
            json={"declared_type": "application/xml", "size_bytes": len(xml), "sha256": "0" * 64},
        )
        assert up.status_code in {400, 422}, up.text


def test_a_detached_cades_signature_is_validated_against_the_file_it_signs(rt_url: str) -> None:
    p = pki()
    sig = detached(p, PNG)
    body = {
        "content_base64": b64(PNG),
        "signature_base64": b64(sig),
        "declared_type": "image/png",
        "checks": ["signature", "seal"],
    }
    with world(rt_url) as (http, mine, _tenant, _admin):
        http.app.state.trust_config = TrustConfig(anchors=(p.root.cert,))
        ok = results(http.post("/v2/documents/validate", headers=mine, json=body))
        assert ok["signature"]["status"] == "VERIFIED_LIMITED"

        wrong = {**body, "content_base64": b64(PNG + b"x")}
        bad = results(http.post("/v2/documents/validate", headers=mine, json=wrong))
        assert bad["signature"]["status"] == "NOT_VERIFIED"

        notb64 = http.post(
            "/v2/documents/validate", headers=mine, json={**body, "signature_base64": "!!!!"}
        )
        assert notb64.status_code == 422
        junk = results(
            http.post(
                "/v2/documents/validate",
                headers=mine,
                json={**body, "signature_base64": b64(b"not a signature")},
            )
        )
        assert junk["signature"]["status"] == "INDETERMINATE"


def test_a_sealed_edc_is_checked_and_nothing_about_the_holder_comes_back(rt_url: str) -> None:
    p = pki()
    xml = sign_xml(p.signer, body=EDC_BODY)
    with world(rt_url) as (http, mine, _tenant, _admin):
        assert (
            http.post("/v2/credentials/edc/verify", json={"content_base64": b64(xml)}).status_code
            == 401
        )

        http.app.state.trust_config = TrustConfig(anchors=(p.root.cert,))
        r = http.post("/v2/credentials/edc/verify", headers=mine, json={"content_base64": b64(xml)})
        assert r.status_code == 200, r.text
        out = r.json()
        assert out["accepted_as_claim"] is True and out["assurance_tier"] == "DOCUMENT_INTEGRITY"
        assert out["claim"] == {
            "awarding_body": "University of Exampleton",
            "title": "BSc Chemistry",
            "issued": "2024-07-12T00:00:00Z",
        }
        assert out["primary_source_verified"] is False and out["statutory_license"] is False
        assert out["issuer_binding"] == "ISSUER_UNKNOWN" and out["issuer_id"] is None
        assert out["qualified_seal"]["detail"]["qualified_status"] == "NOT_ASSESSED"
        assert "Morgan" not in r.text and "1990" not in r.text, (
            "nothing about the holder is returned"
        )

        tampered = xml.replace(b"Chemistry", b"Medicine!")
        bad = http.post(
            "/v2/credentials/edc/verify", headers=mine, json={"content_base64": b64(tampered)}
        ).json()
        assert bad["accepted_as_claim"] is False and bad["seal"]["status"] == "NOT_VERIFIED"

        http.app.state.trust_config = TrustConfig()
        none = http.post(
            "/v2/credentials/edc/verify", headers=mine, json={"content_base64": b64(xml)}
        ).json()
        assert none["accepted_as_claim"] is False and none["assurance_tier"] == "SELF_DECLARED"

        not_edc = http.post(
            "/v2/credentials/edc/verify",
            headers=mine,
            json={"content_base64": b64(b"<invoice><total>1</total></invoice>")},
        )
        assert not_edc.status_code == 422
        assert not_edc.headers["content-type"].startswith("application/problem+json")
        assert "not_an_edc" in not_edc.text.lower().replace(" ", "_").replace(".", "")
        assert (
            http.post(
                "/v2/credentials/edc/verify", headers=mine, json={"content_base64": "!!!!"}
            ).status_code
            == 422
        )


def test_an_edc_sealed_by_the_certificate_recorded_for_its_issuer_is_issuer_authenticated(
    rt_url: str, monkeypatch
) -> None:
    import hashlib

    from zid_platform.modules.credential.api import v2_edc
    from zid_platform.modules.credential.domain import trust_graph

    p = pki()
    xml = sign_xml(p.signer, body=EDC_BODY)
    fingerprint = hashlib.sha256(p.signer.cert.public_bytes(Encoding.DER)).hexdigest()

    def registry(sha: str) -> trust_graph.Registry:
        return trust_graph.parse(
            {
                "version": "t",
                "issuers": [
                    {
                        "issuer_id": "exampleton",
                        "kind": "UNIVERSITY",
                        "authority": "OFFICIAL",
                        "countries": ["XX"],
                        "claim_types": ["ACADEMIC_AWARD"],
                        "names": [{"name": "University of Exampleton"}],
                        "seal_signers": [{"sha256": sha}],
                    }
                ],
            }
        )

    with world(rt_url) as (http, mine, _tenant, _admin):
        http.app.state.trust_config = TrustConfig(anchors=(p.root.cert,))
        body = {"content_base64": b64(xml)}

        monkeypatch.setattr(v2_edc, "issuer_registry", lambda: registry(fingerprint))
        ok = http.post("/v2/credentials/edc/verify", headers=mine, json=body).json()
        assert ok["issuer_binding"] == "BOUND" and ok["issuer_id"] == "exampleton"
        assert ok["assurance_tier"] == "ISSUER_AUTHENTICATED"
        assert ok["primary_source_verified"] is False and ok["statutory_license"] is False

        # a real seal, but not from the certificate the registry records for that university
        monkeypatch.setattr(v2_edc, "issuer_registry", lambda: registry("a" * 64))
        other = http.post("/v2/credentials/edc/verify", headers=mine, json=body).json()
        assert (
            other["issuer_binding"] == "NOT_BOUND"
            and other["assurance_tier"] == "DOCUMENT_INTEGRITY"
        )

        # the bound issuer does not rescue a seal that is broken
        monkeypatch.setattr(v2_edc, "issuer_registry", lambda: registry(fingerprint))
        broken = http.post(
            "/v2/credentials/edc/verify",
            headers=mine,
            json={"content_base64": b64(xml.replace(b"Chemistry", b"Medicine!"))},
        ).json()
        assert broken["assurance_tier"] == "SELF_DECLARED" and broken["accepted_as_claim"] is False


def test_an_mrz_is_extracted_with_its_check_digits_and_never_called_genuine(rt_url: str) -> None:
    lines = [
        "P<UTOERIKSSON<<ANNA<MARIA<<<<<<<<<<<<<<<<<<<",
        "L898902C36UTO7408122F1204159ZE184226B<<<<<10",
    ]
    with world(rt_url) as (http, mine, _tenant, _admin):
        assert http.post("/v2/documents/extract", json={"mrz_lines": lines}).status_code == 401

        r = http.post("/v2/documents/extract", headers=mine, json={"mrz_lines": lines})
        assert r.status_code == 200, r.text
        out = r.json()
        assert out["form"] == "TD3" and out["all_check_digits_hold"] is True
        assert out["fields"]["surname"] == "ERIKSSON"
        assert out["fields"]["document_number"] == {
            "value": "L898902C3",
            "check_digit_ok": True,
            "source": "MRZ",
        }
        assert out["fields"]["birth_date"]["iso"] == "1974-08-12"
        assert out["authenticity"] == "NOT_ASSESSED" and out["assurance_ceiling"] == "SELF_DECLARED"

        bad = [lines[0], lines[1].replace("L898902C3", "L898902C4")]
        flagged = http.post("/v2/documents/extract", headers=mine, json={"mrz_lines": bad}).json()
        assert flagged["all_check_digits_hold"] is False
        assert "DOCUMENT_NUMBER_CHECK_DIGIT_FAILED" in flagged["reason_codes"]
        assert flagged["fields"]["document_number"]["value"] == "L898902C4", "reported as read"

        short = http.post("/v2/documents/extract", headers=mine, json={"mrz_lines": [lines[0]]})
        assert short.status_code == 422
        odd = http.post(
            "/v2/documents/extract", headers=mine, json={"mrz_lines": ["x" * 80, "y" * 80]}
        )
        assert odd.status_code == 422
        notmrz = http.post(
            "/v2/documents/extract",
            headers=mine,
            json={"mrz_lines": ["hello there", "general kenobi!"]},
        )
        assert notmrz.status_code == 422


def test_pdf_form_fields_are_read_and_only_called_unchanged_when_a_valid_signature_covers_them(
    rt_url: str, monkeypatch
) -> None:
    import pdf_signing

    head = (
        b"%PDF-1.7\n1 0 obj\n<< /Type /Catalog /Pages 3 0 R /AcroForm 4 0 R >>\nendobj\n"
        b"4 0 obj\n<< /Fields [5 0 R] >>\nendobj\n"
        b"5 0 obj\n<< /FT /Tx /T (holder) /V (Dana Lee) >>\nendobj\n"
        b"2 0 obj\n<< /Type /Sig /Filter /Adobe.PPKLite /SubFilter /adbe.pkcs7.detached "
        b"/ByteRange [0000000000 0000000000 0000000000 0000000000] /Contents "
    )
    tail = b" /M (D:20260101000000Z) >>\nendobj\ntrailer\n<< /Root 1 0 R >>\n%%EOF\n"
    monkeypatch.setattr(pdf_signing, "pdf_template", lambda: (head, tail))
    p = pki()
    signed = pdf_signing.sign_pdf(p)
    edited = signed + b"5 0 obj\n<< /FT /Tx /T (holder) /V (Someone Else) >>\nendobj\n"

    def extract(http, mine, pdf: bytes):
        r = http.post("/v2/documents/extract", headers=mine, json={"pdf_base64": b64(pdf)})
        assert r.status_code == 200, r.text
        return r.json()

    with world(rt_url) as (http, mine, _tenant, _admin):
        assert (
            http.post("/v2/documents/extract", json={"pdf_base64": b64(signed)}).status_code == 401
        )

        nothing = extract(http, mine, signed)  # the shipped configuration trusts nothing
        assert nothing["fields"] == [
            {"name": "holder", "type": "Tx", "value": "Dana Lee", "source": "PDF_FORM"}
        ]
        assert (
            nothing["signatures_found"] == 1 and nothing["document_signed_and_unchanged"] is False
        )
        assert nothing["assurance_ceiling"] == "SELF_DECLARED"

        http.app.state.trust_config = TrustConfig(anchors=(p.root.cert,))
        ok = extract(http, mine, signed)
        assert ok["document_signed_and_unchanged"] is True
        assert (
            ok["assurance_ceiling"] == "DOCUMENT_INTEGRITY" and ok["authenticity"] == "NOT_ASSESSED"
        )

        # a field changed after signing: the new value is what is read, and it is no longer covered by the signature
        late = extract(http, mine, edited)
        assert late["fields"][0]["value"] == "Someone Else"
        assert late["document_signed_and_unchanged"] is False

        both = http.post(
            "/v2/documents/extract",
            headers=mine,
            json={"pdf_base64": b64(signed), "mrz_lines": ["x" * 44, "y" * 44]},
        )
        assert both.status_code == 422
        notpdf = http.post(
            "/v2/documents/extract",
            headers=mine,
            json={"pdf_base64": b64(b"hello world, not a pdf")},
        )
        assert notpdf.status_code == 422


def test_a_signed_barcode_is_verified_only_against_a_configured_key(rt_url: str) -> None:
    from test_barcode import cose, qr, signer

    s, stranger = signer(), signer()
    text = qr(cose(s))
    with world(rt_url) as (http, mine, _tenant, _admin):

        def extract(body: dict):
            return http.post("/v2/documents/extract", headers=mine, json=body)

        assert http.post("/v2/documents/extract", json={"barcode_text": text}).status_code == 401

        # the shipped configuration lists no barcode key, so nothing verifies
        none = extract({"barcode_text": text})
        assert none.status_code == 200, none.text
        out = none.json()
        assert out["form"] == "SIGNED_BARCODE" and out["signature"]["status"] == "INDETERMINATE"
        assert out["signature"]["reasons"] == ["KEY_NOT_CONFIGURED"]
        assert out["payload"]["1"] == "UT", "the payload is returned as it was signed"
        assert out["authenticity"] == "NOT_ASSESSED" and out["assurance_ceiling"] == "SELF_DECLARED"

        http.app.state.trust_config = TrustConfig(barcode_keys=(s.cert,))
        ok = extract({"barcode_text": text}).json()
        assert (
            ok["signature"]["status"] == "VERIFIED"
            and ok["assurance_ceiling"] == "DOCUMENT_INTEGRITY"
        )

        http.app.state.trust_config = TrustConfig(barcode_keys=(stranger.cert,))
        other = extract({"barcode_text": text}).json()
        assert other["signature"]["status"] == "INDETERMINATE"

        assert extract({"barcode_text": "HC1:" + "!!!!!!!!"}).status_code == 422
        assert extract({"barcode_text": "not a certificate barcode"}).status_code == 422
        both = extract({"barcode_text": text, "mrz_lines": ["x" * 44, "y" * 44]})
        assert both.status_code == 422


def test_capture_alternatives_are_offered_without_a_reason_and_never_as_a_failure(
    rt_url: str,
) -> None:
    with world(rt_url) as (http, mine, _tenant, _admin):
        assert http.get("/v2/capture-alternatives?country=GB").status_code == 401

        r = http.get("/v2/capture-alternatives?country=GB", headers=mine)
        assert r.status_code == 200, r.text
        out = r.json()
        assert out["country"] == "GB" and len(out["routes"]) >= 1
        assert out["reason_required"] is False
        assert out["counts_as_failed_attempt"] is False and out["rejection_possible"] is False
        assert all(
            route["assurance_note"] and route["how"] and route["code"] for route in out["routes"]
        )

        # a reason only reorders; it never removes a route or changes the promise
        asked = http.get(
            "/v2/capture-alternatives?country=gb&reason=NO_USABLE_CAMERA", headers=mine
        ).json()
        assert {x["code"] for x in asked["routes"]} == {x["code"] for x in out["routes"]}
        assert asked["counts_as_failed_attempt"] is False

        for bad in ("country=GBR", "country=1", ""):
            assert http.get(f"/v2/capture-alternatives?{bad}", headers=mine).status_code == 422


CARD = Path(__file__).resolve().parents[1] / "fixtures" / "ocr" / "sample-card.png"


def test_an_image_is_read_through_a_template_and_every_refusal_is_named(
    rt_url: str, monkeypatch
) -> None:
    from zid_platform.modules.evidence.infrastructure import ocr_client

    image = CARD.read_bytes()
    body = {"image_base64": b64(image), "template_id": "sample-card"}

    async def fake_read(base_url, img, regions, lang, **_kw):
        assert base_url == "http://ocr.test" and img == image and lang == "eng"
        assert [r["name"] for r in regions] == ["surname", "document_number", "birth_date"]
        return (
            [
                {"name": "surname", "text": "SURNAME: ERIKSSON", "confidence": 93.0},
                {"name": "document_number", "text": "DOCUMENT NO: L898902C?", "confidence": 88.0},
                {"name": "birth_date", "text": "BORN: 12 AUG 1974", "confidence": 41.0},
            ],
            "tesseract test",
        )

    with world(rt_url) as (http, mine, _tenant, _admin):

        def extract(payload: dict):
            return http.post("/v2/documents/extract", headers=mine, json=payload)

        assert http.post("/v2/documents/extract", json=body).status_code == 401

        monkeypatch.delenv("ZID_OCR_URL", raising=False)
        none = extract(body)
        assert none.status_code == 503, "no OCR service: nothing is read and nothing is guessed"

        monkeypatch.setenv("ZID_OCR_URL", "http://ocr.test")
        monkeypatch.setattr(ocr_client, "read", fake_read)
        r = extract(body)
        assert r.status_code == 200, r.text
        out = r.json()
        assert out["form"] == "OCR_TEMPLATE" and out["template"]["status"] == "SAMPLE"
        by = {f["name"]: f for f in out["fields"]}
        assert by["surname"]["value"] == "ERIKSSON" and by["surname"]["usable"] is True
        assert by["document_number"]["matched"] is False and by["document_number"]["value"] is None
        assert by["document_number"]["text"] == "DOCUMENT NO: L898902C?", (
            "shown as read, never repaired"
        )
        assert (
            by["birth_date"]["reasons"] == ["LOW_CONFIDENCE"]
            and by["birth_date"]["usable"] is False
        )
        assert out["all_fields_usable"] is False
        assert out["authenticity"] == "NOT_ASSESSED" and out["assurance_ceiling"] == "SELF_DECLARED"
        assert all(f["source"] == "OCR" for f in out["fields"])

        assert (
            extract({"image_base64": b64(image)}).status_code == 422
        )  # an image needs its template
        assert extract({**body, "template_id": "no-such-template"}).status_code == 422
        assert (
            extract(
                {"image_base64": b64(b"%PDF-1.7 not an image"), "template_id": "sample-card"}
            ).status_code
            == 422
        )
        assert extract({**body, "mrz_lines": ["x" * 44, "y" * 44]}).status_code == 422


def test_an_image_is_really_read_by_the_ocr_container(rt_url: str, monkeypatch) -> None:
    """End to end against the real Tesseract sidecar: set ZID_TEST_OCR_URL (docker compose --profile ocr up -d ocr). Skipped otherwise."""
    import os

    import pytest

    url = os.environ.get("ZID_TEST_OCR_URL")
    if not url:
        pytest.skip("no OCR container configured (ZID_TEST_OCR_URL)")
    monkeypatch.setenv("ZID_OCR_URL", url)
    with world(rt_url) as (http, mine, _tenant, _admin):
        r = http.post(
            "/v2/documents/extract",
            headers=mine,
            json={"image_base64": b64(CARD.read_bytes()), "template_id": "sample-card"},
        )
        assert r.status_code == 200, r.text
        out = r.json()
        by = {f["name"]: f for f in out["fields"]}
        assert by["surname"]["value"] == "ERIKSSON"
        assert by["document_number"]["value"] == "L898902C3"
        assert by["birth_date"]["value"] == "12 AUG 1974"
        assert out["all_fields_usable"] is True and out["ocr_engine"].startswith("tesseract")
        assert out["authenticity"] == "NOT_ASSESSED"
