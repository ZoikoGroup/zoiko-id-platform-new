"""Say which Stripe account the configured key belongs to (CR-062).

    python -m zid_platform.entrypoints.stripe_account

Reads `ZID_STRIPE_SECRET_KEY_FILE` (the file the cell reads), asks Stripe whose key it is and prints the account id, its name and test or live mode. It never prints the key.
If `ZID_STRIPE_EXPECTED_ACCOUNT` is set to an account id (`acct_...`) and the key belongs to a different account the exit code is 1, so a deployment check can refuse to
start with another product's keys (a test run on the Rooms account's keys charges and invoices into the Rooms account).
"""

from __future__ import annotations

import json
import os
import sys
from pathlib import Path

import httpx


def main() -> int:
    path = os.environ.get("ZID_STRIPE_SECRET_KEY_FILE")
    key = (
        Path(path).read_text(encoding="utf-8").strip()
        if path
        else os.environ.get("ZID_STRIPE_SECRET_KEY", "").strip()
    )
    if not key:
        print(
            "Neither ZID_STRIPE_SECRET_KEY_FILE nor ZID_STRIPE_SECRET_KEY is set: no payment provider is configured.",
            file=sys.stderr,
        )
        return 2
    base = os.environ.get("ZID_STRIPE_API_BASE", "https://api.stripe.com").rstrip("/")
    response = httpx.get(f"{base}/v1/account", auth=(key, ""), timeout=10)
    if response.status_code != 200:
        print(f"Stripe refused the key (HTTP {response.status_code}).", file=sys.stderr)
        return 2
    account = response.json()
    profile = account.get("business_profile") or {}
    name = (
        profile.get("name")
        or (account.get("settings") or {}).get("dashboard", {}).get("display_name")
        or ""
    )
    mode = "live" if key.startswith(("sk_live_", "rk_live_")) else "test"
    expected = os.environ.get("ZID_STRIPE_EXPECTED_ACCOUNT", "").strip()
    print(
        json.dumps(
            {"account": account.get("id"), "name": name, "mode": mode, "expected": expected or None}
        )
    )
    if expected and expected != account.get("id"):
        print(
            f"This key belongs to {account.get('id')}, not the expected account {expected}.",
            file=sys.stderr,
        )
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
