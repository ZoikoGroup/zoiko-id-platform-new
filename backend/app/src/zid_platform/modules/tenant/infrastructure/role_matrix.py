"""Load the role by feature matrices (`backend/config/roles/role-matrix.yaml`; ZID_PLATFORM_CONFIG overrides the config root).
Versioned configuration, validated on load: a matrix that breaks the record's invariants does not start."""

from __future__ import annotations

from functools import lru_cache
from typing import Any

from zid_platform.modules.tenant.domain.role_matrix import Matrix, MatrixError, Mode, Row, build
from zid_platform.modules.tenant.infrastructure.catalog import CONFIG, _yaml


def parse(doc: dict[str, Any]) -> Matrix:
    rows: list[Row] = []
    titles: dict[str, str] = {}
    for key, body in (doc.get("matrices") or {}).items():
        titles[str(key)] = str(body.get("title", ""))
        for a in body.get("actions") or []:
            try:
                cells = {str(r): Mode(str(c)) for r, c in (a.get("cells") or {}).items()}
            except ValueError as e:
                raise MatrixError(f"{a.get('id')}: unknown permission key") from e
            rows.append(
                Row(str(a["id"]), str(key), str(a["feature"]), cells, str(a.get("rule", "")))
            )
    return build(str(doc.get("version", "")), titles, rows)


@lru_cache(maxsize=1)
def role_matrix() -> Matrix:
    return parse(_yaml(CONFIG / "roles" / "role-matrix.yaml"))


@lru_cache(maxsize=1)
def direct_admin_invitation_allowed() -> bool:
    """Owner-ruled: may an Administrator still be invited directly? Anything but an explicit ALLOWED is a refusal."""
    doc = _yaml(CONFIG / "roles" / "role-policy.yaml")
    return str(doc.get("direct_admin_invitation", "REFUSED")).upper() == "ALLOWED"


@lru_cache(maxsize=1)
def billing_access_enforced() -> bool:
    """Owner-ruled (CL-02): is billing limited to the Owner and the Owner's grants? Anything but an explicit REPORT_ONLY is
    enforcement, so a missing or misspelt setting never opens billing to every administrator."""
    doc = _yaml(CONFIG / "roles" / "role-policy.yaml")
    return str(doc.get("billing_access_enforcement", "ENFORCED")).upper() != "REPORT_ONLY"


__all__ = ["billing_access_enforced", "direct_admin_invitation_allowed", "parse", "role_matrix"]
