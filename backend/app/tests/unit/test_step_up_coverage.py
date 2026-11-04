"""Which routes call the step-up guard, and for which action (ZID-ENG-DEC-009, slice L-9).

A route that must ask for fresh strong authentication and quietly does not is the failure the record warns about, and no behaviour test
notices it while enforcement is in shadow. This register names each guarded route and the catalogue action it must name, and fails if a
route loses its guard, names another action, or names an action the catalogue does not classify."""

from __future__ import annotations

import ast
from pathlib import Path

import pytest
from zid_platform.modules.authentication.infrastructure.step_up_config import step_up_catalogue

MODULES = Path(__file__).resolve().parents[2] / "src" / "zid_platform" / "modules"

# (file under modules/, route function, the catalogue action it names)
GUARDED = [
    ("credential/api/credentials.py", "rotate_trust_keys", "rotate-revoke-critical-enterprise-keys-or-production-certificates"),
    ("credential/api/credentials.py", "register_rp", "authorize-new-high-risk-oauth-scopes-relying-party-trust"),
    ("evidence/api/evidence.py", "bulk_export", "export-raw-id-proof-biometric-evidence-ubo-records-or-bulk-pii"),
    ("evidence/api/evidence.py", "release_hold", "delete-regulated-identity-records-or-alter-retention-holds"),
    ("session/api/sessions.py", "revoke_all", "revoke-own-sessions-or-all-other-sessions"),
    ("session/api/sessions.py", "admin_revoke_all", "revoke-own-sessions-or-all-other-sessions"),
    ("tenant/api/approvals.py", "cast_vote", "execute-sensitive-approval-rejection"),
    ("tenant/api/tenants.py", "grant_privileged_role", "promote-user-to-admin-approver-change-privileged-role-grants"),
    ("tenant/api/tenants.py", "add_owner", "initiate-confirm-transfer-of-organization-ownership"),
    ("tenant/api/tenants.py", "remove_owner", "initiate-confirm-transfer-of-organization-ownership"),
    ("federation/api/federation.py", "create_integration", "change-sso-federation-trust-or-scim-privilege-mapping"),
    ("provisioning/api/scim.py", "create_source", "change-sso-federation-trust-or-scim-privilege-mapping"),
    ("authentication/api/webauthn.py", "revoke_authenticator", "enroll-remove-replace-passkey-mfa-or-recovery-factor"),
    ("tenant/api/residency.py", "put_residency", "change-customer-home-region-cross-region-transfer-policy"),
    ("tenant/api/residency.py", "add_rule", "change-customer-home-region-cross-region-transfer-policy"),
]  # fmt: skip


def _function(path: str, name: str) -> tuple[ast.AsyncFunctionDef, dict[str, str]]:
    tree = ast.parse((MODULES / path).read_text(encoding="utf-8"))
    constants = {
        t.id: str(n.value.value)
        for n in tree.body
        if isinstance(n, ast.Assign)
        and isinstance(n.value, ast.Constant)
        and isinstance(n.value.value, str)
        for t in n.targets
        if isinstance(t, ast.Name)
    }
    for node in ast.walk(tree):
        if isinstance(node, ast.AsyncFunctionDef) and node.name == name:
            return node, constants
    raise AssertionError(f"{path}: no route function {name}")


def _guard_actions(fn: ast.AsyncFunctionDef, constants: dict[str, str]) -> set[str]:
    """The `action=` of every guard call inside the route, including one in a nested helper; a module constant is resolved."""
    found: set[str] = set()
    for node in ast.walk(fn):
        if isinstance(node, ast.Call):
            callee = (
                node.func.attr
                if isinstance(node.func, ast.Attribute)
                else getattr(node.func, "id", "")
            )
            if callee in ("require_step_up", "composed_step_up"):
                for kw in node.keywords:
                    if kw.arg == "action" and isinstance(kw.value, ast.Constant):
                        found.add(str(kw.value.value))
                    elif (
                        kw.arg == "action"
                        and isinstance(kw.value, ast.Name)
                        and kw.value.id in constants
                    ):
                        found.add(constants[kw.value.id])
    return found


@pytest.mark.parametrize(("path", "route", "action"), GUARDED, ids=[f"{r}" for _, r, _ in GUARDED])
def test_the_route_asks_for_the_action_the_register_names(
    path: str, route: str, action: str
) -> None:
    assert action in step_up_catalogue().actions, "the catalogue does not classify this action"
    fn, constants = _function(path, route)
    assert action in _guard_actions(fn, constants), (
        f"{route} does not call the step-up guard for {action}"
    )
