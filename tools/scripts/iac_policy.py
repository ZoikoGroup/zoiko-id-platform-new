"""OpenTofu plan policy (DEC-G4 §5; ZID-DEVSECOPS-001 §10). Standard library only.

    tofu plan -out=tfplan && tofu show -json tfplan > plan.json && python3 tools/scripts/iac_policy.py plan.json

Blocks a plan that would create or change infrastructure into a forbidden state: public exposure, wildcard IAM,
missing encryption, long-lived cloud keys, or a region outside the approved list. Exit 1 on any violation.
Covers the AWS and Google resource types (DEC-G2 names AWS; the current deployment is on GCP).
"""

from __future__ import annotations

import json
import sys
from pathlib import Path
from typing import Any

PUBLIC_CIDRS = {"0.0.0.0/0", "::/0"}
PUBLIC_MEMBERS = {"allUsers", "allAuthenticatedUsers"}
BROAD_ROLES = {"roles/owner", "roles/editor"}
SENSITIVE_PORTS = {22, 3389, 5432, 3306, 6379, 9092}


def _as_list(v: Any) -> list[Any]:
    return v if isinstance(v, list) else ([] if v is None else [v])


def _wildcard_statement(doc: Any) -> bool:
    if isinstance(doc, str):
        try:
            doc = json.loads(doc)
        except ValueError:
            return False
    for st in _as_list((doc or {}).get("Statement")):
        if st.get("Effect") == "Allow" and ("*" in _as_list(st.get("Action")) or "*" in _as_list(st.get("Resource"))):
            return True
    return False


def check_resource(rtype: str, a: dict[str, Any], approved_regions: set[str]) -> list[str]:  # noqa: C901, PLR0912
    out: list[str] = []
    if rtype in {"aws_iam_policy", "aws_iam_role_policy", "aws_iam_user_policy", "aws_iam_group_policy"}:
        if _wildcard_statement(a.get("policy")):
            out.append("IAM_WILDCARD")
    if rtype in {"aws_security_group_rule", "aws_security_group"}:
        rules = [a] if rtype.endswith("rule") and a.get("type") == "ingress" else _as_list(a.get("ingress"))
        for r in rules:
            cidrs = set(_as_list(r.get("cidr_blocks")) + _as_list(r.get("ipv6_cidr_blocks")))
            lo, hi = r.get("from_port", 0), r.get("to_port", 65535)
            if cidrs & PUBLIC_CIDRS and (lo == 0 or any(lo <= p <= hi for p in SENSITIVE_PORTS)):
                out.append("PUBLIC_INGRESS")
    if rtype == "aws_s3_bucket_public_access_block":
        keys = ("block_public_acls", "block_public_policy", "ignore_public_acls", "restrict_public_buckets")
        if not all(a.get(k) for k in keys):
            out.append("PUBLIC_BUCKET")
    if rtype == "aws_s3_bucket_acl" and a.get("acl") in {"public-read", "public-read-write"}:
        out.append("PUBLIC_BUCKET")
    if rtype in {"aws_db_instance", "aws_rds_cluster"}:
        if not a.get("storage_encrypted"):
            out.append("UNENCRYPTED_DATABASE")
        if a.get("publicly_accessible"):
            out.append("PUBLIC_DATABASE")
        if rtype == "aws_db_instance" and a.get("deletion_protection") is False:
            out.append("NO_DELETION_PROTECTION")
    if rtype == "aws_ebs_volume" and not a.get("encrypted"):
        out.append("UNENCRYPTED_VOLUME")
    if rtype == "aws_iam_access_key":
        out.append("STATIC_CLOUD_KEY")
    if rtype in {"google_service_account_key", "google_storage_hmac_key"}:
        out.append("STATIC_CLOUD_KEY")
    if rtype.startswith("google_") and (rtype.endswith(("_iam_member", "_iam_binding"))):
        members = set(_as_list(a.get("member")) + _as_list(a.get("members")))
        if members & PUBLIC_MEMBERS:
            out.append("PUBLIC_IAM_MEMBER")
        if a.get("role") in BROAD_ROLES:
            out.append("IAM_WILDCARD")
    if rtype == "google_compute_firewall" and a.get("direction", "INGRESS") == "INGRESS":
        if set(_as_list(a.get("source_ranges"))) & PUBLIC_CIDRS:
            out.append("PUBLIC_INGRESS")
    if rtype == "google_sql_database_instance":
        ip = (_as_list((a.get("settings") or [{}])[0].get("ip_configuration")) or [{}])[0]
        if ip.get("ipv4_enabled") and any(n.get("value") in PUBLIC_CIDRS for n in _as_list(ip.get("authorized_networks"))):
            out.append("PUBLIC_DATABASE")
        if a.get("deletion_protection") is False:
            out.append("NO_DELETION_PROTECTION")
    if rtype == "google_storage_bucket" and a.get("public_access_prevention") == "inherited":
        out.append("PUBLIC_BUCKET")
    if rtype == "google_compute_disk" and not (a.get("disk_encryption_key") or [{}]):
        out.append("UNENCRYPTED_VOLUME")
    region = a.get("region") or a.get("location")
    if approved_regions and isinstance(region, str) and region.lower() not in approved_regions:
        out.append("REGION_NOT_APPROVED")
    return out


def evaluate_plan(plan: dict[str, Any], approved_regions: set[str] | None = None) -> list[str]:
    """`<address>: <CODE>` for every violating resource that the plan creates or updates."""
    violations = []
    for rc in plan.get("resource_changes", []):
        actions = rc.get("change", {}).get("actions", [])
        if actions in (["no-op"], ["read"], ["delete"]):
            continue
        after = rc.get("change", {}).get("after") or {}
        violations += [f"{rc['address']}: {c}" for c in check_resource(rc["type"], after, approved_regions or set())]
    return violations


def main() -> int:
    plan = json.loads(Path(sys.argv[1]).read_text(encoding="utf-8"))
    regions = {r.lower() for r in sys.argv[2:]}
    problems = evaluate_plan(plan, regions)
    for p in problems:
        print(f"DENY {p}")
    return 1 if problems else 0


if __name__ == "__main__":
    sys.exit(main())
