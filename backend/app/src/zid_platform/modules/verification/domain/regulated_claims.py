"""Regulated claims publisher (ZID-RCG-003 RCG-G07, WF-07, section 05.2; T05, T33; CR-077). Pure domain, no I/O.

Sales copy, product labels, trust marks and website text may say only what an APPROVED claim template says, and only while the facts behind it hold:
- a template names its basis: the exact certification scopes it relies on (an official-register attestation, CR-073) and the markets it speaks for
  (a launched market release, CR-072). A certified company with an unrelated service, or a neighbouring country, is not a basis (T05);
- it is never global: a template that names no country, a wildcard, or wording such as "global" or "worldwide" is blocked (T33). Every country the
  template names must be backed by a certification or a market in that country, so a claim stays as narrow as its evidence (section 12);
- it is approved by two roles (legal and the claims owner), each signing the exact template hash; editing the text after approval breaks the hash and
  blocks it (the same discipline as policy packs, T03);
- it is publishable only now: a certification on hold, a market closed or withdrawn, or a template out of its dates blocks it at once, without anyone
  editing the template;
- every decision can be exported as a proof bundle: the template hash, each attestation snapshot and proof hash, each release and its hash, and a
  digest over all of it, so the claim can be replayed and shown to a regulator.
"""

from __future__ import annotations

import hashlib
import json
import re
from collections.abc import Iterable, Mapping
from dataclasses import dataclass, field
from datetime import datetime
from typing import Any

from zid_platform.modules.verification.domain.attestation import Decision as CertDecision
from zid_platform.modules.verification.domain.attestation import Scope as CertScope
from zid_platform.modules.verification.domain.market_release import MarketDecision, MarketKey

AUDIENCES = frozenset({"PUBLIC_WEBSITE", "PRODUCT_LABEL", "SALES", "SUPPORT", "API"})
CLAIM_APPROVER_ROLES = ("legal", "claims_owner")
COUNTRY = re.compile(r"^[A-Z]{2}$")
TEMPLATE_ID = re.compile(r"^[a-z0-9][a-z0-9-]{0,127}$")
# wording that claims more than any single market release or certification can support
GLOBAL_WORDING = re.compile(
    r"\b(global(ly)?|world[- ]?wide|all countries|every country|everywhere|international(ly)? certified|universal(ly)?)\b",
    re.IGNORECASE,
)


class ClaimTemplateError(ValueError):
    def __init__(self, code: str, detail: str = "") -> None:
        super().__init__(f"{code}: {detail}" if detail else code)
        self.code = code


@dataclass(frozen=True)
class Approval:
    reference: str  # the approval record (a ticket, a document id)
    signed_hash: str  # the template hash the approver signed


@dataclass(frozen=True)
class ClaimTemplate:
    template_id: str
    version: int
    text: str
    audiences: frozenset[str]
    countries: tuple[str, ...]
    certifications: tuple[CertScope, ...] = ()
    markets: tuple[MarketKey, ...] = ()
    effective_from: datetime | None = None
    effective_until: datetime | None = None
    approvals: Mapping[str, Approval] = field(default_factory=dict)


@dataclass(frozen=True)
class ClaimDecision:
    publishable: bool
    reasons: tuple[str, ...]
    template_hash: str
    evidence: tuple[Mapping[str, Any], ...] = ()


def _label(s: CertScope) -> str:
    return f"{s.entity}/{s.scheme}/{s.country}/{s.service_code}"


def _market_label(m: MarketKey) -> str:
    return f"{m.country}/{m.product}/{m.purpose}"


def template_hash(t: ClaimTemplate) -> str:
    """What the approvers sign: everything that the claim says and rests on, never the approvals themselves."""
    body = {
        "template_id": t.template_id,
        "version": t.version,
        "text": t.text,
        "audiences": sorted(t.audiences),
        "countries": sorted(t.countries),
        "certifications": sorted(_label(s) for s in t.certifications),
        "markets": sorted(_market_label(m) for m in t.markets),
        "effective_from": t.effective_from.isoformat() if t.effective_from else None,
        "effective_until": t.effective_until.isoformat() if t.effective_until else None,
    }
    return hashlib.sha256(
        json.dumps(body, sort_keys=True, separators=(",", ":")).encode()
    ).hexdigest()


def problems(t: ClaimTemplate) -> list[str]:
    """What is wrong with the template itself, whatever the facts are today."""
    out: list[str] = []
    if not t.text.strip() or len(t.text) > 500:
        out.append("TEMPLATE_TEXT_INVALID")
    if (
        GLOBAL_WORDING.search(t.text)
        or not t.countries
        or any(not COUNTRY.match(c) for c in t.countries)
    ):
        out.append("TEMPLATE_GLOBAL_CLAIM")  # T33: no global certification claim, ever
    if not t.audiences or not t.audiences <= AUDIENCES:
        out.append("TEMPLATE_AUDIENCE_INVALID")
    if not t.certifications and not t.markets:
        out.append("TEMPLATE_NO_BASIS")
    backed = {s.country for s in t.certifications} | {m.country for m in t.markets}
    if any(c not in backed for c in t.countries):
        out.append("TEMPLATE_COUNTRY_UNSUPPORTED")  # a country the claim names that no basis covers
    if any(b not in set(t.countries) for b in backed):
        out.append("TEMPLATE_BASIS_OUTSIDE_COUNTRIES")
    return out


def decide(
    t: ClaimTemplate,
    *,
    at: datetime,
    certifications: Mapping[CertScope, CertDecision],
    markets: Mapping[MarketKey, MarketDecision],
    audience: str | None = None,
) -> ClaimDecision:
    """May this template be shown now (to `audience`)? Every reason it may not is listed; the evidence behind a publishable claim is returned."""
    digest = template_hash(t)
    reasons = problems(t)
    if audience is not None and audience not in t.audiences:
        reasons.append("TEMPLATE_NOT_FOR_AUDIENCE")
    for role in CLAIM_APPROVER_ROLES:
        a = t.approvals.get(role)
        if a is None or not a.reference.strip():
            reasons.append(f"approval_missing:{role}")
        elif a.signed_hash != digest:
            reasons.append(f"approval_hash_mismatch:{role}")  # edited after approval
    if t.effective_from is not None and at < t.effective_from:
        reasons.append("not_yet_effective")
    if t.effective_until is not None and at >= t.effective_until:
        reasons.append("expired")
    evidence: list[Mapping[str, Any]] = []
    for scope in sorted(t.certifications, key=_label):
        d = certifications.get(scope)
        if d is None or not d.certified:
            reasons.append(f"certification_hold:{_label(scope)}:{d.reason if d else 'not_checked'}")
        elif d.snapshot is not None:
            evidence.append(
                {
                    "kind": "certification",
                    "scope": _label(scope),
                    "snapshot_id": d.snapshot.snapshot_id,
                    "proof_hash": d.snapshot.proof_hash,
                    "register_url": d.snapshot.register_url,
                    "fetched_at": d.snapshot.fetched_at.isoformat(),
                }
            )
    for market in sorted(t.markets, key=_market_label):
        m = markets.get(market)
        if m is None or not m.open:
            reasons.append(
                f"market_closed:{_market_label(market)}:{m.reason if m else 'not_checked'}"
            )
        elif m.release is not None:
            evidence.append(
                {
                    "kind": "market",
                    "market": _market_label(market),
                    "release_id": m.release.release_id,
                    "version": m.release.version,
                    "release_hash": m.release.release_hash,
                }
            )
    return ClaimDecision(not reasons, tuple(reasons), digest, tuple(evidence))


def proof_bundle(t: ClaimTemplate, decision: ClaimDecision, *, at: datetime) -> dict[str, Any]:
    """The exportable proof: what the claim said, when it was judged, why, and the evidence, with a digest over all of it."""
    body: dict[str, Any] = {
        "template": {
            "template_id": t.template_id,
            "version": t.version,
            "template_hash": decision.template_hash,
            "text": t.text,
            "audiences": sorted(t.audiences),
            "countries": sorted(t.countries),
            "approvals": {r: a.reference for r, a in sorted(t.approvals.items())},
        },
        "judged_at": at.isoformat(),
        "publishable": decision.publishable,
        "reasons": list(decision.reasons),
        "evidence": [dict(e) for e in decision.evidence],
    }
    canonical = json.dumps(body, sort_keys=True, separators=(",", ":"), default=str)
    return body | {"digest": hashlib.sha256(canonical.encode()).hexdigest()}


def verify_bundle(bundle: Mapping[str, Any]) -> bool:
    """A proof bundle has not been altered: its digest still matches its content."""
    body = {k: v for k, v in bundle.items() if k != "digest"}
    canonical = json.dumps(body, sort_keys=True, separators=(",", ":"), default=str)
    return hashlib.sha256(canonical.encode()).hexdigest() == bundle.get("digest")


def _when(value: Any) -> datetime | None:
    if value is None:
        return None
    if isinstance(value, datetime):
        parsed = value
    else:
        parsed = datetime.fromisoformat(str(value))
    if parsed.tzinfo is None:
        raise ClaimTemplateError("CLAIM_TEMPLATES_INVALID", "times carry a time zone")
    return parsed


def parse(doc: Mapping[str, Any]) -> list[ClaimTemplate]:
    """Build templates from a loaded config document. A malformed row refuses the whole file; a template that is merely unpublishable (a global
    wording, a missing approval) still loads, so operators can see why it is blocked."""
    out: list[ClaimTemplate] = []
    try:
        for raw in doc.get("templates") or []:
            if not TEMPLATE_ID.match(str(raw["template_id"])):
                raise ClaimTemplateError(
                    "CLAIM_TEMPLATES_INVALID", f"template_id {raw['template_id']!r}"
                )
            out.append(
                ClaimTemplate(
                    template_id=str(raw["template_id"]),
                    version=int(raw["version"]),
                    text=str(raw["text"]),
                    audiences=frozenset(str(a).upper() for a in raw.get("audiences") or ()),
                    countries=tuple(str(c).upper() for c in raw.get("countries") or ()),
                    certifications=tuple(
                        CertScope(
                            str(c["entity"]),
                            str(c["scheme"]),
                            str(c["country"]).upper(),
                            str(c["service_code"]),
                        )
                        for c in raw.get("certifications") or ()
                    ),
                    markets=tuple(
                        MarketKey(str(m["country"]).upper(), str(m["product"]), str(m["purpose"]))
                        for m in raw.get("markets") or ()
                    ),
                    effective_from=_when(raw.get("effective_from")),
                    effective_until=_when(raw.get("effective_until")),
                    approvals={
                        str(role): Approval(str(a["reference"]), str(a["signed_hash"]))
                        for role, a in (raw.get("approvals") or {}).items()
                    },
                )
            )
    except ClaimTemplateError:
        raise
    except (KeyError, ValueError, TypeError, AttributeError) as e:
        raise ClaimTemplateError("CLAIM_TEMPLATES_INVALID", str(e)[:80]) from e
    keys = [(t.template_id, t.version) for t in out]
    if len(keys) != len(set(keys)):
        raise ClaimTemplateError("CLAIM_TEMPLATES_INVALID", "duplicate template version")
    return out


def latest(templates: Iterable[ClaimTemplate]) -> list[ClaimTemplate]:
    """The highest version of each template."""
    best: dict[str, ClaimTemplate] = {}
    for t in templates:
        if t.template_id not in best or t.version > best[t.template_id].version:
            best[t.template_id] = t
    return [best[k] for k in sorted(best)]
