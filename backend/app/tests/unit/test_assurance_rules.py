"""Revenue assurance, journals and run-rate KPIs (ZID-COM-001 §22, §24, §30; CR-030). Pure rules."""

from __future__ import annotations

from decimal import Decimal

import pytest
from zid_platform.modules.commercial.domain import assurance as a

D = Decimal
FEES = {"BUSINESS": D(1499), "SCALE": D(4999), "ENTERPRISE": D(12500)}


def test_the_proposal_run_rate_illustration() -> None:
    """§22: 20 Business, 6 Scale and 2 Enterprise at the $12,500 floor = $84,974 a month, $1,019,688 a year."""
    out = a.run_rate([("BUSINESS", 20, False), ("SCALE", 6, False), ("ENTERPRISE", 2, False)], FEES)
    assert out == {"mrr": D(84_974), "arr": D(1_019_688)}
    # and the illustrative gross profit at 65 / 75 / 85 percent
    assert a.gross_profit(out["mrr"], D("0.75")) == D("63730.50")
    assert [
        round(a.gross_profit(out["mrr"], D(m)) / 1000, 1) for m in ("0.65", "0.75", "0.85")
    ] == [
        D("55.2"),
        D("63.7"),
        D("72.2"),
    ]


def test_intercompany_accounts_never_count_as_external_revenue() -> None:
    out = a.run_rate([("BUSINESS", 1, False), ("SCALE", 3, True)], FEES)
    assert out["mrr"] == D(1499)
    with pytest.raises(a.AssuranceError):
        a.run_rate([("PLATINUM", 1, False)], FEES)


def test_journals_balance_and_split_subscription_usage_and_tax() -> None:
    inv = a.invoice_journal(
        "ZID-00000001",
        [
            ("FEE", D(1499), D("299.80")),
            ("OVERAGE", D(270), D(54)),
            ("CONNECTION", D(149), D("29.80")),
            ("CREDIT", D(-53), D(0)),
        ],
    )
    by = {e.account: e for e in inv}
    assert by["RECEIVABLES"].debit == D("2248.60")
    assert by["REVENUE_SUBSCRIPTION"].credit == D(1446) and by["REVENUE_USAGE"].credit == D(419)
    assert by["TAX_PAYABLE"].credit == D("383.60")
    assert a.balanced(inv)
    assert a.balanced(a.credit_note_journal("CN-1", D(10))) and a.balanced(
        a.payment_journal("P-1", D(5))
    )


def test_the_change_log_chain_detects_edits_gaps_and_reorders() -> None:
    entries, prev = [], None
    for n in range(1, 5):
        d = a.digest({"n": n})
        prev = a.link(prev, n, d)
        entries.append((n, d, prev))
    assert a.verify(entries) is None
    assert a.verify([entries[0], (2, a.digest("x"), entries[1][2]), *entries[2:]]) == 2
    assert a.verify([entries[0], *entries[2:]]) == 3
    assert a.digest({"b": 1, "a": 2}) == a.digest({"a": 2, "b": 1})  # canonical
    assert set(a.KPI_DEFINITIONS) == {"mrr", "arr", "consumption_revenue"}


def test_a_signed_audit_pack_verifies_and_every_tampering_is_caught() -> None:
    """The pack is signed with the evidence key and verified offline against trusted public keys only."""
    from zid_crypto import LocalSigner
    from zid_platform.modules.commercial.application.assurance_service import (
        AuditPackError,
        sign_audit_pack,
        verify_audit_pack,
    )

    bundle = {
        "tenant": "t-alias",
        "month": "2026-10",
        "invoices": [{"number": "ZE-1", "gross": "10"}],
    }
    pack = {**bundle, "bundle_digest": a.digest(bundle)}
    signer, other = LocalSigner("evidence-a"), LocalSigner("evidence-b")
    signed = sign_audit_pack(signer, pack, issuer="uk-1")
    keys = [signer.public_jwk()]

    assert verify_audit_pack(signed, keys=keys)["iss"] == "uk-1"

    altered = {**signed, "invoices": [{"number": "ZE-1", "gross": "1"}]}
    with pytest.raises(AuditPackError, match="AUDIT_PACK_ALTERED"):
        verify_audit_pack(altered, keys=keys)
    with pytest.raises(AuditPackError, match="AUDIT_PACK_UNSIGNED"):
        verify_audit_pack(pack, keys=keys)
    with pytest.raises(AuditPackError, match="AUDIT_PACK_SIGNATURE_INVALID"):
        verify_audit_pack(
            signed, keys=[other.public_jwk()]
        )  # a key that did not sign it is never trusted

    # a re-signed pack whose digest does not cover its body is caught too
    forged = sign_audit_pack(signer, {**pack, "bundle_digest": "0" * 64}, issuer="uk-1")
    with pytest.raises(AuditPackError, match="AUDIT_PACK_DIGEST_MISMATCH"):
        verify_audit_pack(forged, keys=keys)
