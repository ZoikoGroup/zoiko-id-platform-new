"""A licensed hit is stored with what the PEP dates say and whether a media article really links to the subject (CR-118): a name alone is never a strong match."""

from __future__ import annotations

import json
from datetime import date

from zid_platform.modules.screening.application.licensed import LicensedHit, hit_row
from zid_platform.modules.screening.domain import coverage as cov
from zid_platform.modules.screening.domain.lists import PartyKind
from zid_platform.modules.screening.domain.matching import Party

TODAY = date(2026, 11, 18)
SUBJECT = Party(
    "p1",
    PartyKind.PERSON,
    "Eric Badege",
    birth="1970-05-04",
    countries=("GB",),
    identifiers=("AB123456",),
)


def reasons(row: dict) -> list[str]:
    return json.loads(str(row["reasons"]))


def test_a_pep_hit_carries_the_dates_status_and_never_lowers_the_strength() -> None:
    hit = LicensedHit("pep-1", "Eric Badege", 0.99, {"office": "Minister", "ended": "2021-03-01"})
    row = hit_row("vendor-a", cov.Klass.PEP_RCA, "v1", hit, subject=SUBJECT, today=TODAY)
    assert "PEP_FORMER" in reasons(row) and "PEP_NOTE_FORMER_RISK_NOT_REDUCED" in reasons(row)
    assert (
        row["strength"] == "STRONG"
    )  # being former is a fact about dates, not a reason to soften the match


def test_a_media_hit_with_only_the_name_is_never_strong_and_says_so() -> None:
    hit = LicensedHit("art-1", "Eric Badege", 0.99, {"headline": "x"})
    row = hit_row("vendor-a", cov.Klass.ADVERSE_MEDIA, "v1", hit, subject=SUBJECT, today=TODAY)
    assert "ARTICLE_NAME_ONLY" in reasons(row) and row["strength"] == "POSSIBLE"


def test_a_media_hit_that_contradicts_the_subject_is_flagged_and_not_strong() -> None:
    hit = LicensedHit("art-2", "Eric Badege", 0.99, {"birth": "1983-01-01", "country": "GB"})
    row = hit_row("vendor-a", cov.Klass.ADVERSE_MEDIA, "v1", hit, subject=SUBJECT, today=TODAY)
    assert "ARTICLE_CONTRADICTED" in reasons(row) and row["strength"] == "POSSIBLE"


def test_a_corroborated_media_hit_keeps_its_strength() -> None:
    hit = LicensedHit("art-3", "Eric Badege", 0.99, {"birth": "1970-05-04", "country": "gb"})
    row = hit_row("vendor-a", cov.Klass.ADVERSE_MEDIA, "v1", hit, subject=SUBJECT, today=TODAY)
    assert "ARTICLE_LINKED" in reasons(row) and row["strength"] == "STRONG"


def test_without_a_subject_or_date_the_row_is_as_before() -> None:
    hit = LicensedHit("x", "Someone", 0.5, {})
    row = hit_row("vendor-a", cov.Klass.PEP_RCA, "v1", hit)
    assert reasons(row) == ["LICENSED_PROVIDER_CANDIDATE"] and row["strength"] == "POSSIBLE"
