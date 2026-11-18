"""The evidence package for one screening case (ZID-DTV-WF-06 S13, ENG-16, QA-043; CR-113). Pure domain, no I/O.

A bundle is built for one stated purpose and one requester, redacted by kind, watermarked, and given an expiry. It carries a manifest of exactly what it holds, and a hash over that
manifest and the decision chain, so an auditor can check that nothing was added, removed or edited after it was made. It is a tamper-evident package; it is not a signed credential and
does not claim to be one.

Redaction is by kind, not by hope. A kind that holds personal data or investigation detail (the candidate, the raw inputs, a provider's payload, a reviewer's notes) is left out, and may be
included only with a second approver who is not the requester, and then only as hashes of its fields: a name, a date of birth, an identifier and a document number never appear. There is no
raw download: the bundle is capped, and anything outside the allowed kinds is refused.
"""

from __future__ import annotations

import hashlib
import json
from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from datetime import datetime, timedelta
from typing import Any

# kinds that are safe by their nature: facts about what was checked and decided, never about the person
OPEN_KINDS = frozenset(
    {
        "REQUEST_AUTHORISATION",
        "SOURCE_SNAPSHOT",
        "COVERAGE",
        "DISPOSITION",
        "POLICY",
        "PROCESSING_LOCATION",
        "VENDOR_TERMS",
        "RETENTION",
    }
)
# kinds that hold personal data or investigation detail: left out unless a second person approves, and then only as hashes
SENSITIVE_KINDS = frozenset(
    {"MATCH_CANDIDATE", "RAW_INPUT", "PROVIDER_PAYLOAD", "CASE_NOTE", "ALERT"}
)
PERSONAL_FIELDS = frozenset(
    {
        "name",
        "names",
        "aliases",
        "birth",
        "date_of_birth",
        "dob",
        "identifiers",
        "document_number",
        "passport",
        "national_id",
        "address",
        "email",
        "phone",
        "nationality",
        "countries",
    }
)
MAX_ARTIFACTS = 200
MAX_TTL = timedelta(days=30)


class ExportError(ValueError):
    def __init__(self, code: str, detail: str = "") -> None:
        super().__init__(f"{code}: {detail}" if detail else code)
        self.code = code
        self.detail = detail


@dataclass(frozen=True, slots=True)
class Artifact:
    kind: str
    ref: str
    data: Mapping[str, Any]


@dataclass(frozen=True, slots=True)
class Bundle:
    case_id: str
    purpose: str
    requester: str
    approver: str | None
    policy_version: str
    created_at: datetime
    expires_at: datetime
    watermark: str
    included: tuple[dict[str, Any], ...]
    excluded: tuple[dict[str, str], ...]  # kind and ref of what was left out, and why
    chain: tuple[str, ...]  # the case's decision hashes, in order
    manifest_hash: str


def _canonical(value: Any) -> bytes:
    return json.dumps(value, sort_keys=True, separators=(",", ":"), default=str).encode()


def _token(value: Any) -> str:
    return "redacted:" + hashlib.sha256(_canonical(value)).hexdigest()[:16]


def redact(data: Mapping[str, Any]) -> dict[str, Any]:
    """Every personal field replaced by a hash token, at any depth. Nothing else changes."""

    def walk(key: str, value: Any) -> Any:
        if key.casefold() in PERSONAL_FIELDS:
            return _token(value)
        if isinstance(value, Mapping):
            return {k: walk(k, v) for k, v in value.items()}
        if isinstance(value, list | tuple):
            return [
                walk(key, v) if not isinstance(v, Mapping | list | tuple) else walk("", v)
                for v in value
            ]
        return value

    return {k: walk(k, v) for k, v in data.items()}


def build(
    case_id: str,
    artifacts: Sequence[Artifact],
    *,
    purpose: str,
    requester: str,
    approver: str | None,
    policy_version: str,
    chain: Sequence[str],
    now: datetime,
    ttl: timedelta = timedelta(days=7),
    include_sensitive: bool = False,
) -> Bundle:
    """The package. Refused without a purpose, for more than the cap, for an unknown kind, or with sensitive kinds requested without a second approver who is not the requester."""
    if len(purpose.strip()) < 10:
        raise ExportError("PURPOSE_REQUIRED", "say what the export is for")
    if not requester:
        raise ExportError("REQUESTER_REQUIRED")
    if len(artifacts) > MAX_ARTIFACTS:
        raise ExportError("EXPORT_TOO_LARGE", f"at most {MAX_ARTIFACTS} items")
    if not timedelta(0) < ttl <= MAX_TTL:
        raise ExportError("EXPIRY_INVALID")
    if include_sensitive and (approver is None or approver == requester):
        raise ExportError(
            "SECOND_APPROVER_REQUIRED",
            "sensitive material needs a second person who is not the requester",
        )
    for a in artifacts:
        if a.kind not in OPEN_KINDS | SENSITIVE_KINDS:
            raise ExportError("KIND_NOT_EXPORTABLE", a.kind)
    included: list[dict[str, Any]] = []
    excluded: list[dict[str, str]] = []
    for a in artifacts:
        if a.kind in SENSITIVE_KINDS and not include_sensitive:
            excluded.append({"kind": a.kind, "ref": a.ref, "why": "SENSITIVE_NOT_APPROVED"})
            continue
        body = redact(
            a.data
        )  # open kinds are redacted too: a stray name in a note field never leaves
        included.append(
            {
                "kind": a.kind,
                "ref": a.ref,
                "sha256": hashlib.sha256(_canonical(body)).hexdigest(),
                "data": body,
            }
        )
    watermark = (
        f"{case_id} | for {requester} | {purpose.strip()[:80]} | until {(now + ttl).isoformat()}"
    )
    manifest = {
        "case_id": case_id,
        "purpose": purpose.strip(),
        "requester": requester,
        "approver": approver if include_sensitive else None,
        "policy_version": policy_version,
        "created_at": now.isoformat(),
        "expires_at": (now + ttl).isoformat(),
        "items": [(i["kind"], i["ref"], i["sha256"]) for i in included],
        "excluded": [(x["kind"], x["ref"]) for x in excluded],
        "chain": list(chain),
    }
    return Bundle(
        case_id,
        purpose.strip(),
        requester,
        approver if include_sensitive else None,
        policy_version,
        now,
        now + ttl,
        watermark,
        tuple(included),
        tuple(excluded),
        tuple(chain),
        hashlib.sha256(_canonical(manifest)).hexdigest(),
    )


def manifest_hash(b: Bundle) -> str:
    manifest = {
        "case_id": b.case_id,
        "purpose": b.purpose,
        "requester": b.requester,
        "approver": b.approver,
        "policy_version": b.policy_version,
        "created_at": b.created_at.isoformat(),
        "expires_at": b.expires_at.isoformat(),
        "items": [
            (i["kind"], i["ref"], hashlib.sha256(_canonical(i["data"])).hexdigest())
            for i in b.included
        ],
        "excluded": [(x["kind"], x["ref"]) for x in b.excluded],
        "chain": list(b.chain),
    }
    return hashlib.sha256(_canonical(manifest)).hexdigest()


def verify(b: Bundle, *, now: datetime) -> list[str]:
    """What is wrong with a bundle an auditor was handed: empty means it is intact and still valid. It checks every item's own hash, the manifest hash, and the expiry."""
    problems: list[str] = []
    for i in b.included:
        if hashlib.sha256(_canonical(i["data"])).hexdigest() != i["sha256"]:
            problems.append(f"ITEM_ALTERED:{i['kind']}:{i['ref']}")
    if manifest_hash(b) != b.manifest_hash:
        problems.append("MANIFEST_HASH_MISMATCH")
    if now >= b.expires_at:
        problems.append("EXPIRED")
    return problems


def leaks_personal_data(b: Bundle) -> list[str]:
    """Any personal field left in clear anywhere in the bundle. For a test and a guard; a bundle built here has none."""
    found: list[str] = []

    def walk(path: str, value: Any) -> None:
        if isinstance(value, Mapping):
            for k, v in value.items():
                if k.casefold() in PERSONAL_FIELDS and not (
                    isinstance(v, str) and v.startswith("redacted:")
                ):
                    found.append(f"{path}.{k}")
                walk(f"{path}.{k}", v)
        elif isinstance(value, list | tuple):
            for n, v in enumerate(value):
                walk(f"{path}[{n}]", v)

    for i in b.included:
        walk(f"{i['kind']}:{i['ref']}", i["data"])
    return found
