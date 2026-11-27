"""A market's proofing rules as one pinned, hashed unit (ZID-DTV-WF-03 section 9; CR-110). Pure domain.

The file the owners maintain says, per market: which methods are required, what an expired document or an unknown revocation means, which alternative routes are signed, the biometric basis, the retry ceilings
and the printed-document checks that must run. This module reads it strictly: a malformed entry refuses the whole file, and an unknown key is an error, so a typo can never switch a control off.
A case is pinned to a market's `policy_hash`; changing the file changes the hash, and a case already decided keeps the hash it was decided under."""

from __future__ import annotations

import hashlib
import json
from collections.abc import Mapping
from dataclasses import dataclass
from typing import Any

from zid_platform.modules.verification.domain.idv_controls import BiometricBasis, RetryPolicy
from zid_platform.modules.verification.domain.idv_decision import (
    AlternateRoute,
    Policy,
    Requirement,
)


class PolicyError(ValueError):
    pass


@dataclass(frozen=True, slots=True)
class MarketRules:
    market: str
    policy: Policy
    basis: BiometricBasis
    self_hosted_engine: bool
    retry: RetryPolicy
    optical_required: frozenset[str]
    attestation_required: bool
    policy_hash: str
    media_retention_days: int | None = (
        None  # how long a reviewed route keeps the document photograph; none set means none is kept, and the route refuses it
    )
    result_valid_days: int | None = (
        None  # how long an approval counts as an identity claim; none set means an approval leaves no claim
    )
    review_sla_hours: int | None = (
        None  # how long a case may wait for its reviewers before the queue calls it overdue
    )


_TOP = {
    "policy_version",
    "purpose",
    "chip",
    "optical",
    "face",
    "clone_check",
    "expired_document",
    "on_revocation_unknown",
    "reject_on_optical_fail",
    "capture_integrity",
    "alternates",
    "biometric",
    "retries",
    "optical_checks",
    "attestation_required",
    "media_retention_days",
    "result_valid_days",
    "review_sla_hours",
}
_BIO = {
    "lawful_basis",
    "impact_assessment_ref",
    "notice_version",
    "independent_pad_ref",
    "self_hosted_engine",
}


def _req(value: Any, key: str, default: str) -> Requirement:
    try:
        return Requirement(str(value if value is not None else default))
    except ValueError as e:
        raise PolicyError(f"{key} must be REQUIRED, OPTIONAL or NOT_USED") from e


def _text(value: Any) -> str | None:
    return str(value).strip() or None if value is not None else None


def _one(market: str, doc: Mapping[str, Any]) -> MarketRules:
    if not isinstance(doc, Mapping):
        raise PolicyError(f"{market}: the entry must be a mapping")
    unknown = sorted(set(doc) - _TOP)
    if unknown:
        raise PolicyError(f"{market}: unknown keys {unknown}")
    version = _text(doc.get("policy_version"))
    if not version:
        raise PolicyError(f"{market}: policy_version is required")
    expired = str(doc.get("expired_document", "REFUSE"))
    if expired not in ("REFUSE", "REVIEW", "ACCEPT"):
        raise PolicyError(f"{market}: expired_document must be REFUSE, REVIEW or ACCEPT")
    revocation = str(doc.get("on_revocation_unknown", "INDETERMINATE"))
    if revocation not in ("INDETERMINATE", "REVIEW"):
        raise PolicyError(f"{market}: on_revocation_unknown must be INDETERMINATE or REVIEW")
    alternates: list[AlternateRoute] = []
    for a in doc.get("alternates") or []:
        if (
            not isinstance(a, Mapping)
            or not _text(a.get("route_id"))
            or set(a)
            - {
                "route_id",
                "may_approve",
                "reviewers_needed",
                "automatic",
                "face_match",
                "face_threshold",
            }
        ):
            raise PolicyError(
                f"{market}: an alternate route needs route_id, may_approve and reviewers_needed only"
            )
        automatic = a.get("automatic", False)
        if not isinstance(automatic, bool):
            raise PolicyError(f"{market}: automatic must be true or false")
        if automatic:
            if "reviewers_needed" in a or a.get("may_approve") is not True:
                raise PolicyError(
                    f"{market}: an automatic route sets may_approve: true and no reviewers_needed (nobody reviews it)"
                )
            face_match = a.get("face_match", False)
            if not isinstance(face_match, bool):
                raise PolicyError(f"{market}: face_match must be true or false")
            threshold = a.get("face_threshold", 0.5)
            if face_match and (
                isinstance(threshold, bool)
                or not isinstance(threshold, int | float)
                or not 0.2 <= float(threshold) <= 0.99
            ):
                raise PolicyError(f"{market}: face_threshold must be a number from 0.2 to 0.99")
            if not face_match and "face_threshold" in a:
                raise PolicyError(f"{market}: face_threshold belongs to a face_match route")
            alternates.append(
                AlternateRoute(str(a["route_id"]), True, 0, True, face_match, float(threshold))
            )
            continue
        if "face_match" in a or "face_threshold" in a:
            raise PolicyError(f"{market}: face_match belongs to an automatic route")
        need = int(a.get("reviewers_needed", 2))
        if need < 2:
            raise PolicyError(f"{market}: an alternate route needs at least two distinct reviewers")
        alternates.append(
            AlternateRoute(str(a["route_id"]), bool(a.get("may_approve", False)), need)
        )
    if any(x.route_id == "CHIP" for x in alternates):
        raise PolicyError(f"{market}: CHIP is not an alternate route")
    bio = doc.get("biometric") or {}
    if not isinstance(bio, Mapping) or set(bio) - _BIO:
        raise PolicyError(f"{market}: biometric has unknown keys")
    retries = doc.get("retries") or {}
    if not isinstance(retries, Mapping) or set(retries) - {"nfc", "capture"}:
        raise PolicyError(f"{market}: retries has unknown keys")
    nfc, cap = (
        int(retries.get("nfc", 0)),
        int(retries.get("capture", 0)),
    )  # unset means a ceiling of none: a configuration fault the controls refuse
    policy = Policy(
        version=version,
        chip=_req(doc.get("chip"), "chip", "REQUIRED"),
        optical=_req(doc.get("optical"), "optical", "REQUIRED"),
        face=_req(doc.get("face"), "face", "REQUIRED"),
        clone_check=_req(doc.get("clone_check"), "clone_check", "OPTIONAL"),
        expired_document=expired,
        on_revocation_unknown=revocation,
        reject_on_optical_fail=bool(doc.get("reject_on_optical_fail", True)),
        capture_integrity=_req(doc.get("capture_integrity"), "capture_integrity", "REQUIRED"),
        alternates=tuple(alternates),
    )
    canonical = json.dumps(
        {"market": market, **{k: doc[k] for k in sorted(doc)}},
        sort_keys=True,
        default=str,
        separators=(",", ":"),
    )
    return MarketRules(
        market=market,
        policy=policy,
        basis=BiometricBasis(
            _text(bio.get("lawful_basis")),
            _text(bio.get("impact_assessment_ref")),
            _text(bio.get("notice_version")),
            _text(bio.get("independent_pad_ref")),
        ),
        self_hosted_engine=bool(
            bio.get("self_hosted_engine", True)
        ),  # an engine's own PAD is assumed unproven until an evaluation is recorded
        retry=RetryPolicy(market, str(doc.get("purpose") or "KYC"), nfc, cap),
        optical_required=frozenset(str(x) for x in (doc.get("optical_checks") or [])),
        attestation_required=bool(doc.get("attestation_required", False)),
        policy_hash="sha256:" + hashlib.sha256(canonical.encode()).hexdigest(),
        media_retention_days=_retention(market, doc.get("media_retention_days")),
        result_valid_days=_bounded(market, "result_valid_days", doc.get("result_valid_days"), 730),
        review_sla_hours=_bounded(market, "review_sla_hours", doc.get("review_sla_hours"), 720),
    )


def _retention(market: str, value: Any) -> int | None:
    if value is None:
        return None
    if isinstance(value, bool) or not isinstance(value, int) or not 1 <= value <= 90:
        raise PolicyError(f"{market}: media_retention_days is a whole number of days from 1 to 90")
    return value


def _bounded(market: str, name: str, value: Any, top: int) -> int | None:
    if value is None:
        return None
    if isinstance(value, bool) or not isinstance(value, int) or not 1 <= value <= top:
        raise PolicyError(f"{market}: {name} is a whole number from 1 to {top}")
    return value


def parse(doc: Mapping[str, Any] | None) -> dict[str, MarketRules]:
    markets = (doc or {}).get("markets") or {}
    if not isinstance(markets, Mapping):
        raise PolicyError("markets must be a mapping of market code to rules")
    return {str(k).upper(): _one(str(k).upper(), v) for k, v in markets.items()}
