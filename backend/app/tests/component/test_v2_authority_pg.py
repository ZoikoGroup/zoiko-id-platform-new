"""POST /v2/mandates/verify and /v2/rooms/property-checks through the real app and Postgres, with real uploaded and really signed PDFs.

What is proved over HTTP: authentication, the evidence rules (unknown, malformed and tampered evidence), what a configured trust anchor changes,
and that no answer is ever stronger than VERIFIED_LIMITED or ELIGIBLE. The rule-by-rule logic is in `tests/unit/test_authority_check.py`.
"""

from __future__ import annotations

import sys
import uuid
from pathlib import Path

from test_v2_facade_pg import world
from test_v2_uploads_pg import announce, put

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "unit"))
from pdf_signing import pki, sign_pdf
from zid_platform.modules.evidence.application.trust import TrustConfig

UNSIGNED_PDF = (
    b"%PDF-1.4\n1 0 obj\n<< /Type /Catalog >>\nendobj\ntrailer\n<< /Root 1 0 R >>\n%%EOF\n"
)


def lei(base: str = "5493001KJTIIGC8Y1R") -> str:
    n = int("".join(str(int(c, 36)) for c in base) + "00")
    return base + f"{98 - n % 97:02d}"


def upload(http, mine, content: bytes) -> str:
    a = announce(http, mine, content, "application/pdf").json()
    assert put(http, mine, a, content).status_code == 201
    return a["evidence_ref"]


def test_a_mandate_is_signed_document_only_and_a_company_without_one_has_no_authority(
    rt_url: str,
) -> None:
    p = pki()
    body = {
        "organization_ref": "org_acme",
        "subject_ref": "sub_dana",
        "lei": lei(),
        "authority_claim": "sign_contracts_up_to_limit",
    }
    with world(rt_url) as (http, mine, _tenant, _admin):

        def verify(**extra):
            return http.post("/v2/mandates/verify", headers=mine, json={**body, **extra})

        assert http.post("/v2/mandates/verify", json=body).status_code == 401

        # T30: the identifier is well formed and no mandate is on file: authority is INDETERMINATE
        none = verify()
        assert none.status_code == 200, none.text
        out = none.json()
        assert out["legal_entity"]["reasons"] == ["LEI_WELL_FORMED_EXISTENCE_NOT_LOOKED_UP_HERE"]
        assert (
            out["authority"]["outcome"] == "INDETERMINATE" and out["authority_assurance"] == "NONE"
        )
        assert out["authority"]["reasons"] == ["MANDATE_EVIDENCE_ABSENT"]
        assert (
            out["policy_pack"]["id"] == "authority-evidence-v1"
            and out["valid_until"] > out["checked_at"]
        )

        signed = upload(http, mine, sign_pdf(p))
        # the shipped configuration trusts nothing, so even a really signed mandate proves nothing about who signed it
        untrusted = verify(mandate_evidence_refs=[signed]).json()
        assert untrusted["authority"]["outcome"] == "INDETERMINATE"
        assert untrusted["authority"]["reasons"] == ["EVIDENCE_NOT_SIGNED_OR_NOT_TRUSTED"]

        http.app.state.trust_config = TrustConfig(anchors=(p.root.cert,))
        ok = verify(mandate_evidence_refs=[signed]).json()
        assert (
            ok["authority"]["outcome"] == "VERIFIED_LIMITED"
            and ok["authority"]["outcome"] != "VERIFIED"
        )
        assert ok["authority_assurance"] == "SIGNED_DOCUMENT_ONLY"
        assert "scope_of_authority_for_this_act_not_confirmed" in ok["authority"]["limitations"]
        assert ok["evidence"][0]["signed_and_unchanged"] is True

        tampered = upload(http, mine, sign_pdf(p, tamper_document=True))
        bad = verify(mandate_evidence_refs=[signed, tampered]).json()
        assert bad["authority"]["outcome"] == "NOT_VERIFIED", (
            "one broken document is not outvoted by a good one"
        )

        unsigned = upload(http, mine, UNSIGNED_PDF)
        assert (
            verify(mandate_evidence_refs=[unsigned]).json()["authority"]["outcome"]
            == "INDETERMINATE"
        )

        ghost = "ev_" + str(uuid.uuid4())  # well formed but not this tenant's upload
        assert verify(mandate_evidence_refs=[ghost]).json()["authority"]["reasons"] == [
            "EVIDENCE_NOT_USABLE"
        ]
        assert verify(mandate_evidence_refs=["not-a-ref"]).status_code == 422

        wrong_lei = list(lei())
        wrong_lei[2] = "9" if wrong_lei[2] != "9" else "8"
        assert verify(lei="".join(wrong_lei)).json()["legal_entity"]["reasons"] == [
            "LEI_CHECK_DIGITS_INVALID"
        ]


def test_property_checks_keep_title_mandate_and_the_legal_route_apart(rt_url: str) -> None:
    p = pki()
    base = {
        "property_ref": "prop_1",
        "host_ref": "sub_host",
        "jurisdiction": {"country": "GB", "subdivision": "GB-ENG"},
        "purpose": "rental_listing",
    }
    with world(rt_url) as (http, mine, _tenant, _admin):
        http.app.state.trust_config = TrustConfig(anchors=(p.root.cert,))
        title = upload(http, mine, sign_pdf(p))
        mandate = upload(http, mine, sign_pdf(p))

        def check(**extra):
            return http.post("/v2/rooms/property-checks", headers=mine, json={**base, **extra})

        agent = {
            "relationship": "authorized_agent",
            "requested_dimensions": ["ownership", "mandate"],
        }
        assert http.post("/v2/rooms/property-checks", json={**base, **agent}).status_code == 401

        # T24: an official title without a mandate does not let an agent list
        t24 = check(**agent, title_evidence_refs=[title])
        assert t24.status_code == 200, t24.text
        out = t24.json()
        dims = {d["dimension"]: d for d in out["dimensions"]}
        assert (
            dims["ownership"]["outcome"] == "VERIFIED_LIMITED"
            and dims["mandate"]["outcome"] == "INDETERMINATE"
        )
        assert out["listing_authority"] == "UNKNOWN"

        # with a mandate of its own it is a matter for review, and never ELIGIBLE
        both = check(**agent, title_evidence_refs=[title], mandate_evidence_refs=[mandate]).json()
        assert (
            both["listing_authority"] == "REVIEW_REQUIRED"
            and both["listing_authority"] != "ELIGIBLE"
        )
        assert "never confirms a right to let" in both["listing_authority_note"]

        # T25: England needs a registered provider, and no other place borrows its rule
        england = check(relationship="owner", requested_dimensions=["tenant_legal_route"]).json()
        assert england["dimensions"][0]["reasons"] == [
            "RIGHT_TO_RENT_REQUIRES_A_REGISTERED_PROVIDER"
        ]
        elsewhere = check(
            relationship="owner",
            requested_dimensions=["tenant_legal_route"],
            jurisdiction={"country": "US", "subdivision": "US-CA"},
        ).json()
        assert elsewhere["dimensions"][0]["outcome"] == "UNAVAILABLE"
        assert elsewhere["dimensions"][0]["reasons"] == ["NO_RULE_FOR_THIS_JURISDICTION"]
        scotland = check(
            relationship="owner",
            requested_dimensions=["tenant_legal_route"],
            jurisdiction={"country": "GB", "subdivision": "GB-SCT"},
        ).json()
        assert scotland["dimensions"][0]["reasons"] == ["NO_RULE_FOR_THIS_JURISDICTION"]

        assert (
            check(relationship="landlord_friend", requested_dimensions=["ownership"]).status_code
            == 422
        )
        assert check(relationship="owner", requested_dimensions=["vibes"]).status_code == 422
        assert (
            check(
                relationship="owner",
                requested_dimensions=["ownership"],
                jurisdiction={"country": "gb"},
            ).status_code
            == 422
        )
