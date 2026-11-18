"""The authenticity evaluation gate (T24, T25; CR-125): no policy never passes, synthetic samples never count, an attack not clearly failed is an attack accepted, and each document type is judged alone."""

from __future__ import annotations

import pytest
from zid_platform.modules.verification.domain import authenticity_evaluation as ae

KINDS = frozenset({"PRINT_COPY", "SCREEN_REPLAY", "TEXT_EDIT"})
POLICY = ae.Policy(
    min_genuine=3,
    min_attacks=3,
    max_attack_accept_rate=0.0,
    max_genuine_reject_rate=0.34,
    max_genuine_inconclusive_rate=0.34,
    required_attack_kinds=KINDS,
    min_devices=2,
)


def s(
    doc="GB-PASSPORT",
    check="TEMPLATE",
    genuine=True,
    kind=None,
    source="SPECIMEN",
    device="Pixel 8",
    verdict="PASS",
) -> ae.Sample:
    return ae.Sample(doc, check, genuine, kind, source, device, verdict)


def good() -> list[ae.Sample]:
    return [
        s(), s(device="iPhone 15"), s(device="Pixel 8"),
        s(genuine=False, kind="PRINT_COPY", verdict="FAIL"), s(genuine=False, kind="SCREEN_REPLAY", verdict="FAIL", device="iPhone 15"), s(genuine=False, kind="TEXT_EDIT", verdict="FAIL"),
    ]  # fmt: skip


def one(results: list[ae.Result], doc="GB-PASSPORT", check="TEMPLATE") -> ae.Result:
    return next(r for r in results if r.document_type == doc and r.check == check)


def test_a_sample_is_strictly_formed() -> None:
    for bad in (
        {"check": "MAGIC"},
        {"verdict": "MAYBE"},
        {"genuine": True, "kind": "PRINT_COPY"},
        {"genuine": False, "kind": None},
        {"genuine": False, "kind": "WISHFUL"},
        {"source": "IMAGINED"},
        {"device": " "},
    ):
        with pytest.raises(ae.EvaluationError):
            s(**bad)


def test_with_no_policy_recorded_nothing_passes_and_nothing_may_be_switched_on() -> None:
    results = ae.evaluate(good(), None)
    assert (
        one(results).status is ae.Status.NOT_CONFIGURED
        and ae.may_switch_on(results, "GB-PASSPORT", "TEMPLATE") is False
    )


def test_enough_real_evidence_that_every_attack_was_failed_passes_that_pair_only() -> None:
    results = ae.evaluate(good(), POLICY, wanted=[("FR-ID", "TEMPLATE")])
    assert (
        one(results).status is ae.Status.PASS
        and ae.may_switch_on(results, "GB-PASSPORT", "TEMPLATE") is True
    )
    assert (
        ae.may_switch_on(results, "GB-PASSPORT", "TAMPER") is False
    )  # a different check was never tried
    fr = one(results, "FR-ID")
    assert (
        fr.status is ae.Status.FAIL and "TOO_FEW_GENUINE_DOCUMENTS" in fr.reasons
    )  # a pair nobody tested is reported as untested, not absent


def test_synthetic_samples_never_count() -> None:
    padded = [s(source="SYNTHETIC") for _ in range(50)] + [
        s(genuine=False, kind="PRINT_COPY", source="SYNTHETIC", verdict="FAIL") for _ in range(50)
    ]
    r = one(ae.evaluate(padded, POLICY))
    assert (
        r.status is ae.Status.FAIL
        and r.counts["genuine"] == 0
        and r.counts["synthetic_ignored"] == 100
    )


def test_an_attack_that_was_not_clearly_failed_is_an_attack_accepted() -> None:
    for verdict in ("PASS", "INCONCLUSIVE"):
        items = good()[:5] + [s(genuine=False, kind="TEXT_EDIT", verdict=verdict)]
        r = one(ae.evaluate(items, POLICY))
        assert r.status is ae.Status.FAIL and "ATTACKS_ACCEPTED_ABOVE_LIMIT" in r.reasons, verdict


def test_a_check_that_rejects_or_cannot_judge_too_many_real_documents_fails() -> None:
    rejecting = [s(verdict="FAIL"), s(verdict="FAIL", device="iPhone 15"), s()] + good()[3:]
    assert "GENUINE_REJECTED_ABOVE_LIMIT" in one(ae.evaluate(rejecting, POLICY)).reasons
    unsure = [
        s(verdict="INCONCLUSIVE"),
        s(verdict="INCONCLUSIVE", device="iPhone 15"),
        s(),
    ] + good()[3:]
    assert "GENUINE_INCONCLUSIVE_ABOVE_LIMIT" in one(ae.evaluate(unsure, POLICY)).reasons


def test_every_named_attack_kind_and_enough_phones_are_needed() -> None:
    no_replay = [x for x in good() if x.attack_kind != "SCREEN_REPLAY"] + [
        s(genuine=False, kind="PRINT_COPY", verdict="FAIL")
    ]
    assert "ATTACK_KIND_NOT_TRIED:SCREEN_REPLAY" in one(ae.evaluate(no_replay, POLICY)).reasons
    one_phone = [
        ae.Sample(
            x.document_type, x.check, x.genuine, x.attack_kind, x.source, "Pixel 8", x.verdict
        )
        for x in good()
    ]
    assert "TOO_FEW_DEVICE_MODELS" in one(ae.evaluate(one_phone, POLICY)).reasons


def test_each_document_type_is_judged_alone() -> None:
    other = [
        ae.Sample("DE-ID", x.check, x.genuine, x.attack_kind, x.source, x.device_model, "PASS")
        for x in good()
    ]  # the same checks, but they accept every attack on this document
    results = ae.evaluate([*good(), *other], POLICY)
    assert (
        one(results, "GB-PASSPORT").status is ae.Status.PASS
        and one(results, "DE-ID").status is ae.Status.FAIL
    )


def test_a_results_file_is_read_strictly() -> None:
    row = {
        "document_type": "GB-PASSPORT",
        "check": "template",
        "genuine": False,
        "attack_kind": "print_copy",
        "source": "live",
        "device_model": "Pixel 8",
        "verdict": "fail",
    }
    parsed = ae.parse_samples([row])
    assert (
        parsed[0].check == "TEMPLATE"
        and parsed[0].attack_kind == "PRINT_COPY"
        and parsed[0].source == "LIVE"
    )
    for bad in (
        {**row, "surprise": 1},
        {k: v for k, v in row.items() if k != "verdict"},
        {**row, "genuine": "no"},
        {**row, "verdict": "MAYBE"},
    ):
        with pytest.raises(ae.EvaluationError):
            ae.parse_samples([bad])
    with pytest.raises(ae.EvaluationError):
        ae.parse_samples({"not": "a list"})
