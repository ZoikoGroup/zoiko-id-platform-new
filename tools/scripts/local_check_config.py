"""Local development only: a copy of backend/config with the WF-05 check methods testable (adapters listed, retention marked APPROVED for dev).

The repository config ships no adapters and leaves every retention rule UNSET (a Legal/DPO decision, ZID-DTV-WF-05 §23), so on a local cell
every check method is refused (NO_ADAPTER, RETENTION_NOT_APPROVED). This writes a copy OUTSIDE the repository and prints the line to put in
backend/.env (ZID_PLATFORM_CONFIG). Nothing here is a real retention approval. Re-run after pulling config changes, then restart the backend.

    uv run python ../tools/scripts/local_check_config.py
"""

from __future__ import annotations

import os
import shutil
from pathlib import Path

import yaml

REPO_CONFIG = Path(__file__).resolve().parents[2] / "backend" / "config"
TARGET = Path(os.environ.get("LOCALAPPDATA") or Path.home()) / "zoiko-id" / "platform-config"

# The methods whose flows run locally (codes reach Mailpit through the dev sender). Voice, carrier and licensed data sources have no adapter.
ADAPTERS = {
    "EMAIL_CHALLENGE": {"engine_id": "zid-email-challenge", "engine_version": "1", "route": "OPEN"},
    "PHONE_SMS_CHALLENGE": {"engine_id": "zid-sms-challenge", "engine_version": "1", "route": "OPEN"},
    "ADDRESS_LOOKUP": {"engine_id": "zid-address-lookup", "engine_version": "1", "route": "OPEN"},
    "ADDRESS_DOCUMENT": {"engine_id": "zid-address-document", "engine_version": "1", "route": "MANUAL"},
    "ADDRESS_POSTAL_CHALLENGE": {"engine_id": "zid-postal-challenge", "engine_version": "1", "route": "OPEN"},
    "DEVICE_RISK": {"engine_id": "zid-device-risk", "engine_version": "1", "route": "OPEN"},
}


def main() -> None:
    if TARGET.exists():
        shutil.rmtree(TARGET)
    shutil.copytree(REPO_CONFIG, TARGET)
    (TARGET / "verification" / "check-adapters.yaml").write_text(
        "# LOCAL DEVELOPMENT COPY (tools/scripts/local_check_config.py)\n" + yaml.safe_dump({"adapters": ADAPTERS}, sort_keys=True),
        encoding="utf-8",
    )
    retention_file = TARGET / "privacy" / "check-data-classes.yaml"
    classes = yaml.safe_load(retention_file.read_text(encoding="utf-8"))["classes"]
    dev = {"status": "APPROVED", "retention_days": 30, "approved_by": "local-dev", "legal_basis_ref": "LOCAL-DEV-NOT-AN-APPROVAL"}
    retention_file.write_text(
        "# LOCAL DEVELOPMENT COPY: not a real retention approval\n" + yaml.safe_dump({"classes": {c: dict(dev) for c in classes}}, sort_keys=True),
        encoding="utf-8",
    )
    print(f"wrote {TARGET}")
    print(f"add to backend/.env:  ZID_PLATFORM_CONFIG={TARGET.as_posix()}")


if __name__ == "__main__":
    main()
