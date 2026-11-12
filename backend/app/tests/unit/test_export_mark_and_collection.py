"""AT-047 (a marked export) and AT-034/035/037 (counsel's collection requirements) as rules.

The collection tests assert behaviour around fixture rules only; what Illinois, California or Australia require is counsel's to state and is not written here.
"""

from __future__ import annotations

from datetime import UTC, date, datetime, timedelta

import pytest
import yaml
from zid_crypto import LocalSigner
from zid_platform.modules.privacy.application import export_signing as signing
from zid_platform.modules.privacy.domain import collection as col
from zid_platform.modules.privacy.domain import export_mark as ex

NOW = datetime(2026, 11, 12, 12, 0, tzinfo=UTC)
SIGNER = LocalSigner("export-test")
KEYS = [SIGNER.public_jwk()]
SOURCE = {
    "full_name": "Dana Lee",
    "licence_status": "ACTIVE",
    "licence_expiry": "2027-03-01",
    "home_address": "1 High St",
}


def made(include=("licence_status", "licence_expiry"), **over):
    args = {
        "recipient": "zoikorum",
        "purpose": "engagement_check",
        "ttl": timedelta(hours=1),
        "tenant_id": "t1",
        "now": NOW,
    }
    payload, missing = ex.build(SOURCE, list(include), **{**args, **over})
    return payload, missing, signing.sign(payload, SIGNER)


def test_at047_only_the_named_claims_are_in_the_export_and_the_rest_is_dropped() -> None:
    payload, missing, _ = made()
    assert payload["claims"] == {"licence_status": "ACTIVE", "licence_expiry": "2027-03-01"}
    assert "Dana" not in str(payload) and "High St" not in str(payload)
    assert missing == []
    _p, missing, _t = made(include=("licence_status", "no_such_claim"))
    assert missing == ["no_such_claim"], "a name the source lacks is reported, never invented"


def test_at047_raw_biometric_and_document_claims_are_refused_by_name() -> None:
    for name in (
        "selfie_image",
        "face_template",
        "embedding_vector",
        "passport_number",
        "raw_document",
        "biometric_score",
    ):
        with pytest.raises(ex.ExportError) as e:
            ex.build(
                {name: "x"},
                [name],
                recipient="r",
                purpose="p",
                ttl=timedelta(hours=1),
                tenant_id="t",
                now=NOW,
            )
        assert e.value.code == "CLAIM_NOT_EXPORTABLE"
    assert ex.FORBIDDEN.search("licence_status") is None and ex.FORBIDDEN.search("rawness") is None


def test_at047_the_export_is_signed_recipient_bound_and_expires() -> None:
    payload, _m, token = made()
    got = signing.verify(
        token, keys=KEYS, now=NOW + timedelta(minutes=5), expected_recipient="zoikorum"
    )
    assert (
        got["recipient"] == "zoikorum"
        and got["purpose"] == "engagement_check"
        and got["export_id"] == payload["export_id"]
    )
    with pytest.raises(ex.ExportError, match="EXPORT_EXPIRED"):
        signing.verify(token, keys=KEYS, now=NOW + timedelta(hours=2))
    with pytest.raises(ex.ExportError, match="EXPORT_NOT_FOR_THIS_RECIPIENT"):
        signing.verify(token, keys=KEYS, now=NOW, expected_recipient="someone-else")


def test_at047_a_changed_byte_a_wrong_key_or_a_wrong_type_fails() -> None:
    _p, _m, token = made()
    head, body, sig = token.split(".")
    tampered = f"{head}.{body[:-2]}AA.{sig}"
    with pytest.raises(ex.ExportError, match="EXPORT_SIGNATURE_INVALID"):
        signing.verify(tampered, keys=KEYS, now=NOW)
    with pytest.raises(ex.ExportError, match="EXPORT_SIGNATURE_INVALID"):
        signing.verify(token, keys=[LocalSigner("someone-else").public_jwk()], now=NOW)
    other_type = signing.sign_jws(
        SIGNER, {"exp": int((NOW + timedelta(hours=1)).timestamp())}, typ="other+jwt"
    )
    with pytest.raises(ex.ExportError, match="EXPORT_SIGNATURE_INVALID"):
        signing.verify(other_type, keys=KEYS, now=NOW)


def test_at047_no_claims_no_recipient_or_an_unbounded_lifetime_is_refused() -> None:
    def build(**kw):
        a = {
            "source": SOURCE,
            "include": ["licence_status"],
            "recipient": "r",
            "purpose": "p",
            "ttl": timedelta(hours=1),
            "tenant_id": "t",
            "now": NOW,
        }
        a.update(kw)
        return ex.build(a.pop("source"), a.pop("include"), **a)

    with pytest.raises(ex.ExportError, match="NO_CLAIMS_NAMED"):
        build(include=[])
    with pytest.raises(ex.ExportError, match="RECIPIENT_AND_PURPOSE_REQUIRED"):
        build(recipient=" ")
    for ttl in (timedelta(seconds=5), ex.MAX_TTL + timedelta(seconds=1)):
        with pytest.raises(ex.ExportError, match="TTL_OUT_OF_RANGE"):
            build(ttl=ttl)


TODAY = date(2026, 11, 12)
RULES = {
    "approval_ref": "COUNSEL-FIXTURE-2",
    "effective_from": "2026-01-01",
    "written_notice_required": True,
    "release_required": True,
}


def test_the_shipped_collection_rules_are_all_counsel_pending_so_sensitive_collection_is_denied() -> (
    None
):
    from zid_platform.modules.privacy.application import control

    doc = yaml.safe_load((control.CONFIG / "collection-rules.yaml").read_text(encoding="utf-8"))[
        "packs"
    ]
    assert {"us-il-bipa", "us-ca-ccpa", "au-privacy"} <= set(doc)
    for pack_id in doc:
        d = col.evaluate(
            doc, pack_id=pack_id, evidence={"notice_version": "N@1", "release_ref": "R-1"}, on=TODAY
        )
        assert (
            d.applies
            and not d.allowed
            and d.reasons == ("BIOMETRIC_COLLECTION_RULES_NOT_SET_BY_COUNSEL",)
        )


def test_at034_at035_at037_rules_in_force_demand_each_requirement_counsel_switches_on() -> None:
    rules = {"p": RULES}
    none = col.evaluate(rules, pack_id="p", evidence={}, on=TODAY)
    assert not none.allowed and set(none.reasons) == {"NOTICE_REQUIRED", "RELEASE_REQUIRED"}
    half = col.evaluate(rules, pack_id="p", evidence={"notice_version": "N@1"}, on=TODAY)
    assert half.reasons == ("RELEASE_REQUIRED",)
    full = col.evaluate(
        rules, pack_id="p", evidence={"notice_version": "N@1", "release_ref": "R-1"}, on=TODAY
    )
    assert full.allowed and full.rules_ref == "p@COUNSEL-FIXTURE-2"
    retention = {"p": {**RULES, "release_required": False, "retention_schedule_required": True}}
    assert col.evaluate(
        retention, pack_id="p", evidence={"notice_version": "N@1"}, on=TODAY
    ).reasons == ("RETENTION_SCHEDULE_NOT_ASSERTED",)
    necessity = {
        "p": {
            **RULES,
            "written_notice_required": False,
            "release_required": False,
            "necessity_assessment_required": True,
        }
    }
    assert col.evaluate(necessity, pack_id="p", evidence={}, on=TODAY).reasons == (
        "NECESSITY_ASSESSMENT_REQUIRED",
    )


def test_a_pack_with_no_entry_is_untouched_and_a_future_effective_date_does_not_count() -> None:
    free = col.evaluate({"p": RULES}, pack_id="other", evidence={}, on=TODAY)
    assert free.applies is False and free.allowed is True
    future = col.evaluate(
        {"p": {**RULES, "effective_from": "2027-01-01"}},
        pack_id="p",
        evidence={"notice_version": "N@1", "release_ref": "R"},
        on=TODAY,
    )
    assert future.reasons == ("BIOMETRIC_COLLECTION_RULES_NOT_YET_IN_FORCE",) and not future.allowed
