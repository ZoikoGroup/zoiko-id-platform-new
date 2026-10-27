"""Promotion gate for a release candidate (DEC-G4; ZID-DEVSECOPS-001 §5-§15, §26). Standard library only.

    python3 tools/scripts/release_gate.py CANDIDATE.json [--policy deploy/release/promotion-policy.json]
        [--exceptions deploy/release/security-exceptions.json] [--target prod] [--quarantine DIGEST ...]

The pipeline runs the cryptographic verification (cosign verify, gh attestation verify, SBOM attestation) and
writes what it verified into the candidate record; this gate then decides, fail-closed, whether the artifact may
be promoted. Anything missing, unverified, unexcepted or expired is a reason to refuse. It never reads a network.

Candidate record:
  image            registry/repo@sha256:<digest>       (a tag-only reference is refused)
  signature        {verified, identity, issuer}        what `cosign verify` established (absent = unsigned)
  provenance       {verified, builder_id, source_repo, source_ref, subject_digest, slsa_level}
  sbom             {format, subject_digest, components}   components: ["name@version", ...] from the SBOM
  findings         [{id, severity}]                    image scan result
  preprod_digest   digest that passed PREPROD          (required for target prod)
"""

from __future__ import annotations

import argparse
import json
import re
import sys
from dataclasses import dataclass, field
from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import Any

DIGEST = re.compile(r"^(?P<repo>[^@\s]+)@(?P<digest>sha256:[0-9a-f]{64})$")


@dataclass
class Decision:
    allowed: bool
    reasons: list[str] = field(default_factory=list)


def _parse_time(value: str) -> datetime:
    t = datetime.fromisoformat(value.replace("Z", "+00:00"))
    return t if t.tzinfo else t.replace(tzinfo=UTC)


def active_exceptions(exceptions: list[dict[str, Any]], now: datetime) -> set[str]:
    """Finding ids whose exception is approved, named and not yet expired (an expired one permits nothing)."""
    live = set()
    for e in exceptions:
        if not (e.get("finding") and e.get("approved_by") and e.get("expires")):
            continue
        if _parse_time(e["expires"]) > now:
            live.add(e["finding"])
    return live


def _component(c: Any) -> tuple[str, str]:
    """`(name, version)` of an SBOM component given as `name@version` or `{name, version}`."""
    if isinstance(c, dict):
        return str(c.get("name", "")).lower(), str(c.get("version", ""))
    name, _, version = str(c).rpartition("@")
    return (name or str(c)).lower(), version if name else ""


def blocked_dependencies(sbom: dict[str, Any], policy: dict[str, Any]) -> list[str]:
    """Reasons from the dependency policy (SEC-24): a known-malicious or disallowed component blocks promotion.

    An entry names a package and optionally the versions that are blocked (none listed = every version). When the
    policy requires an inventory and the SBOM lists no components, the gate cannot prove the absence of a blocked
    one, so it refuses."""
    blocked = policy.get("blocked_dependencies") or []
    components = sbom.get("components")
    if not components:
        return ["SBOM_INVENTORY_MISSING"] if policy.get("require_component_inventory") else []
    found = {_component(c) for c in components}
    reasons = []
    for entry in blocked:
        versions = {str(v) for v in entry.get("versions") or []}
        for name, version in sorted(found):
            if name == entry["name"].lower() and (not versions or version in versions):
                reasons.append(f"DEPENDENCY_BLOCKED:{name}@{version}")
    return reasons


def evaluate(
    candidate: dict[str, Any],
    policy: dict[str, Any],
    *,
    target: str,
    now: datetime,
    exceptions: list[dict[str, Any]] | None = None,
    quarantined: frozenset[str] = frozenset(),
    known_exploited: frozenset[str] | None = None,
) -> Decision:
    reasons: list[str] = []
    if policy.get("require_known_exploited_feed") and known_exploited is None:
        reasons.append(
            "KNOWN_EXPLOITED_FEED_MISSING"
        )  # without the feed the gate cannot prove none apply
    exploited = known_exploited or frozenset()
    m = DIGEST.match(candidate.get("image", ""))
    if not m:
        return Decision(False, ["NOT_DIGEST_PINNED"])
    digest = m["digest"]
    if not any(m["repo"].startswith(prefix) for prefix in policy["approved_registries"]):
        reasons.append("REGISTRY_NOT_APPROVED")
    if digest in quarantined:
        reasons.append("ARTIFACT_QUARANTINED")

    sig = candidate.get("signature") or {}
    if not sig.get("verified"):
        # Also the signer-outage case: no verifiable signature means no production artifact.
        reasons.append("SIGNATURE_UNVERIFIED")
    elif (
        sig.get("identity") not in policy["approved_signer_identities"]
        or sig.get("issuer") != policy["oidc_issuer"]
    ):
        reasons.append("SIGNER_NOT_APPROVED")

    prov = candidate.get("provenance") or {}
    if not prov.get("verified"):
        reasons.append("PROVENANCE_UNVERIFIED")
    else:
        if prov.get("builder_id") not in policy["approved_builders"]:
            reasons.append("BUILDER_NOT_APPROVED")
        if (
            prov.get("source_repo") not in policy["approved_sources"]
            or prov.get("source_ref") not in policy["release_refs"]
        ):
            reasons.append("SOURCE_NOT_APPROVED")
        if prov.get("subject_digest") != digest:
            reasons.append("PROVENANCE_DIGEST_MISMATCH")
        if int(prov.get("slsa_level", 0)) < policy["min_slsa_build_level"]:
            reasons.append("SLSA_LEVEL_TOO_LOW")

    sbom = candidate.get("sbom") or {}
    if sbom.get("format") not in policy["sbom_formats"]:
        reasons.append("SBOM_MISSING")
    elif sbom.get("subject_digest") != digest:
        reasons.append("SBOM_DIGEST_MISMATCH")

    reasons += blocked_dependencies(sbom, policy)

    excepted = active_exceptions(exceptions or [], now)
    for f in candidate.get("findings", []):
        if f.get("id") in exploited:
            # Actively exploited (SEC-30): blocks at any severity and no exception permits it; the only way
            # forward is a fixed digest, released through the emergency path with its change record.
            reasons.append(f"KNOWN_EXPLOITED_BLOCKS:{f.get('id')}")
        elif (
            f.get("severity", "").upper() in policy["blocking_severities"]
            and f.get("id") not in excepted
        ):
            reasons.append(f"VULNERABILITY_BLOCKS:{f.get('id')}")

    if target == "prod" and candidate.get("preprod_digest") != digest:
        reasons.append("NOT_THE_PREPROD_DIGEST")
    return Decision(not reasons, reasons)


MANIFEST_FIELDS = (
    "commit",
    "image",
    "provenance_digest",
    "sbom_digest",
    "config_digest",
    "approvers",
    "change_id",
)


def release_manifest(
    candidate: dict[str, Any],
    *,
    commit: str,
    config_digest: str,
    approvers: list[str],
    change_id: str,
    author: str,
) -> dict[str, Any]:
    """The release evidence record (DevSecOps §26): one deployment maps to one commit, digest, provenance,
    SBOM, config and approver set. Refuses a manifest where any link is missing or the author approved it."""
    if not re.fullmatch(r"[0-9a-f]{40}", commit):
        raise ValueError("commit must be a full 40-hex SHA")
    independent = sorted({a for a in approvers if a and a != author})
    if not independent:
        raise ValueError("a release needs at least one approver who is not its author")
    if not change_id:
        raise ValueError("a release needs a change id")
    manifest = {
        "commit": commit,
        "image": candidate["image"],
        "provenance_digest": (candidate.get("provenance") or {}).get("subject_digest"),
        "sbom_digest": (candidate.get("sbom") or {}).get("subject_digest"),
        "config_digest": config_digest,
        "approvers": independent,
        "change_id": change_id,
    }
    missing = [k for k in MANIFEST_FIELDS if not manifest.get(k)]
    if missing:
        raise ValueError(f"release manifest incomplete: {missing}")
    return manifest


def parse_known_exploited(feed: Any) -> frozenset[str]:
    """CVE ids from a CISA KEV feed (`{"vulnerabilities": [{"cveID": ...}]}`) or a plain list of ids."""
    items = feed.get("vulnerabilities", []) if isinstance(feed, dict) else feed
    if not isinstance(items, list):
        raise ValueError("known-exploited feed is not a list")
    ids = {(i.get("cveID") if isinstance(i, dict) else i) for i in items}
    return frozenset(str(i) for i in ids if i)


def kev_remediation(
    findings: list[dict[str, Any]],
    known_exploited: frozenset[str],
    first_seen: dict[str, str],
    *,
    now: datetime,
    policy: dict[str, Any],
) -> list[dict[str, Any]]:
    """What the emergency remediation workflow owes for each actively exploited finding in a deployed image:
    a due time `kev_remediation_hours` after it was first seen, and whether that has passed. A finding with no
    recorded first-seen time cannot be shown to be inside the window, so it counts as overdue."""
    window = timedelta(hours=int(policy["kev_remediation_hours"]))
    out = []
    for f in findings:
        if f.get("id") not in known_exploited:
            continue
        seen = first_seen.get(f["id"])
        due = _parse_time(seen) + window if seen else now
        out.append(
            {
                "finding": f["id"],
                "due": due.isoformat(),
                "overdue": seen is None or now >= due,
                "path": "emergency_release",  # fixed digest, named approver, change id, post-review
            }
        )
    return out


def emergency_release(
    record: dict[str, Any],
    *,
    now: datetime,
    policy: dict[str, Any],
    known_exploited: frozenset[str] | None = None,
) -> Decision:
    """Break-glass is still signed and admitted; it adds a named approver, a change id, an immediate security
    notification and a post-review due date. The normal gate is NOT bypassed (DEC-G4 §6)."""
    reasons = []
    if not record.get("approver"):
        reasons.append("NAMED_APPROVER_MISSING")
    if record.get("approver") == record.get("author"):
        reasons.append("APPROVER_IS_AUTHOR")
    if not record.get("change_id"):
        reasons.append("CHANGE_ID_MISSING")
    if not record.get("security_notified"):
        reasons.append("SECURITY_NOT_NOTIFIED")
    due = record.get("post_review_due")
    if (
        not due
        or _parse_time(due) <= now
        or (_parse_time(due) - now).days > policy["break_glass_post_review_days"]
    ):
        reasons.append("POST_REVIEW_NOT_SCHEDULED_IN_WINDOW")
    gate = evaluate(
        record["candidate"], policy, target="emergency", now=now, known_exploited=known_exploited
    )
    reasons += gate.reasons
    return Decision(not reasons, reasons)


def main() -> int:
    root = Path(__file__).resolve().parents[2]
    ap = argparse.ArgumentParser()
    ap.add_argument("candidate")
    ap.add_argument("--policy", default=str(root / "deploy/release/promotion-policy.json"))
    ap.add_argument("--exceptions", default=str(root / "deploy/release/security-exceptions.json"))
    ap.add_argument("--target", default="prod")
    ap.add_argument("--quarantine", nargs="*", default=[])
    ap.add_argument("--kev", help="CISA known-exploited-vulnerabilities feed (JSON)")
    args = ap.parse_args()
    kev = (
        parse_known_exploited(json.loads(Path(args.kev).read_text(encoding="utf-8")))
        if args.kev
        else None
    )
    decision = evaluate(
        json.loads(Path(args.candidate).read_text(encoding="utf-8")),
        json.loads(Path(args.policy).read_text(encoding="utf-8")),
        target=args.target,
        now=datetime.now(UTC),
        exceptions=json.loads(Path(args.exceptions).read_text(encoding="utf-8")),
        quarantined=frozenset(args.quarantine),
        known_exploited=kev,
    )
    print(json.dumps({"allowed": decision.allowed, "reasons": decision.reasons}))
    return 0 if decision.allowed else 1


if __name__ == "__main__":
    sys.exit(main())
