"""DTV-20, DTV-21, DTV-22: phone and email shape, keyed hashing, shared devices and the link graph. Synthetic identifiers only."""

from __future__ import annotations

import random

import pytest
from zid_platform.modules.verification.domain import identity_signals as sig

PEPPER = b"unit-test-pepper"
K = sig.Kind


def obs(subject: str, kind: sig.Kind, ident: str) -> sig.Observation:
    return sig.Observation(subject, kind, ident)


@pytest.mark.parametrize(
    ("raw", "expected"),
    [
        ("+44 20 7946 0958", "+442079460958"),
        ("+1 (415) 555-0132", "+14155550132"),
        ("+81.3.1234.5678", "+81312345678"),
    ],
)
def test_a_phone_number_is_normalized_to_e164_shape_only(raw, expected):
    assert sig.normalize_phone(raw).e164 == expected
    assert "SHAPE_ONLY_NOT_REACHABILITY" in sig.normalize_phone(raw).notes


@pytest.mark.parametrize(
    ("raw", "code"),
    [
        ("020 7946 0958", "PHONE_NEEDS_COUNTRY_CODE"),
        ("+0123456789", "PHONE_INVALID"),
        ("+12345", "PHONE_INVALID"),
        ("+1234567890123456", "PHONE_INVALID"),
        ("call me", "PHONE_INVALID"),
        ("", "PHONE_INVALID"),
    ],
)
def test_a_bad_phone_number_is_refused_with_a_code(raw, code):
    with pytest.raises(sig.SignalError, match=code):
        sig.normalize_phone(raw)


def test_an_email_is_normalized_and_flagged_without_being_called_deliverable():
    m = sig.normalize_email("Dana.Lee+news@Example.COM.")
    assert m.normalized == "Dana.Lee+news@example.com" and m.domain == "example.com"
    assert m.plus_addressed and not m.role_account and not m.disposable
    assert "SHAPE_ONLY_NOT_DELIVERABILITY" in m.notes
    assert sig.normalize_email("INFO@example.com").role_account
    assert sig.normalize_email(
        "a@mail.test", disposable_domains=frozenset({"mail.test"})
    ).disposable
    # no list ships, so nothing is flagged disposable until an operator supplies one
    assert not sig.normalize_email("a@mail.test").disposable


@pytest.mark.parametrize(
    "raw",
    [
        "",
        "no-at-sign",
        "a@@b.com",
        "a@b",
        "@b.com",
        "a@-b.com",
        "a@b..com",
        "a b@c.com",
        "a@1.2.3.4",
        "x" * 65 + "@b.com",
        "a@" + "b" * 64 + ".com",
    ],
)
def test_a_malformed_email_is_refused(raw):
    with pytest.raises(sig.SignalError, match="EMAIL_INVALID"):
        sig.normalize_email(raw)


def test_the_hash_is_keyed_and_never_links_across_companies_or_kinds():
    a = sig.identifier_hash("+442079460958", tenant_id="t1", kind=K.PHONE, pepper=PEPPER)
    assert a == sig.identifier_hash("+442079460958", tenant_id="t1", kind=K.PHONE, pepper=PEPPER)
    assert a != sig.identifier_hash("+442079460958", tenant_id="t2", kind=K.PHONE, pepper=PEPPER)
    assert a != sig.identifier_hash("+442079460958", tenant_id="t1", kind=K.ADDRESS, pepper=PEPPER)
    assert a != sig.identifier_hash("+442079460958", tenant_id="t1", kind=K.PHONE, pepper=b"other")
    assert "442079460958" not in a
    with pytest.raises(sig.SignalError, match="PEPPER_REQUIRED"):
        sig.identifier_hash("x", tenant_id="t1", kind=K.PHONE, pepper=b"")


def test_a_provider_that_ships_does_not_exist_and_says_so():
    a = sig.assess_phone("+442079460958")
    assert a.shape_ok and not a.provider.available
    assert a.provider.reasons == ("NO_PROVIDER_CONFIGURED",)
    assert a.provider.risk is None  # no provider, no risk figure: none is invented
    e = sig.assess_email("info@example.com")
    assert e.shape_ok and "ROLE_ACCOUNT" in e.flags and not e.provider.available


def test_a_provider_that_raises_is_unavailable_never_a_pass_or_a_fail():
    class Broken:
        def check(self, kind, normalized, *, country):
            raise ConnectionError("down")

    a = sig.assess_phone("+442079460958", provider=Broken())
    assert a.provider == sig.ProviderAnswer(False, None, ("PROVIDER_FAILED",), "")

    class Fine:
        def check(self, kind, normalized, *, country):
            assert normalized == "+442079460958"  # the provider sees the normalized value
            return sig.ProviderAnswer(True, "LOW", ("KNOWN_LINE",), "acme")

    assert sig.assess_phone("+44 20 7946 0958", provider=Fine()).provider.risk == "LOW"


def test_a_bad_shape_is_reported_and_the_provider_is_not_asked():
    class Never:
        def check(self, kind, normalized, *, country):
            raise AssertionError("a malformed value must not reach a provider")

    assert sig.assess_phone("garbage", provider=Never()).flags == ("PHONE_INVALID",)
    assert sig.assess_email("garbage", provider=Never()).shape_ok is False


def test_a_device_shared_by_many_people_is_flagged_but_a_hub_and_a_family_are_not():
    def crowd(n: int) -> list[sig.Observation]:
        return [obs(f"p{i:03d}", K.DEVICE, f"d-{n}") for i in range(n)]

    found = sig.shared_devices([*crowd(2), *crowd(4), *crowd(5)])
    assert [d.device for d in found] == ["d-5"]  # a family of 2 or 4 is ordinary
    assert found[0].recommendation == "REVIEW" and len(found[0].subjects) == 5
    assert sig.shared_devices(crowd(sig.MAX_FANOUT + 1)) == []  # a kiosk says nothing about anyone
    assert [d.device for d in sig.shared_devices(crowd(sig.MAX_FANOUT))] == [f"d-{sig.MAX_FANOUT}"]


def test_people_sharing_one_identifier_are_a_weak_link_and_more_kinds_make_it_stronger():
    one = sig.link_graph([obs("a", K.PHONE, "ph1"), obs("b", K.PHONE, "ph1")])
    assert [(s.subjects, s.strength) for s in one] == [(("a", "b"), "WEAK")]
    two = sig.link_graph(
        [
            obs("a", K.PHONE, "ph1"),
            obs("b", K.PHONE, "ph1"),
            obs("a", K.DEVICE, "d1"),
            obs("b", K.DEVICE, "d1"),
        ]
    )
    assert two[0].strength == "MODERATE" and two[0].shared == (("DEVICE", 1), ("PHONE", 1))
    three = sig.link_graph(
        [obs(p, k, f"{k}-1") for p in ("a", "b") for k in (K.PHONE, K.DEVICE, K.ADDRESS)]
    )
    assert three[0].strength == "STRONG"


def test_a_link_is_never_a_verdict_and_says_so():
    s = sig.link_graph([obs("a", K.PHONE, "x"), obs("b", K.PHONE, "x")])[0]
    assert s.recommendation == "REVIEW" and "NOT PROOF" in s.notes[0]
    assert not any(hasattr(s, name) for name in ("fraud", "verdict", "score", "risk_score"))
    summary = sig.describe([s])
    assert summary["recommendation"] == "REVIEW" and "'x'" not in repr(summary)
    assert sig.describe([])["recommendation"] == "NONE"


def test_a_hub_identifier_connects_nobody():
    crowd = [obs(f"p{i:03d}", K.PHONE, "switchboard") for i in range(sig.MAX_FANOUT + 1)]
    assert sig.link_graph(crowd) == []
    # two people who share a real identifier AND a switchboard are still linked, by the real one only
    mixed = [*crowd, obs("p000", K.DEVICE, "d9"), obs("p001", K.DEVICE, "d9")]
    groups = sig.link_graph(mixed)
    assert [g.subjects for g in groups] == [("p000", "p001")]
    assert groups[0].shared == (("DEVICE", 1),)


def test_chains_join_into_one_group_and_separate_people_stay_apart():
    groups = sig.link_graph(
        [
            obs("a", K.PHONE, "1"),
            obs("b", K.PHONE, "1"),
            obs("b", K.EMAIL, "2"),
            obs("c", K.EMAIL, "2"),
            obs("x", K.PHONE, "9"),
            obs("y", K.PHONE, "9"),
            obs("z", K.PHONE, "alone"),
        ]
    )
    assert [g.subjects for g in groups] == [("a", "b", "c"), ("x", "y")]


def test_the_result_does_not_depend_on_the_order_the_observations_arrive_in():
    base = [
        obs("a", K.PHONE, "1"),
        obs("b", K.PHONE, "1"),
        obs("b", K.EMAIL, "2"),
        obs("c", K.EMAIL, "2"),
        obs("d", K.DEVICE, "3"),
        obs("e", K.DEVICE, "3"),
        obs("c", K.DEVICE, "3"),
    ]
    expected = sig.link_graph(base)
    for seed in range(20):
        shuffled = base[:]
        random.Random(seed).shuffle(shuffled)
        assert sig.link_graph(shuffled) == expected


def test_the_same_person_seen_twice_with_one_identifier_is_not_a_link():
    assert sig.link_graph([obs("a", K.PHONE, "1"), obs("a", K.PHONE, "1")]) == []
