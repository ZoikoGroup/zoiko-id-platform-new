"""The identity supplier sandbox run (EDR05-01), ready for the day an account exists. Opt-in, never part of CI.

    ZID_PROOF_VERIFF_KEY=... ZID_PROOF_VERIFF_SECRET=... \\
      python tools/scripts/supplier_sandbox_proof.py veriff --base-url https://stationapi.veriff.com --out docs/evidence/veriff-sandbox.json
    ZID_PROOF_SUMSUB_TOKEN=... ZID_PROOF_SUMSUB_SECRET=... \\
      python tools/scripts/supplier_sandbox_proof.py sumsub --base-url https://api.sumsub.com --level basic-kyc-level --out docs/evidence/sumsub-sandbox.json

It drives the platform's own supplier clients with SANDBOX credentials from the environment (never from a file in the repository), creates one throwaway session or applicant with no person data,
and writes what happened: whether the supplier accepted the signed request, whether its answer parsed into the hand-off the platform expects, and the shape of its answers (field names only, no values).
That is the evidence for the open points in docs/signoff/identity-suppliers-owner-pack.md. It then needs real documents, real devices and each Wave 1 country's local checks, which this cannot do.
Attach the output with tools/scripts/record_evidence.py. Credentials are never printed or written.
"""

from __future__ import annotations

import argparse
import asyncio
import json
import os
import pathlib
import sys
import uuid

ROOT = pathlib.Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / "backend" / "app" / "src"))
sys.path.insert(0, str(ROOT / "backend" / ".venv" / "Lib" / "site-packages"))


def shape(value: object, depth: int = 0) -> object:
    """Field names and types only: nothing a supplier returned is kept as a value."""
    if isinstance(value, dict):
        return {k: shape(v, depth + 1) for k, v in value.items()} if depth < 3 else "object"
    if isinstance(value, list):
        return [shape(value[0], depth + 1)] if value else []
    return type(value).__name__


async def run(args: argparse.Namespace) -> dict[str, object]:
    from zid_platform.modules.verification.infrastructure.providers import (
        SumsubClient,
        VeriffClient,
    )

    ref = f"proof-{uuid.uuid4()}"
    env = os.environ
    if args.supplier == "veriff":
        have = {
            "ZID_PROOF_VERIFF_KEY": env.get("ZID_PROOF_VERIFF_KEY"),
            "ZID_PROOF_VERIFF_SECRET": env.get("ZID_PROOF_VERIFF_SECRET"),
        }
        client = VeriffClient(
            args.base_url,
            lambda name: env.get(name),
            "ZID_PROOF_VERIFF_KEY",
            "ZID_PROOF_VERIFF_SECRET",
        )
    else:
        have = {
            "ZID_PROOF_SUMSUB_TOKEN": env.get("ZID_PROOF_SUMSUB_TOKEN"),
            "ZID_PROOF_SUMSUB_SECRET": env.get("ZID_PROOF_SUMSUB_SECRET"),
        }
        client = SumsubClient(
            args.base_url,
            lambda name: env.get(name),
            "ZID_PROOF_SUMSUB_TOKEN",
            "ZID_PROOF_SUMSUB_SECRET",
            args.level or "basic-kyc-level",
        )
    missing = [k for k, v in have.items() if not v]
    if missing:
        return {
            "supplier": args.supplier,
            "ran": False,
            "reason": f"no sandbox credentials in the environment: {', '.join(missing)}",
        }
    result: dict[str, object] = {
        "supplier": args.supplier,
        "ran": True,
        "base_url": args.base_url,
        "session_ref": ref,
    }
    try:
        handoff = await client.create_session(ref, None)
    except Exception as e:  # noqa: BLE001 - the evidence is what the supplier did, including refusing
        return {
            **result,
            "session_created": False,
            "error": type(e).__name__,
            "detail": str(e)[:200],
        }
    result["session_created"] = handoff is not None
    if handoff is not None:
        result["handoff"] = {
            "has_id": bool(handoff.provider_ref),
            "has_url": bool(handoff.url),
            "has_token": bool(handoff.token),
        }
        if args.supplier == "veriff":
            try:
                result["decision_shape"] = shape(await client.decision(handoff.provider_ref))
            except Exception as e:  # noqa: BLE001
                result["decision_error"] = type(e).__name__
    return result


def main() -> int:
    ap = argparse.ArgumentParser(
        description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter
    )
    ap.add_argument("supplier", choices=["veriff", "sumsub"])
    ap.add_argument("--base-url", required=True)
    ap.add_argument("--level")
    ap.add_argument("--out", type=pathlib.Path)
    args = ap.parse_args()
    report = asyncio.run(run(args))
    text = json.dumps(report, indent=2)
    if args.out and report.get("ran"):
        args.out.parent.mkdir(parents=True, exist_ok=True)
        args.out.write_text(text + "\n", encoding="utf-8")
    print(text)
    return 0 if report.get("ran") and report.get("session_created") else 1


if __name__ == "__main__":
    raise SystemExit(main())
