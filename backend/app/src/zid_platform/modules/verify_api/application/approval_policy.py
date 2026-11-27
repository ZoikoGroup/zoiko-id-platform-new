"""One switch for the Verify API's "a different person decides" rules: go-live review and approval, a proposed price, a production credential.

ZID_VERIFY_SINGLE_APPROVER=on lets the person who asked also decide. It is read on each call, so a deployment can change it with a restart. Unset or anything else keeps
the rule. Only the person-versus-person check is relaxed: the signed grant, the registry-verified company, the sandbox checks, the attestations and the single-use,
bound approval record all stay in force.
"""

from __future__ import annotations

import os


def single_approver() -> bool:
    return os.environ.get("ZID_VERIFY_SINGLE_APPROVER", "").strip().lower() in {
        "on",
        "true",
        "1",
        "yes",
    }
