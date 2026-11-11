"""GID-052 identifier matching, GID-053/061 portable disclosure, GID-054 issuer outreach, GID-058/060 lifecycle and ordering.

GLB-08  old-name degree with a lawful name change: evidence-based resolution, else a person
GLB-10  status changes after a pass: the change is applied and the dimension recomputed
GLB-14  manual verification response from a spoofed sender is refused
GLB-20  withdrawn permission stops future disclosure and every derived grant
GLB-22  right name, wrong year: no adverse decision
GLB-24  Arabic name on the source, Latin on the document: matched, not rejected
GLB-26  an out-of-order update cannot undo a newer status
GLB-27  an external verifier gets a bounded assertion, never a raw document
GLB-33  a decision for one consumer is not usable by another
"""

from __future__ import annotations

import hmac
from datetime import UTC, datetime, timedelta

import pytest
from zid_platform.modules.credential.application.identity_match import Holder, match_holder
from zid_platform.modules.credential.domain import lifecycle as lc
from zid_platform.modules.credential.domain import outreach as oc
from zid_platform.modules.credential.domain import portable as pt
from zid_platform.modules.credential.domain.claims import CredentialStatus, SubjectMatch
from zid_platform.modules.credential.domain.eligibility import Dimension, DimStatus

T0 = datetime(2026, 11, 11, 12, tzinfo=UTC)


def at(**kw) -> datetime:
    return T0 + timedelta(**kw)


# --- GID-052 -----------------------------------------------------------------------------------------------------------


def test_a_diacritic_or_word_order_difference_is_not_a_rejection():
    for source in ("José Álvarez", "ALVAREZ JOSE", "jose alvarez"):
        assert match_holder(Holder("Jose Alvarez"), source)[0] is SubjectMatch.MATCHED


def test_glb_24_an_arabic_source_name_matches_a_latin_document_name():
    decision, _ = match_holder(Holder("Mohammed Ali"), "محمد علي")
    assert decision in (
        SubjectMatch.MATCHED,
        SubjectMatch.AMBIGUOUS,
    )  # transliteration never makes it NOT_MATCHED
    assert decision is not SubjectMatch.NOT_MATCHED


def test_glb_08_an_old_name_needs_change_evidence_otherwise_a_person_decides():
    h = Holder("Dana Lee", former_names=["Dana Smith"])
    assert match_holder(h, "Dana Smith") == (
        SubjectMatch.AMBIGUOUS,
        ("FORMER_NAME_WITHOUT_CHANGE_EVIDENCE",),
    )
    with_ev = Holder("Dana Lee", former_names=["Dana Smith"], name_change_evidence=True)
    assert match_holder(with_ev, "Dana Smith") == (
        SubjectMatch.MATCHED,
        ("MATCHED_VIA_FORMER_NAME_WITH_EVIDENCE",),
    )


def test_glb_22_a_conflicting_birth_year_never_becomes_a_match():
    d, why = match_holder(Holder("Dana Lee", birth_year=1980), "Dana Lee", source_birth_year=1991)
    assert d is SubjectMatch.AMBIGUOUS and "BIRTH_YEAR_CONFLICT" in why
    assert (
        match_holder(Holder("Dana Lee", birth_year=1980), "Dana Lee", source_birth_year=1980)[0]
        is SubjectMatch.MATCHED
    )
    assert (
        match_holder(Holder("Dana Lee", birth_year=1980), "Dana Lee")[0] is SubjectMatch.MATCHED
    )  # no year at source: no conflict


def test_a_different_name_is_not_matched_and_a_partial_goes_to_a_person():
    assert match_holder(Holder("Dana Lee"), "Priya Raman")[0] is SubjectMatch.NOT_MATCHED
    assert match_holder(Holder("Dana Lee"), "Dana Leeson")[0] is SubjectMatch.AMBIGUOUS


# --- GID-053 / 061 -----------------------------------------------------------------------------------------------------


def _grant(**over) -> pt.Grant:
    base = {
        "grant_id": "g1",
        "subject": "sub1",
        "recipient": "acme",
        "consumer": "zoikorum",
        "purpose": "ENGAGEMENT",
        "fields": {"claim_type", "decision"},
        "created_at": T0,
        "expires_at": at(days=30),
    }
    base.update(over)
    return pt.new_grant(**base)


CLAIM = {
    "claim_type": "PROFESSIONAL_LICENSE",
    "decision": "VERIFIED",
    "license_ref": "L-1",
    "date_of_birth": "1980-01-01",
}


def _disclose(g, grants=None, now=None, **kw):
    args = {
        "recipient": "acme",
        "consumer": "zoikorum",
        "purpose": "ENGAGEMENT",
        "now": now or at(days=1),
    }
    args.update(kw)
    return pt.disclose(g, grants or {g.grant_id: g}, CLAIM, **args)


def test_only_the_granted_fields_go_to_the_named_recipient():
    assert _disclose(_grant()) == {"claim_type": "PROFESSIONAL_LICENSE", "decision": "VERIFIED"}


@pytest.mark.parametrize(
    ("kw", "code"),
    [
        ({"recipient": "other"}, "RECIPIENT_MISMATCH"),
        ({"purpose": "MARKETING"}, "PURPOSE_MISMATCH"),
        ({"consumer": "rooms"}, "CONSUMER_SCOPE_MISMATCH"),  # GLB-33
        ({"now": at(days=31)}, "GRANT_NOT_IN_FORCE"),
        ({"now": at(days=-1)}, "GRANT_NOT_IN_FORCE"),
    ],
)
def test_every_term_must_match_at_disclosure(kw, code):
    with pytest.raises(pt.DisclosureError) as e:
        _disclose(_grant(), **kw)
    assert e.value.code == code


def test_a_grant_cannot_name_a_document_or_a_birth_date():
    for bad in ("date_of_birth", "raw_document", "transcript", "national_id"):
        with pytest.raises(pt.DisclosureError) as e:
            _grant(fields={"decision", bad})
        assert e.value.code == "FIELD_NEVER_DISCLOSED"
    with pytest.raises(pt.DisclosureError):
        _grant(fields=set())
    with pytest.raises(pt.DisclosureError):
        _grant(expires_at=T0)


def test_glb_20_withdrawal_stops_future_disclosure_and_every_derived_grant():
    parent = _grant()
    child = pt.new_grant(
        grant_id="g2",
        subject="sub1",
        recipient="partner",
        consumer="zoikorum",
        purpose="ENGAGEMENT",
        fields={"decision"},
        created_at=at(days=1),
        expires_at=at(days=10),
        parent=parent,
    )
    grants = {"g1": parent, "g2": child}
    assert pt.disclose(
        child,
        grants,
        CLAIM,
        recipient="partner",
        consumer="zoikorum",
        purpose="ENGAGEMENT",
        now=at(days=2),
    ) == {"decision": "VERIFIED"}
    withdrawn = pt.withdraw(parent, at(days=3))
    grants["g1"] = withdrawn
    for g in (withdrawn, child):
        with pytest.raises(pt.DisclosureError) as e:
            pt.disclose(
                g,
                grants,
                CLAIM,
                recipient=g.recipient,
                consumer="zoikorum",
                purpose="ENGAGEMENT",
                now=at(days=4),
            )
        assert e.value.code == "GRANT_NOT_IN_FORCE"
    # before the withdrawal it was fine: history is not rewritten
    assert pt.in_force(parent, grants, at(days=2)) is True


def test_a_derived_grant_can_only_narrow_and_must_have_a_visible_parent():
    parent = _grant()
    with pytest.raises(pt.DisclosureError) as e:
        pt.new_grant(
            grant_id="g2",
            subject="sub1",
            recipient="p",
            consumer="zoikorum",
            purpose="ENGAGEMENT",
            fields={"decision", "claim_type", "issuer_ref"},
            created_at=T0,
            expires_at=at(days=5),
            parent=parent,
        )
    assert e.value.code == "GRANT_WIDER_THAN_PARENT"
    with pytest.raises(pt.DisclosureError):
        pt.new_grant(
            grant_id="g2",
            subject="sub1",
            recipient="p",
            consumer="zoikorum",
            purpose="ENGAGEMENT",
            fields={"decision"},
            created_at=T0,
            expires_at=at(days=90),
            parent=parent,
        )
    with pytest.raises(pt.DisclosureError):
        pt.new_grant(
            grant_id="g2",
            subject="someone-else",
            recipient="p",
            consumer="zoikorum",
            purpose="ENGAGEMENT",
            fields={"decision"},
            created_at=T0,
            expires_at=at(days=5),
            parent=parent,
        )
    orphan = pt.new_grant(
        grant_id="g3",
        subject="sub1",
        recipient="p",
        consumer="zoikorum",
        purpose="ENGAGEMENT",
        fields={"decision"},
        created_at=T0,
        expires_at=at(days=5),
        parent=parent,
    )
    assert pt.in_force(orphan, {"g3": orphan}, at(days=1)) is False  # parent unknown: fail closed


def test_glb_27_the_public_view_is_bounded():
    full = {
        **CLAIM,
        "claim_type": "ACADEMIC_AWARD",
        "credential_status": "ACTIVE",
        "assurance_tier": "PRIMARY_SOURCE_CONFIRMED",
        "source_checked_at": "2026-11-10",
        "issuer_ref": "uni-x",
        "policy_ref": {"id": "p", "version": 1},
        "reason_codes": [],
        "jurisdiction": {},
    }
    view = pt.public_view(full)
    assert (
        set(view) <= set(pt.PUBLIC_FIELDS)
        and "date_of_birth" not in view
        and "license_ref" not in view
    )
    assert pt.public_view(full, revoked=True)["decision"] == "NO_LONGER_VALID"


# --- GID-054 -----------------------------------------------------------------------------------------------------------

KEY = b"issuer-registered-secret"
CONTACTS = {"registrar@uni.example"}


def _open() -> oc.Request:
    return oc.open_request(
        request_id="r1",
        issuer_id="uni-x",
        claim_type="ACADEMIC_AWARD",
        nonce="n-123",
        requested_by="ops1",
        now=T0,
        deadline=at(days=14),
        registered_contacts=CONTACTS,
    )


def _resp(**over) -> oc.Response:
    payload = b'{"outcome":"CONFIRMED"}'
    base = {
        "sender": "registrar@uni.example",
        "nonce": "n-123",
        "outcome": "CONFIRMED",
        "payload": payload,
        "signature": hmac.new(KEY, payload, "sha256").digest(),
        "received_at": at(days=2),
    }
    base.update(over)
    return oc.Response(**base)


def test_an_authenticated_answer_reviewed_by_two_others_is_accepted():
    req = oc.receive(_open(), _resp(), registered_contacts=CONTACTS, verify=oc.hmac_verifier(KEY))
    assert req.state is oc.State.RESPONDED
    done = oc.review(req, {"rev1": True, "rev2": True})
    assert done.state is oc.State.ACCEPTED and oc.verdict(done) == ("CONFIRMED", ())


@pytest.mark.parametrize(
    ("over", "code"),
    [
        ({"sender": "attacker@evil.example"}, "SENDER_NOT_REGISTERED"),  # GLB-14
        ({"nonce": "n-999"}, "NONCE_MISMATCH"),
        ({"signature": b"forged"}, "SIGNATURE_INVALID"),
        ({"received_at": at(days=15)}, "RESPONSE_AFTER_DEADLINE"),
        ({"outcome": "MAYBE"}, "OUTCOME_UNRECOGNIZED"),
    ],
)
def test_a_spoofed_replayed_late_or_unsigned_answer_is_refused(over, code):
    with pytest.raises(oc.OutreachError) as e:
        oc.receive(
            _open(), _resp(**over), registered_contacts=CONTACTS, verify=oc.hmac_verifier(KEY)
        )
    assert e.value.code == code


def test_a_request_needs_a_registered_contact_never_one_from_the_document():
    with pytest.raises(oc.OutreachError) as e:
        oc.open_request(
            request_id="r",
            issuer_id="i",
            claim_type="X",
            nonce="n",
            requested_by="u",
            now=T0,
            deadline=at(days=1),
            registered_contacts=[],
        )
    assert e.value.code == "NO_REGISTERED_CONTACT"


def test_review_is_dual_control_and_the_requester_cannot_take_part():
    req = oc.receive(_open(), _resp(), registered_contacts=CONTACTS, verify=oc.hmac_verifier(KEY))
    for approvals, code in (
        ({"rev1": True}, "SECOND_REVIEWER_REQUIRED"),
        ({"ops1": True, "rev2": True}, "REQUESTER_CANNOT_REVIEW"),
    ):
        with pytest.raises(oc.OutreachError) as e:
            oc.review(req, approvals)
        assert e.value.code == code
    assert oc.review(req, {"rev1": True, "rev2": False}).state is oc.State.REJECTED


def test_glb_13_silence_expires_to_unverifiable_never_a_finding():
    req = oc.expire(_open(), at(days=15))
    assert req.state is oc.State.EXPIRED and oc.verdict(req) == (
        "UNVERIFIABLE",
        ("ISSUER_DID_NOT_ANSWER",),
    )
    assert oc.expire(_open(), at(days=1)).state is oc.State.PENDING
    assert oc.verdict(_open()) == ("PENDING", ())


def test_an_issuer_denial_is_a_denial_only_after_dual_review():
    req = oc.receive(
        _open(), _resp(outcome="DENIED"), registered_contacts=CONTACTS, verify=lambda p, s: True
    )
    assert oc.verdict(req)[0] == "PENDING"  # not decided by one party's say-so
    assert oc.verdict(oc.review(req, {"a": True, "b": True})) == (
        "NOT_CONFIRMED",
        ("ISSUER_DENIED",),
    )


def test_a_second_answer_cannot_replace_the_first():
    req = oc.receive(_open(), _resp(), registered_contacts=CONTACTS, verify=oc.hmac_verifier(KEY))
    with pytest.raises(oc.OutreachError) as e:
        oc.receive(req, _resp(), registered_contacts=CONTACTS, verify=oc.hmac_verifier(KEY))
    assert e.value.code == "REQUEST_NOT_PENDING"


# --- GID-058 / 060 -----------------------------------------------------------------------------------------------------


def _ev(
    i: str, status: CredentialStatus, seq: int | None = None, when: datetime | None = None
) -> lc.SourceEvent:
    return lc.SourceEvent(i, "c1", status, when or T0, seq)


def test_glb_10_a_status_change_after_a_pass_is_applied_and_reported():
    s = lc.LifecycleState("c1")
    a = lc.apply_event(s, _ev("e1", CredentialStatus.ACTIVE, 1))
    assert a.changed and a.state.status is CredentialStatus.ACTIVE
    b = lc.apply_event(a.state, _ev("e2", CredentialStatus.SUSPENDED, 2))
    assert b.changed and b.applied and b.state.status is CredentialStatus.SUSPENDED


def test_glb_26_a_late_older_update_cannot_undo_a_newer_status_and_is_kept_in_history():
    s = lc.apply_event(lc.LifecycleState("c1"), _ev("e2", CredentialStatus.SUSPENDED, 5)).state
    late = lc.apply_event(s, _ev("e1", CredentialStatus.ACTIVE, 3))
    assert not late.applied and not late.changed and late.state.status is CredentialStatus.SUSPENDED
    assert late.state.history == (("e2", "SUSPENDED", True), ("e1", "ACTIVE", False))


def test_ordering_falls_back_to_observation_time_without_sequences():
    s = lc.apply_event(
        lc.LifecycleState("c1"), _ev("e2", CredentialStatus.REVOKED, when=at(hours=2))
    ).state
    assert not lc.apply_event(s, _ev("e1", CredentialStatus.ACTIVE, when=at(hours=1))).applied
    assert lc.apply_event(s, _ev("e3", CredentialStatus.ACTIVE, when=at(hours=3))).applied


def test_the_same_event_twice_is_one_event():
    a = lc.apply_event(lc.LifecycleState("c1"), _ev("e1", CredentialStatus.ACTIVE, 1))
    again = lc.apply_event(
        a.state, _ev("e1", CredentialStatus.REVOKED, 9)
    )  # same id, different content: ignored
    assert again.state is a.state and not again.changed and not again.applied


def test_an_event_for_another_claim_is_refused():
    with pytest.raises(ValueError):
        lc.apply_event(
            lc.LifecycleState("c1"), lc.SourceEvent("e", "other", CredentialStatus.ACTIVE, T0)
        )


def test_a_status_maps_to_the_dimension_it_feeds_and_downgrades_it():
    assert lc.CLAIM_DIMENSION["PROFESSIONAL_LICENSE"] is Dimension.ELIGIBILITY
    assert lc.CLAIM_DIMENSION["INSURANCE_ASSERTION"] is Dimension.INSURANCE
    assert lc.dimension_status("INSURANCE_ASSERTION", CredentialStatus.ACTIVE) is DimStatus.VERIFIED
    assert lc.dimension_status("INSURANCE_ASSERTION", CredentialStatus.EXPIRED) is DimStatus.EXPIRED
    for status in (CredentialStatus.SUSPENDED, CredentialStatus.REVOKED, CredentialStatus.UNKNOWN):
        assert lc.dimension_status("PROFESSIONAL_LICENSE", status) is DimStatus.NOT_VERIFIED


def test_due_for_recheck_lists_expired_expiring_and_stale_earliest_first():
    items = [
        lc.Tracked("fresh", "X", at(days=200), at(days=-1)),
        lc.Tracked("expiring", "X", at(days=5), at(days=-1)),
        lc.Tracked("expired", "X", at(days=-2), at(days=-10)),
        lc.Tracked("stale", "X", None, at(days=-400)),
    ]
    got = lc.due_for_recheck(items, now=T0, lead=timedelta(days=30), freshness=timedelta(days=365))
    assert got == [("stale", "STALE"), ("expired", "EXPIRED"), ("expiring", "EXPIRING")]


def test_an_unreachable_source_never_flips_a_status_it_only_loses_confirmation():
    s = lc.apply_event(lc.LifecycleState("c1"), _ev("e1", CredentialStatus.ACTIVE, 1)).state
    assert lc.unreachable(s, last_confirmed=T0, now=at(hours=1), grace=timedelta(hours=24)) == (
        CredentialStatus.ACTIVE,
        True,
    )
    assert lc.unreachable(s, last_confirmed=T0, now=at(days=3), grace=timedelta(hours=24)) == (
        CredentialStatus.ACTIVE,
        False,
    )
