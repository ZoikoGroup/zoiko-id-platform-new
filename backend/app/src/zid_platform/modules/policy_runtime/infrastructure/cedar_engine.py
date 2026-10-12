"""Cedar policy engine via cedarpy (DEC-C3; ZID-AUTHZ-001 §4, §18.1, §23.1, §25).

- One Cedar schema per tenant, generated from the action catalog; policies that reference anything
  undeclared fail validation before they can publish.
- Pre-publication checks: schema/type validation, the release's own allow/deny cases, and the
  authority invariant: no permit that can apply to an R2/R3 action may skip `context.authority_valid`.
  Authority algebra stays in the authority module (DEC-C3); Cedar only sees the verified fact.
- Every evaluation runs the tenant release *together with* the platform floor; forbid beats permit.
- Missing context is filled with deny-safe defaults, and any evaluation error is a DENY with
  POLICY_EVALUATION_ERROR, never a silent allow ("zero silent fail-open", AUTHZ §25).
- Compiled bundles are cached by release content hash: published releases are immutable.
"""

from __future__ import annotations

import json
import os
from collections.abc import Mapping
from dataclasses import dataclass
from functools import lru_cache
from pathlib import Path
from typing import Any

import cedarpy

from zid_platform.modules.policy_runtime.domain.release import PolicyError, PolicyRelease

POLICY_DIR = Path(
    os.environ.get("ZID_POLICY_DIR") or Path(__file__).resolve().parents[6] / "policies"
)
FLOOR_FILE = POLICY_DIR / "cedar" / "platform-floor" / "floor.cedar"

PRINCIPAL_TYPE = "Principal"
RESOURCE_TYPE = "Resource"

# Context every decision carries, with deny-safe defaults for anything the caller left out.
CONTEXT_DEFAULTS: dict[str, object] = {
    "tenant_match": False,
    "authority_valid": False,
    "authority_stale": True,
    "security_state": "UNKNOWN",
    "risk_class": 3,
    "authn_level": 0,
    "purpose": "",
    "value_minor": 0,
}
_CEDAR_TYPES = {bool: "Boolean", int: "Long", str: "String"}


def build_schema(actions: Mapping[str, Mapping[str, Any]]) -> dict[str, Any]:
    """Cedar JSON schema for a tenant's action catalog (DEC-C3: generated, never hand-written)."""
    context = {
        "type": "Record",
        "attributes": {
            k: {"type": _CEDAR_TYPES[type(v)], "required": True}
            for k, v in CONTEXT_DEFAULTS.items()
        },
    }
    entity = {
        "shape": {
            "type": "Record",
            "attributes": {"tenant_id": {"type": "String", "required": False}},
        }
    }
    return {
        "": {
            "entityTypes": {PRINCIPAL_TYPE: entity, RESOURCE_TYPE: entity},
            "actions": {
                name: {
                    "appliesTo": {
                        "principalTypes": [PRINCIPAL_TYPE],
                        "resourceTypes": [RESOURCE_TYPE],
                        "context": context,
                    }
                }
                for name in sorted(actions)
            },
        }
    }


def high_risk_actions(actions: Mapping[str, Mapping[str, Any]]) -> frozenset[str]:
    return frozenset(a for a, spec in actions.items() if spec.get("min_risk_class") in ("R2", "R3"))


@lru_cache(maxsize=1)
def floor_policies() -> str:
    return FLOOR_FILE.read_text(encoding="utf-8")


@dataclass(frozen=True, slots=True)
class Outcome:
    allowed: bool
    determining: tuple[str, ...]  # policy ids that decided (diagnostics, no secrets)
    codes: tuple[str, ...]


def _actions_in_scope(scope: Mapping[str, Any], all_actions: frozenset[str]) -> frozenset[str]:
    op = scope.get("op")
    if op == "All":
        return all_actions
    if op == "==":
        return frozenset({scope["entity"]["id"]})
    if op == "in":
        ents = scope.get("entities") or [scope.get("entity")]
        return frozenset(e["id"] for e in ents if e)
    return all_actions  # unknown shape: treat as broadest, so the check stays conservative


def authority_invariant_violations(
    policies: str, catalog: Mapping[str, Mapping[str, Any]]
) -> list[str]:
    """Permits reachable by an R2/R3 action that don't require context.authority_valid (AUTHZ §18.1)."""
    est = json.loads(cedarpy.policies_to_json_str(policies))
    risky = high_risk_actions(catalog)
    out = []
    for pid, pol in est.get("staticPolicies", {}).items():
        if pol.get("effect") != "permit":
            continue
        if not _actions_in_scope(pol.get("action", {}), frozenset(catalog)) & risky:
            continue
        whens = [c for c in pol.get("conditions", []) if c.get("kind") == "when"]
        if not any('"authority_valid"' in json.dumps(c.get("body")) for c in whens):
            name = pol.get("annotations", {}).get("id", pid)
            out.append(f"{name}: permits an R2/R3 action without requiring context.authority_valid")
    return out


def _uid(type_name: str, id_: str) -> dict[str, str]:
    return {"type": type_name, "id": id_}


def evaluate(
    release: PolicyRelease,
    *,
    principal_id: str,
    action: str,
    resource_id: str,
    context: Mapping[str, object],
    schema: Mapping[str, Any],
) -> Outcome:
    """Evaluate one request against tenant release + platform floor. Errors deny."""
    full_context = {**CONTEXT_DEFAULTS, **context}
    try:
        bundle = _bundle(release.content_hash, release.policies)
        result = cedarpy.is_authorized(
            {
                "principal": _uid(PRINCIPAL_TYPE, principal_id),
                "action": _uid("Action", action),
                "resource": _uid(RESOURCE_TYPE, resource_id),
                "context": full_context,
            },
            bundle.policy_set,
            [],
            schema=json.dumps(schema),
        )
    except Exception as exc:  # noqa: BLE001 - the engine never fails open
        return Outcome(False, (), ("POLICY_EVALUATION_ERROR", type(exc).__name__))
    errors = list(result.diagnostics.errors)
    if errors:
        return Outcome(False, (), ("POLICY_EVALUATION_ERROR",))
    names = tuple(bundle.names.get(r, r) for r in result.diagnostics.reasons)
    if result.allowed:
        return Outcome(True, names, ("POLICY_PERMIT",))
    forbid = any(r in bundle.forbids for r in result.diagnostics.reasons)
    return Outcome(False, names, ("POLICY_FORBID",) if forbid else ("POLICY_NO_PERMIT",))


@dataclass(frozen=True, slots=True)
class _Bundle:
    policy_set: Any
    names: dict[str, str]  # Cedar positional id (policyN) -> @id annotation
    forbids: frozenset[str]  # positional ids of forbid policies


@lru_cache(maxsize=256)
def _bundle(content_hash: str, tenant_policies: str) -> _Bundle:
    """Compiled tenant release + floor, keyed by the release's content hash (immutable releases)."""
    source = f"{tenant_policies}\n{floor_policies()}"
    est = json.loads(cedarpy.policies_to_json_str(source)).get("staticPolicies", {})
    return _Bundle(
        policy_set=cedarpy.PolicySet.from_str(source),
        names={pid: pol.get("annotations", {}).get("id", pid) for pid, pol in est.items()},
        forbids=frozenset(pid for pid, pol in est.items() if pol.get("effect") == "forbid"),
    )


def prepublication_problems(
    release: PolicyRelease, catalog: Mapping[str, Mapping[str, Any]]
) -> list[str]:
    """Blocking checks before a release can leave VALIDATING (AUTHZ §18.1)."""
    schema = build_schema(catalog)
    try:
        validation = cedarpy.validate_policies(
            release.policies + "\n" + floor_policies(), json.dumps(schema)
        )
    except Exception as exc:  # noqa: BLE001
        return [f"POLICY_PARSE_ERROR: {exc}"]
    if not validation.validation_passed:
        return [f"POLICY_SCHEMA_INVALID: {e}" for e in validation.errors]
    problems = authority_invariant_violations(release.policies, catalog)
    if not release.cases:
        problems.append("POLICY_TESTS_REQUIRED: a release needs allow and deny cases")
    for case in release.cases:
        out = evaluate(
            release,
            principal_id=case.principal,
            action=case.action,
            resource_id=case.resource,
            context=case.context,
            schema=schema,
        )
        if out.allowed != case.expect_allow:
            problems.append(
                f"POLICY_TEST_FAILED: {case.name} expected {'ALLOW' if case.expect_allow else 'DENY'}"
            )
    return problems


def require_publishable(release: PolicyRelease, catalog: Mapping[str, Mapping[str, Any]]) -> None:
    problems = prepublication_problems(release, catalog)
    if problems:
        raise PolicyError(problems[0].split(":")[0], "; ".join(problems))
