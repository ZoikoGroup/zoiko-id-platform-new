"""Hash an Operator Console password for ZID_OPERATOR_PASSWORD_LOGINS / ZID_OPERATOR_LOGINS_FILE.

    python -m zid_platform.entrypoints.hash_operator_password

Prompts for the password (not echoed, asked twice, at least 12 characters) and prints the scrypt hash to put in the
operator's `password_hash`. The password itself is never printed or stored.
"""

from __future__ import annotations

import getpass
import sys

from zid_platform.modules.authentication.domain.operator_password import MIN_LENGTH, hash_password


def main() -> int:
    password = getpass.getpass("Operator password: ")
    if password != getpass.getpass("Again: "):
        print("The two entries differ.", file=sys.stderr)
        return 1
    try:
        print(hash_password(password))
    except ValueError:
        print(f"The password must be at least {MIN_LENGTH} characters.", file=sys.stderr)
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
