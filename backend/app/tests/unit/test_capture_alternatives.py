"""GID-062 / GLB-30: a person who cannot use the camera is offered another way, with no reason asked and never a rejection."""

from __future__ import annotations

import pytest
from zid_platform.modules.verification.domain import capture_alternatives as alt
from zid_platform.modules.verification.infrastructure.capture_alternatives_registry import (
    capture_alternatives,
)


def _doc(**over):
    base = {
        "version": "t",
        "alternatives": [
            {
                "code": "A",
                "title": "a",
                "how": "h",
                "assurance_note": "n",
                "suits": ["CANNOT_USE_CAMERA"],
            },
            {
                "code": "B",
                "title": "b",
                "how": "h",
                "assurance_note": "n",
                "suits": ["NO_USABLE_CAMERA"],
                "needs_human": True,
            },
        ],
        "default": ["A", "B"],
        "countries": {},
    }
    return {**base, **over}


def test_the_shipped_configuration_offers_every_country_at_least_one_route() -> None:
    reg = capture_alternatives()
    assert reg.default and all(c in reg.alternatives for c in reg.default)
    for country in ("GB", "US", "ZZ"):
        assert alt.offer(reg, country).routes, f"{country} has no alternative"


def test_asking_needs_no_reason_and_is_never_a_failed_attempt_or_a_rejection() -> None:
    reg = capture_alternatives()
    for reason in (None, "CANNOT_USE_CAMERA", "PREFER_NOT_TO_SAY", "something unrecognised"):
        o = alt.offer(reg, "GB", reason)
        assert o.reason_required is False
        assert o.counts_as_failed_attempt is False and o.rejection_possible is False


def test_the_reason_only_orders_the_routes_and_an_unknown_reason_is_ignored_not_refused() -> None:
    reg = alt.parse(_doc())
    assert [a.code for a in alt.offer(reg, "GB", "NO_USABLE_CAMERA").routes] == ["B", "A"]
    assert [a.code for a in alt.offer(reg, "GB", "CANNOT_USE_CAMERA").routes] == ["A", "B"]
    assert [a.code for a in alt.offer(reg, "GB", "not-a-reason").routes] == ["A", "B"]
    assert [a.code for a in alt.offer(reg, "GB").routes] == ["A", "B"]


def test_every_configured_route_is_always_offered_whatever_the_reason() -> None:
    reg = alt.parse(_doc())
    for reason in (None, *sorted(alt.REASONS)):
        assert {a.code for a in alt.offer(reg, "GB", reason).routes} == {"A", "B"}


def test_a_country_can_offer_its_own_routes_and_the_country_code_is_case_insensitive() -> None:
    reg = alt.parse(_doc(countries={"fr": ["B"]}))
    assert [a.code for a in alt.offer(reg, "FR").routes] == ["B"]
    assert [a.code for a in alt.offer(reg, "fr").routes] == ["B"]
    assert [a.code for a in alt.offer(reg, "GB").routes] == ["A", "B"]


@pytest.mark.parametrize(
    ("doc", "code"),
    [
        (_doc(default=[]), "NO_ALTERNATIVE_FOR_COUNTRY"),
        (_doc(countries={"DE": []}), "NO_ALTERNATIVE_FOR_COUNTRY"),
        (_doc(countries={"DE": ["NOPE"]}), "ALTERNATIVE_UNKNOWN"),
        (_doc(alternatives=[]), "NO_ALTERNATIVES_DEFINED"),
    ],
)
def test_a_configuration_that_leaves_someone_with_no_other_way_does_not_load(
    doc, code: str
) -> None:
    with pytest.raises(alt.AlternativeError) as e:
        alt.parse(doc)
    assert e.value.code == code


def test_an_incomplete_duplicate_or_misclassified_route_is_refused() -> None:
    incomplete = _doc(alternatives=[{"code": "A", "title": "a"}])
    with pytest.raises(alt.AlternativeError) as e:
        alt.parse(incomplete)
    assert e.value.code == "ALTERNATIVE_INCOMPLETE"
    one = _doc()["alternatives"][0]
    with pytest.raises(alt.AlternativeError) as e2:
        alt.parse(_doc(alternatives=[one, one]))
    assert e2.value.code == "ALTERNATIVE_DUPLICATE"
    with pytest.raises(alt.AlternativeError) as e3:
        alt.parse(_doc(alternatives=[{**one, "suits": ["LOOKS_SUSPICIOUS"]}]))
    assert e3.value.code == "REASON_UNKNOWN"


def test_every_shipped_route_says_what_assurance_it_gives_in_plain_words() -> None:
    for a in capture_alternatives().alternatives.values():
        assert len(a.assurance_note) > 30 and len(a.how) > 30
        assert "suspicious" not in (a.how + a.assurance_note).lower()
