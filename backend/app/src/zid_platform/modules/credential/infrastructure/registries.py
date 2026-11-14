"""Load the credential trust configuration: issuer registry, source contracts, eligibility policies and tier rules.

`backend/config/credential-trust/*` (ZID_PLATFORM_CONFIG overrides the config root). Versioned configuration: a source, issuer or
policy is switched on or off by editing the file, never code. A file that fails validation is refused whole; there is no
partial registry. Every file ships SAMPLE rows only: nothing real is listed until the owners add it.
"""

from __future__ import annotations

import os
from datetime import UTC, datetime
from functools import lru_cache
from pathlib import Path
from typing import Any

import yaml
from sqlalchemy.ext.asyncio import AsyncConnection

from zid_platform.modules.credential.domain import eligibility as el
from zid_platform.modules.credential.domain import fairness as fa
from zid_platform.modules.credential.domain import practice as pr
from zid_platform.modules.credential.domain import sources as src
from zid_platform.modules.credential.domain import trust_graph as tg

CONFIG = Path(
    os.environ.get("ZID_PLATFORM_CONFIG") or Path(__file__).resolve().parents[6] / "config"
)
DIR = "credential-trust"


def _doc(name: str) -> dict[str, Any]:
    return yaml.safe_load((CONFIG / DIR / name).read_text(encoding="utf-8")) or {}


def parse_policies(doc: dict[str, Any]) -> tuple[list[el.EligibilityPolicy], list[el.TierRule]]:
    try:
        policies = [
            el.EligibilityPolicy(
                policy_id=p["policy_id"],
                version=int(p["version"]),
                country=p["country"].upper(),
                subdivision=p.get("subdivision"),
                profession_code=p["profession_code"],
                service_code=p["service_code"],
                required_claim_types=tuple(
                    p.get("required_claim_types") or ("PROFESSIONAL_LICENSE",)
                ),
                recognition_required_for=tuple(p.get("recognition_required_for") or ()),
                max_assertion_age_days=int(p.get("max_assertion_age_days", 365)),
            )
            for p in doc.get("policies") or []
        ]
        rules = [
            el.TierRule(
                rule_id=r["rule_id"],
                version=int(r["version"]),
                tier_a=tuple(el.Dimension(d) for d in r["tier_a"]),
                tier_b=tuple(el.Dimension(d) for d in r["tier_b"]),
                tier_c=tuple(el.Dimension(d) for d in r.get("tier_c") or ["IDENTITY"]),
                mandatory=tuple(el.Dimension(d) for d in r.get("mandatory") or ()),
            )
            for r in doc.get("tier_rules") or []
        ]
    except (KeyError, ValueError, TypeError) as e:
        raise ValueError(f"ELIGIBILITY_POLICY_INVALID: {str(e)[:80]}") from e
    keys = [(p.policy_id, p.version) for p in policies]
    if len(keys) != len(set(keys)):
        raise ValueError("ELIGIBILITY_POLICY_INVALID: duplicate policy version")
    return policies, rules


@lru_cache(maxsize=1)
def issuer_registry() -> tg.Registry:
    return tg.parse(_doc("issuer-registry.yaml"))


@lru_cache(maxsize=1)
def source_contracts() -> tuple[src.SourceContract, ...]:
    return tuple(src.parse(_doc("source-contracts.yaml")))


@lru_cache(maxsize=1)
def policies() -> tuple[list[el.EligibilityPolicy], list[el.TierRule]]:
    return parse_policies(_doc("eligibility-policies.yaml"))


async def current_issuer_registry(conn: AsyncConnection) -> tg.Registry:
    """The registry a check uses now: the configured registry with the issuer ledger (CR-075) layered over it. A statement recorded through the
    operator API replaces the configured row for that issuer; with no statements this is exactly the configured registry. Read per request, so
    every server process sees a new statement on its next request."""
    from zid_platform.modules.credential.infrastructure.issuer_history_store import registry_at

    return await registry_at(conn, datetime.now(UTC), base=issuer_registry())


@lru_cache(maxsize=1)
def practice_rules() -> tuple[pr.PracticeRule, ...]:
    return tuple(pr.parse(_doc("practice-rules.yaml")))


def find_practice_rule(policy_id: str) -> pr.PracticeRule | None:
    """The highest version of the practice rule for this policy; None if the owners have published none (the engagement is then decided as before)."""
    found = [r for r in practice_rules() if r.policy_id == policy_id]
    return max(found, key=lambda r: r.version) if found else None


def find_policy(
    country: str, subdivision: str | None, profession: str, service: str
) -> el.EligibilityPolicy | None:
    """The highest version of the policy for exactly this place, profession and service; None if there is none."""
    found = [
        p
        for p in policies()[0]
        if p.country == country.upper()
        and p.subdivision == subdivision
        and p.profession_code == profession
        and p.service_code == service
    ]
    return max(found, key=lambda p: p.version) if found else None


def fairness_threshold() -> fa.Threshold | None:
    """The approved disparity threshold, or None (T44): the file is owner content and ships unapproved. Read on every use, so approving it
    takes effect at the next check without a restart."""
    return fa.parse_threshold(_doc("fairness-thresholds.yaml"))
