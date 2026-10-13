"""OpenID Federation 1.1 trust-chain rules (ZID-AUTH-001 §12; ZID-VC-001 trust federation).

Pure domain over *signature-verified* entity statements (the application layer verifies each
signature with the key the chain itself vouches for). Rules:
- a chain is [leaf entity configuration, subordinate statements ..., trust anchor configuration];
  every statement is unexpired, issued by the next entity up, and about the one below;
- the anchor is one the tenant configured, with pinned keys (discovery is never trust bootstrap);
- `crit` claims we do not understand reject the statement; `max_path_length` and naming
  constraints are enforced; the chain expires with its earliest statement;
- metadata policy is merged top-down (a subordinate may only narrow) and applied to the leaf's
  metadata; a policy conflict or a violated `essential`/`one_of`/`subset_of` fails the chain.
"""

from __future__ import annotations

from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from typing import Any
from urllib.parse import urlsplit

ENTITY_TYP = "entity-statement+jwt"
SKEW = timedelta(seconds=60)
MAX_CHAIN = 6
UNDERSTOOD_CRIT: frozenset[str] = frozenset()  # no extension claims are supported yet
OPERATORS = ("value", "add", "default", "one_of", "subset_of", "superset_of", "essential")


class TrustChainError(ValueError):
    def __init__(self, code: str, detail: str = "") -> None:
        super().__init__(f"{code}: {detail}" if detail else code)
        self.code = code


@dataclass(frozen=True, slots=True)
class Statement:
    iss: str
    sub: str
    iat: datetime
    exp: datetime
    jwks: Mapping[str, Any] | None
    metadata: Mapping[str, Any]
    metadata_policy: Mapping[str, Any]
    authority_hints: tuple[str, ...]
    constraints: Mapping[str, Any]

    @property
    def self_signed(self) -> bool:
        return self.iss == self.sub


def parse(claims: Mapping[str, Any], *, now: datetime) -> Statement:
    for name in ("iss", "sub"):
        if not isinstance(claims.get(name), str) or not str(claims[name]).startswith("https://"):
            raise TrustChainError("ENTITY_STATEMENT_INVALID", name)
    iat, exp = claims.get("iat"), claims.get("exp")
    if not isinstance(iat, int | float) or not isinstance(exp, int | float):
        raise TrustChainError("ENTITY_STATEMENT_INVALID", "iat/exp")
    issued, expires = datetime.fromtimestamp(iat, UTC), datetime.fromtimestamp(exp, UTC)
    if issued > now + SKEW:
        raise TrustChainError("ENTITY_STATEMENT_NOT_YET_VALID", str(claims["sub"]))
    if now >= expires:
        raise TrustChainError("ENTITY_STATEMENT_EXPIRED", str(claims["sub"]))
    crit = claims.get("crit") or []
    if not isinstance(crit, list) or set(crit) - UNDERSTOOD_CRIT:
        raise TrustChainError("ENTITY_STATEMENT_CRIT_UNSUPPORTED", ",".join(map(str, crit)))
    hints = claims.get("authority_hints") or []
    if not isinstance(hints, list) or not all(isinstance(h, str) for h in hints):
        raise TrustChainError("ENTITY_STATEMENT_INVALID", "authority_hints")
    return Statement(
        iss=claims["iss"],
        sub=claims["sub"],
        iat=issued,
        exp=expires,
        jwks=claims.get("jwks") if isinstance(claims.get("jwks"), dict) else None,
        metadata=claims.get("metadata") or {},
        metadata_policy=claims.get("metadata_policy") or {},
        authority_hints=tuple(hints),
        constraints=claims.get("constraints") or {},
    )


def check_links(chain: Sequence[Statement], *, anchor: str) -> datetime:
    """Shape of the chain, leaf first. Returns the chain's expiry."""
    if not 2 <= len(chain) <= MAX_CHAIN:
        raise TrustChainError("TRUST_CHAIN_LENGTH")
    leaf, top = chain[0], chain[-1]
    if not leaf.self_signed or not top.self_signed or top.sub != anchor:
        raise TrustChainError("TRUST_CHAIN_ENDPOINTS")
    for i in range(1, len(chain) - 1):  # each statement is about its issuer's subordinate below
        below, statement, above = chain[i - 1], chain[i], chain[i + 1]
        if statement.sub != below.iss or statement.iss != above.sub:
            raise TrustChainError("TRUST_CHAIN_BROKEN", f"at {statement.sub}")
        if statement.self_signed:
            raise TrustChainError("TRUST_CHAIN_BROKEN", "subordinate statement is self-signed")
    _constraints(chain)
    return min(s.exp for s in chain)


def _host(entity_id: str) -> str:
    return (urlsplit(entity_id).hostname or "").lower()


def _constraints(chain: Sequence[Statement]) -> None:
    """Each subordinate statement's constraints bind everything below it."""
    for i, statement in enumerate(chain[1:-1], start=1):
        c = statement.constraints
        max_path = c.get("max_path_length")
        intermediates_below = i - 1
        if isinstance(max_path, int) and intermediates_below > max_path:
            raise TrustChainError("TRUST_CHAIN_MAX_PATH_LENGTH")
        naming = c.get("naming_constraints") or {}
        for below in chain[:i]:
            host = _host(below.sub)
            permitted = naming.get("permitted") or []
            excluded = naming.get("excluded") or []
            if permitted and not any(_suffix(host, p) for p in permitted):
                raise TrustChainError("TRUST_CHAIN_NAMING_CONSTRAINT", host)
            if any(_suffix(host, e) for e in excluded):
                raise TrustChainError("TRUST_CHAIN_NAMING_CONSTRAINT", host)


def _suffix(host: str, rule: str) -> bool:
    rule = rule.lower()
    return host == rule.lstrip(".") if not rule.startswith(".") else host.endswith(rule)


def merge_policies(policies: Sequence[Mapping[str, Any]]) -> dict[str, dict[str, dict[str, Any]]]:
    """Top-down (anchor first). A subordinate may only make the policy stricter."""
    merged: dict[str, dict[str, dict[str, Any]]] = {}
    for policy in policies:
        for entity_type, params in policy.items():
            target = merged.setdefault(entity_type, {})
            for param, ops in params.items():
                current = target.setdefault(param, {})
                for op, value in ops.items():
                    if op not in OPERATORS:
                        raise TrustChainError("METADATA_POLICY_OPERATOR_UNSUPPORTED", op)
                    current[op] = _combine(op, current.get(op), value, param)
    return merged


def _combine(op: str, old: Any, new: Any, param: str) -> Any:
    if old is None:
        return new
    if op in ("value", "default"):
        if old != new:
            raise TrustChainError("METADATA_POLICY_CONFLICT", f"{param}.{op}")
        return old
    if op == "add":
        return sorted(set(old) | set(new))
    if op in ("one_of", "subset_of"):
        both = [v for v in old if v in new]
        if not both:
            raise TrustChainError("METADATA_POLICY_CONFLICT", f"{param}.{op}")
        return both
    if op == "superset_of":
        return sorted(set(old) | set(new))
    if op == "essential":
        return bool(old) or bool(new)
    return new


def apply_policy(metadata: Mapping[str, Any], policy: Mapping[str, Any]) -> dict[str, Any]:
    out = {k: dict(v) if isinstance(v, Mapping) else v for k, v in metadata.items()}
    for entity_type, params in policy.items():
        md: dict[str, Any] = dict(out.get(entity_type) or {})
        for param, ops in params.items():
            value = md.get(param)
            if "value" in ops:
                value = ops["value"]
            if "add" in ops:
                value = sorted(set(value or []) | set(ops["add"]))
            if value is None and "default" in ops:
                value = ops["default"]
            if value is not None and "one_of" in ops and value not in ops["one_of"]:
                raise TrustChainError("METADATA_POLICY_VIOLATION", f"{param} one_of")
            if value is not None and "subset_of" in ops:
                value = [
                    v
                    for v in (value if isinstance(value, list) else [value])
                    if v in ops["subset_of"]
                ]
            if (
                value is not None
                and "superset_of" in ops
                and not set(ops["superset_of"])
                <= set(value if isinstance(value, list) else [value])
            ):
                raise TrustChainError("METADATA_POLICY_VIOLATION", f"{param} superset_of")
            if ops.get("essential") and value in (None, []):
                raise TrustChainError("METADATA_POLICY_VIOLATION", f"{param} essential")
            if value is None:
                md.pop(param, None)
            else:
                md[param] = value
        out[entity_type] = md
    return out
