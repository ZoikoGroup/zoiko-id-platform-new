"""PEP screening, adverse media and velocity signals (CR-068; ZID-GAP-001 WP-14): a PEP match goes to a person, adverse media only
adds, a failing provider is a review, velocity thresholds have exact window edges, and a stale PEP list blocks a high-risk approval."""

from __future__ import annotations

import asyncio
from datetime import UTC, datetime, timedelta
from decimal import Decimal
from typing import Any

import pytest
from zid_platform.modules.screening.application.signals import check_adverse_media
from zid_platform.modules.screening.domain.decision import (
    AuthorityEvidence,
    AuthorityKind,
    Policy,
    Role,
    ScreeningInput,
    ScreeningStatus,
    Subject,
    decide,
)
from zid_platform.modules.screening.domain.lists import Entry, ListId, PartyKind, Snapshot
from zid_platform.modules.screening.domain.matching import Party
from zid_platform.modules.screening.domain.ownership import (
    CountryPack,
    Edge,
    Graph,
    Holder,
    HolderKind,
)
from zid_platform.modules.screening.domain.signals import (
    DEFAULT_RULES,
    Action,
    AdverseMediaResult,
    Dimension,
    MediaOutcome,
    RiskTier,
    apply_adverse_media,
    check_signal_resolution,
    evaluate_velocity,
    hash_identifier,
    in_window,
)
from zid_platform.modules.screening.infrastructure.adverse_media_fake import FakeAdverseMedia
from zid_platform.modules.screening.infrastructure.config import config, parse
from zid_platform.modules.screening.infrastructure.parsers import ListFormatError, parse_pep

NOW = datetime(2026, 11, 10, 12, 0, tzinfo=UTC)
SANCTIONS = (ListId.OFAC_SDN,)
GB = CountryPack("GB", "g1", None, None, ())
OFFICER = AuthorityEvidence(AuthorityKind.REGISTER_OFFICER, "officer-entry-1")


def snap(list_id: ListId, *entries: Entry, age: timedelta = timedelta(hours=1)) -> Snapshot:
    return Snapshot(list_id, "v1", "h" * 64, NOW - age, entries)


def sdn() -> Snapshot:
    return snap(ListId.OFAC_SDN, Entry(ListId.OFAC_SDN, "1", PartyKind.PERSON, "Zed Nobody"))


def pep(age: timedelta = timedelta(hours=1)) -> Snapshot:
    e = Entry(
        ListId.PEP, "pep-7", PartyKind.PERSON, "Rita Minister", countries=("gb",),
        programs=("LEVEL:NATIONAL", "POSITION:Minister of Finance"),
    )  # fmt: skip
    return snap(ListId.PEP, e, age=age)


def inputs(rep: str = "Alice Smith", *, snaps: tuple[Snapshot, ...], **over: Any) -> ScreeningInput:
    graph = Graph(
        "co",
        {"co": Holder("co", HolderKind.ENTITY), "o": Holder("o", HolderKind.PERSON)},
        (Edge("o", "co", Decimal(60)),),
    )
    subjects = (
        Subject(Role.ENTITY, Party("co", PartyKind.ENTITY, "Acme Widgets Limited")),
        Subject(Role.REPRESENTATIVE, Party("rep", PartyKind.PERSON, rep)),
        Subject(Role.OWNER, Party("o", PartyKind.PERSON, "Olivia Owner")),
    )  # fmt: skip
    policy = Policy(
        "p1",
        SANCTIONS,
        timedelta(hours=48),
        timedelta(days=365),
        timedelta(days=90),
        high_risk_required_lists=(ListId.PEP,),
    )
    base: dict[str, Any] = {
        "country": "GB", "subjects": subjects, "graph": graph, "authority": OFFICER, "elevated_risk": False,
        "snapshots": {s.list_id: s for s in snaps}, "pack": GB, "policy": policy, "now": NOW,
    }  # fmt: skip
    return ScreeningInput(**{**base, **over})


# ---- PEP -------------------------------------------------------------------------------------------------------------


def test_a_pep_match_routes_to_review_with_the_list_version_and_score_never_reject_or_verify() -> (
    None
):
    d = decide(inputs("Rita Minister", snaps=(sdn(), pep()), elevated_risk=True))
    assert d.status is ScreeningStatus.REVIEW_REQUIRED  # a person decides
    assert d.status is not ScreeningStatus.REJECTED
    assert {"SCREENING_ALERT", "PEP_MATCH"} <= set(d.reasons)
    m = d.matches[0]
    assert m.list_id is ListId.PEP and m.list_version == "v1" and m.score >= 0.88 and m.reasons
    assert "LEVEL:NATIONAL" in m.programs  # position and level reach the reviewer
    assert ("PEP", "v1", "h" * 64) in d.lists


def test_no_pep_match_on_a_fresh_list_is_a_clear_high_risk_approval_path() -> None:
    d = decide(inputs(snaps=(sdn(), pep()), elevated_risk=True))
    assert d.status is ScreeningStatus.VERIFIED and ("PEP", "v1", "h" * 64) in d.lists


def test_a_pep_match_does_not_make_an_ownership_chain_look_sanctioned() -> None:
    pack = CountryPack("GB", "g1", None, None, ("ofac_50",))
    d = decide(inputs("Olivia Owner", snaps=(sdn(), pep()), elevated_risk=True, pack=pack))
    assert d.status is ScreeningStatus.VERIFIED
    pep_owner = Snapshot(
        ListId.PEP, "v1", "h" * 64, NOW, (Entry(ListId.PEP, "p", PartyKind.PERSON, "Olivia Owner"),)
    )
    d = decide(inputs(snaps=(sdn(), pep_owner), elevated_risk=True, pack=pack))
    assert d.status is ScreeningStatus.REVIEW_REQUIRED and not any(
        r.startswith("OWNERSHIP_SANCTIONED") for r in d.reasons
    )


def test_a_stale_or_missing_pep_list_blocks_a_high_risk_approval_with_a_reason_code() -> None:
    stale = decide(inputs(snaps=(sdn(), pep(age=timedelta(hours=49))), elevated_risk=True))
    assert stale.status is ScreeningStatus.UNAVAILABLE
    assert "LIST_STALE:PEP" in stale.reasons and "HIGH_RISK_APPROVAL_BLOCKED" in stale.reasons
    missing = decide(inputs(snaps=(sdn(),), elevated_risk=True))
    assert (
        missing.status is ScreeningStatus.UNAVAILABLE and "LIST_UNAVAILABLE:PEP" in missing.reasons
    )
    # the same stale list does not block an ordinary-risk case, and a stale required sanctions list still does
    assert (
        decide(inputs(snaps=(sdn(), pep(age=timedelta(hours=49))))).status
        is ScreeningStatus.VERIFIED
    )
    old_sdn = snap(
        ListId.OFAC_SDN,
        Entry(ListId.OFAC_SDN, "1", PartyKind.PERSON, "Zed Nobody"),
        age=timedelta(hours=60),
    )
    assert (
        "HIGH_RISK_APPROVAL_BLOCKED"
        not in decide(inputs(snaps=(old_sdn, pep()), elevated_risk=True)).reasons
    )


def test_pep_is_off_by_default_in_the_shipped_policy() -> None:
    cfg = config()
    assert ListId.PEP not in cfg.policy.required_lists and cfg.policy.high_risk_required_lists == ()
    assert not cfg.sources[
        ListId.PEP
    ].configured  # no download source until the owners choose a licensed one
    on = parse({"policy": {"required_lists": ["OFAC_SDN"], "high_risk_required_lists": ["PEP"]}})
    assert on.policy.high_risk_required_lists == (ListId.PEP,)


PEP_CSV = (
    b"source_ref,name,aliases,birth,country,position,level\n"
    b"p1,Rita Minister,R. Minister|Rita M,1961-04-02,GB,Minister of Finance,NATIONAL\n"
    b"p2,Omar Mayor,,1975,US,Mayor,LOCAL\n"
)


def test_the_pep_csv_and_json_formats_parse_to_person_entries_and_bad_files_are_refused_whole() -> (
    None
):
    rows = parse_pep(PEP_CSV)
    assert [r.source_ref for r in rows] == ["p1", "p2"] and rows[0].aliases == (
        "R. Minister",
        "Rita M",
    )
    assert (
        rows[0].birth == ("1961",)
        and rows[0].countries == ("gb",)
        and "LEVEL:NATIONAL" in rows[0].programs
    )
    assert all(r.list_id is ListId.PEP and r.kind is PartyKind.PERSON for r in rows)
    js = b'{"entries": [{"source_ref": "j1", "name": "Ana Senator", "aliases": ["A. Senator"], "country": "ES", "level": "NATIONAL"}]}'
    assert parse_pep(js)[0].primary_name == "Ana Senator"
    for bad in (
        b"name,level\nx,NATIONAL\n",
        PEP_CSV + b"p3,Bad Level,,,GB,Clerk,SOMETHING\n",
        PEP_CSV + b"p1,Dup,,,GB,Clerk,LOCAL\n",
        b"source_ref,name,aliases,birth,country,position,level\n",
        b"{not json",
    ):
        with pytest.raises(ListFormatError):
            parse_pep(bad)


# ---- adverse media ---------------------------------------------------------------------------------------------------


def result(outcome: MediaOutcome, **kw: Any) -> AdverseMediaResult:
    return AdverseMediaResult("p", NOW, outcome, **kw)


def test_adverse_media_can_only_add_a_review_or_raise_the_tier_never_clear() -> None:
    clean = apply_adverse_media(RiskTier.HIGH, result(MediaOutcome.NO_HITS))
    assert (
        not clean.review_required and clean.tier is RiskTier.HIGH and clean.cleared is False
    )  # nothing added, nothing lowered
    hit = apply_adverse_media(
        RiskTier.LOW, result(MediaOutcome.HITS, hit_count=2, severity=RiskTier.HIGH)
    )
    assert hit.review_required and hit.tier is RiskTier.HIGH and not hit.cleared
    mild = apply_adverse_media(
        RiskTier.HIGH, result(MediaOutcome.HITS, hit_count=1, severity=RiskTier.LOW)
    )
    assert mild.review_required and mild.tier is RiskTier.HIGH  # never lowered
    bogus = apply_adverse_media(RiskTier.LOW, result(MediaOutcome.HITS, hit_count=0))
    assert bogus.review_required and "ADVERSE_MEDIA_RESULT_INVALID" in bogus.reasons


@pytest.mark.parametrize("outcome", [MediaOutcome.ERROR, MediaOutcome.TIMEOUT])
def test_a_failed_check_is_a_review_never_a_pass(outcome: MediaOutcome) -> None:
    e = apply_adverse_media(RiskTier.LOW, result(outcome))
    assert (
        e.review_required
        and e.tier is RiskTier.ELEVATED
        and "ADVERSE_MEDIA_NOT_CHECKED" in e.reasons
        and not e.cleared
    )


class FakeConn:  # check_adverse_media only reaches the database when it has something to record
    pass


@pytest.mark.parametrize(
    ("fake", "code"),
    [
        (FakeAdverseMedia(raises=RuntimeError("boom")), "ADVERSE_MEDIA_ERROR"),
        (FakeAdverseMedia(hangs=True), "ADVERSE_MEDIA_TIMEOUT"),
    ],
)
def test_a_provider_exception_or_timeout_fails_safe_into_review(
    fake: FakeAdverseMedia, code: str, monkeypatch: pytest.MonkeyPatch
) -> None:
    from zid_platform.modules.screening.application import signals

    seen: list[str] = []

    async def fake_raise(conn: Any, tenant_id: str, **kw: Any) -> Any:
        seen.append(kw["rule"])
        return "RAISED"

    monkeypatch.setattr(signals, "_raise", fake_raise)
    effect, raised = asyncio.run(
        check_adverse_media(
            FakeConn(), "t1", subject_ref="s1", party=Party("rep", PartyKind.PERSON, "Alice Smith"),  # type: ignore[arg-type]
            port=fake, current_tier=RiskTier.LOW, now=NOW, timeout_s=0.05,
        )
    )  # fmt: skip
    assert effect.review_required and not effect.cleared and raised == "RAISED" and seen == [code]
    assert fake.calls == [
        ("s1", "rep")
    ]  # the provider is asked with references, and the fake recorded them


# ---- velocity --------------------------------------------------------------------------------------------------------


def rule(dim: Dimension, action: Action):  # type: ignore[no-untyped-def]
    return next(r for r in DEFAULT_RULES if r.dimension is dim and r.action is action)


def test_a_velocity_count_below_the_threshold_raises_nothing_and_at_it_raises_with_the_counts() -> (
    None
):
    r = rule(Dimension.DEVICE, Action.SIGNUP)
    key = (r.dimension, r.action, r.window_seconds)
    assert evaluate_velocity({key: r.threshold - 1}) == []
    (b,) = evaluate_velocity({key: r.threshold})
    assert b.rule == r.name and b.count == r.threshold and b.threshold == r.threshold
    assert b.detail()["window_seconds"] == 86400 and b.tier is RiskTier.ELEVATED


def test_the_window_edges_are_exact() -> None:
    w = timedelta(hours=1)
    assert in_window(NOW, NOW, w)  # the current attempt counts
    assert in_window(NOW - w + timedelta(seconds=1), NOW, w)
    assert not in_window(NOW - w, NOW, w)  # exactly a window old has aged out
    assert not in_window(NOW + timedelta(seconds=1), NOW, w)  # the future never counts


def test_identifiers_are_hashed_tenant_scoped_and_never_raw() -> None:
    a = hash_identifier("Person@Example.com ", tenant_id="t1", kind="contact")
    assert len(a) == 64 and "example" not in a
    assert a == hash_identifier("person@example.com", tenant_id="t1", kind="contact")  # normalized
    assert a != hash_identifier("person@example.com", tenant_id="t2", kind="contact")
    assert a != hash_identifier(
        "person@example.com", tenant_id="t1", kind="contact", pepper=b"secret"
    )


def test_a_signal_resolution_needs_an_outcome_a_reason_and_evidence() -> None:
    ok = (
        "CLEARED_BY_REVIEWER",
        "Checked the passport scan and the device log.",
        "doc:evidence-17",
        "OPEN",
    )
    assert check_signal_resolution(*ok) is None
    assert check_signal_resolution("MAYBE", *ok[1:]) == "RULING_OUTCOME_INVALID"
    assert check_signal_resolution(ok[0], "short", ok[2], "OPEN") == "RATIONALE_REQUIRED"
    assert check_signal_resolution(ok[0], ok[1], " ", "OPEN") == "EVIDENCE_REF_REQUIRED"
    assert check_signal_resolution(*ok[:3], "RESOLVED") == "SIGNAL_ALREADY_RESOLVED"
