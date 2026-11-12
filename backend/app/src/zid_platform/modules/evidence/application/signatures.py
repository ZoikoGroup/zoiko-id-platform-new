"""Turn what the CMS and trust modules found into the `signature` and `seal` results of a document validation (ZID-INT-002 section 09).

Statuses and what they claim:
- NOT_VERIFIED       the signature is broken: the document changed after signing, or the signature value does not verify;
- NOT_VERIFIED       also when the certificate was revoked at or before the (trusted) signing time, or was not valid at that time;
- INDETERMINATE      the cryptography is sound but it cannot be relied on yet: no trust anchor is configured, the chain does not end at one,
                     a certificate is not valid now (and the signing time is only claimed), content was appended after signing, or the
                     algorithm is one this validator does not support;
- VERIFIED_LIMITED   cryptographically valid AND the chain ends at a trust anchor the operator configured, but something was not checked:
                     the signing time is the signer's own claim (no verified timestamp) or revocation was not (fully) checked. The
                     reasons list exactly what is missing;
- VERIFIED           all of it: valid, chained to a configured anchor, a timestamp that verified against a configured TSA root fixes the
                     signing time, the chain was valid at that time, and a supplied CRL covers every certificate and lists none as revoked.
                     Even then it is not lawful authority for any purpose.
- UNAVAILABLE        the seal check without an authenticated trusted list: qualified status is NOT_ASSESSED.

A valid signature is not lawful authority for any purpose, and nothing here makes Zoiko iD a qualified trust service provider.
"""

from __future__ import annotations

from dataclasses import replace
from datetime import UTC, datetime

from zid_platform.modules.evidence.application import cms, xades
from zid_platform.modules.evidence.application import trust as tr
from zid_platform.modules.evidence.domain import documents as docs

ALWAYS = ("REVOCATION_NOT_CHECKED", "SIGNING_TIME_NOT_TRUSTED")


def signature_result(findings: list[cms.Finding], config: tr.TrustConfig) -> docs.CheckResult:
    detail: dict[str, object] = {
        "signatures": [f.public() for f in findings],
        "trust_notes": config.notes(),
        "chain_checked": any(f.chain is not cms.Chain.NO_ANCHORS for f in findings),
        "revocation_checked": all(f.revocation in {"GOOD", "PARTIAL"} for f in findings),
        "signing_time_checked": all(f.timestamp == "VALID" for f in findings),
    }
    broken = [f for f in findings if not f.cryptographically_valid and f.algorithm_ok]
    if broken:
        reasons = sorted(
            {p for f in broken for p in f.problems if p != "CONTENT_ADDED_AFTER_SIGNING"}
            or {"SIGNATURE_INVALID"}
        )
        return docs.CheckResult("signature", docs.Status.NOT_VERIFIED, tuple(reasons), detail)
    unreadable = [f for f in findings if not f.signer]
    if unreadable:
        reasons = sorted({p for f in unreadable for p in f.problems})
        return docs.CheckResult(
            "signature",
            docs.Status.INDETERMINATE,
            tuple(reasons or ["SIGNATURE_UNREADABLE"]),
            detail,
        )
    unsupported = [f for f in findings if not f.algorithm_ok]
    if unsupported:
        reasons = sorted({p for f in unsupported for p in f.problems})
        return docs.CheckResult("signature", docs.Status.INDETERMINATE, tuple(reasons), detail)
    # every signature is cryptographically sound; what is left is whether it can be relied on
    if any(f.revocation == "REVOKED" for f in findings):
        if all(f.timestamp == "VALID" for f in findings):
            return docs.CheckResult(
                "signature",
                docs.Status.NOT_VERIFIED,
                ("CERTIFICATE_REVOKED_BEFORE_SIGNING",),
                detail,
            )
        return docs.CheckResult(
            "signature",
            docs.Status.NOT_VERIFIED,
            ("CERTIFICATE_REVOKED", "SIGNING_TIME_NOT_TRUSTED"),
            detail,
        )
    if any(not f.covers_whole_file for f in findings):
        uncovered = (
            "REFERENCE_DOES_NOT_COVER_DOCUMENT"
            if any("REFERENCE_DOES_NOT_COVER_DOCUMENT" in f.problems for f in findings)
            else "CONTENT_ADDED_AFTER_SIGNING"
        )
        return docs.CheckResult(
            "signature", docs.Status.INDETERMINATE, (uncovered, *ALWAYS), detail
        )
    if any(f.chain is cms.Chain.EXPIRED for f in findings):
        if all(
            f.timestamp == "VALID" for f in findings
        ):  # the time is trusted, so the certificate really was not valid when signed
            return docs.CheckResult(
                "signature",
                docs.Status.NOT_VERIFIED,
                ("CERTIFICATE_NOT_VALID_AT_SIGNING_TIME",),
                detail,
            )
        reasons = ("SIGNATURE_VALID", "CERTIFICATE_NOT_VALID_NOW", "SIGNING_TIME_NOT_TRUSTED")
        if any(f.timestamp_token_present for f in findings):
            reasons = (*reasons, "TIMESTAMP_NOT_VERIFIED")
        return docs.CheckResult("signature", docs.Status.INDETERMINATE, reasons, detail)
    if any(f.chain is cms.Chain.NO_ANCHORS for f in findings):
        return docs.CheckResult(
            "signature",
            docs.Status.INDETERMINATE,
            ("SIGNATURE_VALID", "NO_TRUST_ANCHORS_CONFIGURED", *ALWAYS),
            detail,
        )
    if not all(f.chain is cms.Chain.TRUSTED for f in findings):
        return docs.CheckResult(
            "signature",
            docs.Status.INDETERMINATE,
            ("SIGNATURE_VALID", "ISSUER_NOT_TRUSTED", *ALWAYS),
            detail,
        )
    reasons = ["CRYPTOGRAPHICALLY_VALID", "CHAIN_TO_CONFIGURED_ANCHOR"]
    timed = all(f.timestamp == "VALID" for f in findings)
    checked = all(f.revocation == "GOOD" for f in findings)
    reasons.append("SIGNING_TIME_TRUSTED_BY_TIMESTAMP" if timed else "SIGNING_TIME_NOT_TRUSTED")
    if checked:
        reasons.append("REVOCATION_CHECKED_GOOD")
    elif any(f.revocation == "PARTIAL" for f in findings):
        reasons.append("REVOCATION_PARTIALLY_CHECKED")
    else:
        reasons.append("REVOCATION_NOT_CHECKED")
    status = docs.Status.VERIFIED if timed and checked else docs.Status.VERIFIED_LIMITED
    return docs.CheckResult("signature", status, tuple(reasons), detail)


def seal_result(
    findings: list[cms.Finding], config: tr.TrustConfig, at: datetime
) -> docs.CheckResult:
    """Qualified status as a configured, authenticated trusted list states it for the signer's CA. Otherwise NOT_ASSESSED."""
    ok = [f for f in findings if f.cryptographically_valid and f.chain is cms.Chain.TRUSTED]
    if not ok:
        return docs.CheckResult(
            "seal",
            docs.Status.UNAVAILABLE,
            ("NO_TRUSTED_SIGNATURE_TO_ASSESS",),
            {"qualified_status": "NOT_ASSESSED"},
        )
    q = tr.qualified_status(ok[0].chain_certificates, config, at)
    detail: dict[str, object] = {
        "qualified_status": q.status,
        "service_type": q.service_type,
        "trusted_lists": [
            {"name": t.name, "authenticated": t.authenticated, "problem": t.problem}
            for t in config.trusted_lists
        ],
        "revocation_checked": False,
    }
    if q.status == "NOT_ASSESSED":
        return docs.CheckResult(
            "seal", docs.Status.UNAVAILABLE, (*q.reasons, "NO_TRUST_LIST"), detail
        )
    if q.status == "NOT_QUALIFIED":
        return docs.CheckResult("seal", docs.Status.INDETERMINATE, q.reasons, detail)
    return docs.CheckResult(
        "seal",
        docs.Status.VERIFIED_LIMITED,
        ("LISTED_AS_QUALIFIED_SERVICE", "REVOCATION_NOT_CHECKED"),
        detail,
    )


def apply(
    v: docs.Validation, pdf: bytes, config: tr.TrustConfig, *, at: datetime | None = None
) -> docs.Validation:
    """Replace the detection-only `signature` and `seal` results of `v` with real ones, when the document is a PDF that carries a signature."""
    if v.sniffed_type not in {"application/pdf", docs.XML_TYPE}:
        return v
    when = at or datetime.now(UTC)
    inspect = xades.inspect_xml if v.sniffed_type == docs.XML_TYPE else cms.inspect_pdf
    findings = inspect(
        pdf,
        anchors=config.anchors,
        at=when,
        tsa_roots=config.tsa_roots,
        crls=config.crls,
        ocsp_responses=config.ocsp_responses,
    )
    if not findings:
        return v  # no signature: the domain's NO_SIGNATURE_FOUND stands
    out = _replace(v, findings, config, when)
    if v.sniffed_type != "application/pdf":
        return out
    stamps = cms.inspect_document_timestamps(pdf, tsa_roots=config.tsa_roots, at=when)
    state = cms.archive_state(stamps)
    results = []
    for r in out.results:
        if r.check == "signature" and stamps:
            reasons = (
                (*r.reasons, "DOCUMENT_TIMESTAMP_PROBLEM")
                if state == "ARCHIVE_BROKEN"
                else r.reasons
            )
            r = replace(
                r,
                reasons=reasons,
                detail={**r.detail, "document_timestamps": stamps, "ltv_state": state},
            )
        results.append(r)
    return replace(out, results=tuple(results))


def apply_detached(
    v: docs.Validation,
    content: bytes,
    signature: bytes,
    config: tr.TrustConfig,
    *,
    at: datetime | None = None,
) -> docs.Validation:
    """Replace the `signature` and `seal` results with those of a standalone CAdES signature (a detached .p7s) over `content`, which can
    be any allowed type. The signature file is the caller's evidence; the content is what it must match."""
    when = at or datetime.now(UTC)
    findings = cms.inspect_cades(
        signature,
        content,
        anchors=config.anchors,
        at=when,
        tsa_roots=config.tsa_roots,
        crls=config.crls,
        ocsp_responses=config.ocsp_responses,
    )
    return _replace(v, findings, config, when)


def _replace(
    v: docs.Validation, findings: list[cms.Finding], config: tr.TrustConfig, when: datetime
) -> docs.Validation:
    out = []
    for r in v.results:
        if r.check == "signature":
            out.append(signature_result(findings, config))
        elif r.check == "seal":
            out.append(seal_result(findings, config, when))
        else:
            out.append(r)
    return replace(v, results=tuple(out))
