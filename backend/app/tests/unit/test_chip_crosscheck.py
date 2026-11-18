"""The independent chip cross-check: same document, same device, read and cryptographic reports kept apart, a pass where the reference failed is critical, and the gate never passes unconfigured (E03; CR-123)."""

from __future__ import annotations

import pytest
from zid_platform.modules.evidence.domain import chip_crosscheck as cc

SOD = "a" * 64
G1, G2 = "1" * 64, "2" * 64
GOOD = cc.CryptoReport("PASS", "TRUSTED", "CHECKED_GOOD", "PASS", "NOT_SUPPORTED")


def run(
    reader: str = "ZOIKO",
    *,
    doc: str = "lab-doc-1",
    device: str = "Pixel 8",
    access: str = "PACE_GM",
    sod: str = SOD,
    groups=None,
    crypto=GOOD,
) -> cc.ChipRun:
    return cc.ChipRun(
        reader,
        "1.0",
        device,
        "14",
        doc,
        access,
        cc.ReadReport(sod, groups or {1: G1, 2: G2}),
        crypto,
    )


def test_a_run_names_a_known_reader_and_access_and_a_document_and_device() -> None:
    with pytest.raises(cc.CrosscheckError):
        run("ACME")
    with pytest.raises(cc.CrosscheckError):
        run(access="MAGIC")
    with pytest.raises(cc.CrosscheckError):
        run(doc=" ")


def test_the_pair_must_be_ours_and_an_independent_reader() -> None:
    with pytest.raises(cc.CrosscheckError):
        cc.compare(run("JMRTD"), run("NFCPASSPORTREADER"))
    with pytest.raises(cc.CrosscheckError):
        cc.compare(run("ZOIKO"), run("ZOIKO"))  # our reader checked against itself proves nothing


def test_agreement_on_the_same_document_and_device_is_agreement_in_both_reports() -> None:
    out = cc.compare(run(), run("JMRTD"))
    assert out.comparable and out.read is cc.Agreement.AGREE and out.read_differences == ()
    assert (
        out.checks["passive_auth"] is cc.Agreement.AGREE
        and out.checks["chip_auth"] is cc.Agreement.NOT_COMPARABLE
    )  # neither supports it
    assert out.critical == () and out.ours_stricter == ()


def test_a_different_document_signed_object_or_device_is_not_a_comparison() -> None:
    assert cc.compare(run(), run("JMRTD", doc="lab-doc-2")).reason == "DIFFERENT_DOCUMENT_OR_SOD"
    assert cc.compare(run(), run("JMRTD", sod="b" * 64)).reason == "DIFFERENT_DOCUMENT_OR_SOD"
    assert (
        cc.compare(run(), run("NFCPASSPORTREADER", device="iPhone 15")).reason == "DIFFERENT_DEVICE"
    )
    assert cc.compare(run(), run("JMRTD", doc="lab-doc-2")).comparable is False


def test_the_read_report_disagrees_when_a_group_differs_or_only_one_reader_got_it() -> None:
    differs = cc.compare(run(), run("JMRTD", groups={1: G1, 2: "9" * 64}))
    assert differs.read is cc.Agreement.DISAGREE and differs.read_differences == (
        "DG2_BYTES_DIFFER",
    )
    one = cc.compare(run(groups={1: G1}), run("JMRTD"))
    assert one.read_differences == ("DG2_READ_BY_ONE_ONLY",)


def test_a_pass_where_the_reference_failed_is_critical_and_the_reverse_is_only_stricter() -> None:
    bad_ref = cc.CryptoReport("FAIL", "TRUSTED", "CHECKED_GOOD", "PASS")
    critical = cc.compare(run(), run("JMRTD", crypto=bad_ref))
    assert (
        critical.critical == ("passive_auth",)
        and critical.checks["passive_auth"] is cc.Agreement.DISAGREE
    )
    strict = cc.compare(run(crypto=cc.CryptoReport("FAIL", "UNTRUSTED")), run("JMRTD"))
    assert strict.critical == () and "passive_auth" in strict.ours_stricter


def test_a_check_one_side_did_not_run_is_not_comparable_never_agreement() -> None:
    out = cc.compare(
        run(crypto=cc.CryptoReport("PASS")), run("JMRTD", crypto=cc.CryptoReport("NOT_RUN"))
    )
    assert out.checks["passive_auth"] is cc.Agreement.NOT_COMPARABLE


POLICY = cc.GatePolicy(
    min_documents=2,
    min_devices=2,
    required_access=frozenset({"BAC", "PACE_GM"}),
    min_runs_per_access=2,
    required_references=frozenset({"JMRTD", "NFCPASSPORTREADER"}),
)


def pair(doc: str, device: str, access: str, reference: str, **kw) -> cc.Comparison:
    return cc.compare(
        run(
            doc=doc, device=device, access=access, **{k: v for k, v in kw.items() if k == "crypto"}
        ),
        run(
            reference,
            doc=doc,
            device=device,
            access=access,
            **{k: v for k, v in kw.items() if k == "ref_crypto"},
        )
        if "ref_crypto" not in kw
        else run(reference, doc=doc, device=device, access=access, crypto=kw["ref_crypto"]),
    )


def enough() -> list[cc.Comparison]:
    return [
        pair("d1", "Pixel 8", "BAC", "JMRTD"), pair("d1", "Pixel 8", "PACE_GM", "JMRTD"),
        pair("d2", "iPhone 15", "BAC", "NFCPASSPORTREADER"), pair("d2", "iPhone 15", "PACE_GM", "NFCPASSPORTREADER"),
    ]  # fmt: skip


def test_the_gate_never_passes_with_no_recorded_policy() -> None:
    g = cc.gate(enough(), None)
    assert g.status is cc.Status.NOT_CONFIGURED and g.reasons == ("NO_GATE_POLICY_RECORDED",)


def test_the_gate_passes_only_with_enough_documents_devices_protocols_and_both_references() -> None:
    assert cc.gate(enough(), POLICY).status is cc.Status.PASS
    assert "TOO_FEW_DOCUMENTS" in cc.gate(enough()[:2], POLICY).reasons
    only_jmrtd = [c for c in enough() if c.reference == "JMRTD"]
    reasons = cc.gate(only_jmrtd, POLICY).reasons
    assert "REFERENCE_NOT_USED:NFCPASSPORTREADER" in reasons and "TOO_FEW_DEVICE_MODELS" in reasons
    assert (
        "TOO_FEW_RUNS:PACE_GM"
        in cc.gate([c for c in enough() if c.access == "BAC"], POLICY).reasons
    )


def test_one_critical_disagreement_fails_the_gate_however_much_else_agrees() -> None:
    bad = pair(
        "d3",
        "Pixel 8",
        "BAC",
        "JMRTD",
        ref_crypto=cc.CryptoReport("FAIL", "TRUSTED", "CHECKED_GOOD", "PASS"),
    )
    g = cc.gate([*enough(), bad], POLICY)
    assert (
        g.status is cc.Status.FAIL
        and "CRITICAL_DISAGREEMENT" in g.reasons
        and "CRYPTOGRAPHIC_REPORTS_DISAGREE" in g.reasons
    )


def test_pairs_that_are_not_comparable_count_for_nothing() -> None:
    void = cc.compare(run(doc="d9"), run("JMRTD", doc="other"))
    g = cc.gate([void], POLICY)
    assert (
        g.status is cc.Status.FAIL
        and "NO_COMPARABLE_RUNS" in g.reasons
        and g.counts["comparable"] == 0
    )
    padded = cc.gate([*enough(), void, void], POLICY)
    assert (
        padded.status is cc.Status.PASS and padded.counts["comparable"] == 4
    )  # padding the list with void pairs adds nothing


def test_passive_authentication_that_was_never_compared_does_not_pass_the_gate() -> None:
    skipped = [
        pair(
            c.document_ref, c.device_model, c.access, c.reference, crypto=cc.CryptoReport("NOT_RUN")
        )
        for c in enough()
    ]
    assert "PASSIVE_AUTH_NOT_COMPARED_ENOUGH" in cc.gate(skipped, POLICY).reasons
