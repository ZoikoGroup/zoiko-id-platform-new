"""The independent tester's client for the assurance plane (ZID-UAT-005): record a run and its signed manifests from a results file.

    ZID_UAT_TOKEN=... python tools/scripts/uat_client.py --base-url https://cell.example results.json

An independent party (a QA team, a penetration tester, an auditor, a recovery witness) runs its own exercise and writes down what it saw in a results
file. This tool turns that file into a run: it freezes the run, appends each case's events (idempotent: running it twice records nothing twice), and signs
one manifest per case through the API. It decides nothing: the platform's rules turn the manifest into a verdict, and a claimed PASS with an incomplete
manifest comes back INCONCLUSIVE, which this tool reports and exits non-zero for.

The bearer token is read from the environment, never from the command line, and never printed. Standard library only.

Results file (JSON):
  {"release_unit": {...}, "environment_id": "...", "build_sha256": "<64 hex>", "config_digest": "<64 hex>", "fixture_hashes": [...],
   "commit_authors": [...], "pins": {"policy_version": "..."},
   "cases": [{"case_id": "GOV-01", "events": [{"kind": "...", "payload": {...}}], "result": "PASS", "requirement_ids": [...], "trace_refs": [...],
              "provider_receipt_refs": [...], "source_snapshot_refs": [...], "measurements": {...}, "defect_id": null}]}
"""

from __future__ import annotations

import argparse
import hashlib
import json
import os
import sys
import urllib.error
import urllib.request
from collections.abc import Callable, Mapping
from typing import Any

Send = Callable[[str, str, Mapping[str, Any] | None], tuple[int, dict[str, Any]]]
RUN_FIELDS = (
    "release_unit",
    "environment_id",
    "build_sha256",
    "config_digest",
    "fixture_hashes",
    "commit_authors",
    "pins",
)
MANIFEST_FIELDS = (
    "requirement_ids",
    "trace_refs",
    "provider_receipt_refs",
    "source_snapshot_refs",
    "measurements",
    "defect_id",
    "oracle_version",
    "human_approval_refs",
)


def http_send(base_url: str, token: str, timeout: float = 30.0) -> Send:
    def send(method: str, path: str, body: Mapping[str, Any] | None) -> tuple[int, dict[str, Any]]:
        data = json.dumps(body).encode() if body is not None else None
        req = urllib.request.Request(
            base_url.rstrip("/") + path, data=data, method=method,
            headers={"Authorization": f"Bearer {token}", "Content-Type": "application/json", "Accept": "application/json"},
        )  # fmt: skip
        try:
            with urllib.request.urlopen(req, timeout=timeout) as resp:
                return resp.status, json.loads(resp.read() or b"{}")
        except urllib.error.HTTPError as e:
            try:
                return e.code, json.loads(e.read() or b"{}")
            except ValueError:
                return e.code, {"detail": "the server answered with something that is not JSON"}

    return send


def event_key(run_id: str, case_id: str, index: int, kind: str, payload: Mapping[str, Any]) -> str:
    """A key that is the same every time for the same event, so a second run of the same file records nothing twice."""
    body = json.dumps(
        [run_id, case_id, index, kind, payload], sort_keys=True, separators=(",", ":")
    )
    return "evt-" + hashlib.sha256(body.encode()).hexdigest()[:40]


class UatError(Exception):
    def __init__(self, where: str, status: int, body: Mapping[str, Any]) -> None:
        super().__init__(
            f"{where}: HTTP {status} {body.get('code', '')} {body.get('detail', '')}".strip()
        )
        self.status = status


def record(send: Send, results: Mapping[str, Any], *, run_id: str | None = None) -> dict[str, Any]:
    """Create the run (unless `run_id` names one), append every event, sign every manifest. Raises UatError on any refusal."""
    if run_id is None:
        body = {k: results[k] for k in RUN_FIELDS if k in results}
        body["case_ids"] = [c["case_id"] for c in results["cases"]]
        status, made = send("POST", "/v2/assurance/test-runs", body)
        if status != 201:
            raise UatError("create run", status, made)
        run_id = made["run_id"]
    verdicts = []
    for case in results["cases"]:
        cid = case["case_id"]
        for i, ev in enumerate(case.get("events", [])):
            payload = ev.get("payload", {})
            status, out = send(
                "POST", f"/v2/assurance/test-runs/{run_id}/events",
                {"case_id": cid, "kind": ev["kind"], "payload": payload, "idempotency_key": event_key(run_id, cid, i, ev["kind"], payload)},
            )  # fmt: skip
            if status != 201:
                raise UatError(f"event {i} of {cid}", status, out)
        body = {k: case[k] for k in MANIFEST_FIELDS if case.get(k) not in (None, [], {})}
        body["result"] = case["result"]
        status, signed = send(
            "POST", f"/v2/assurance/test-runs/{run_id}/cases/{cid}/manifest", body
        )
        if status != 201:
            raise UatError(f"manifest for {cid}", status, signed)
        verdicts.append(
            {
                "case_id": cid,
                "claimed": case["result"].upper(),
                "verdict": signed["verdict"],
                "problems": signed.get("problems", []),
            }
        )
    return {
        "run_id": run_id,
        "verdicts": verdicts,
        "all_as_claimed": all(v["claimed"] == v["verdict"] for v in verdicts),
    }


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description=__doc__.split("\n")[0])
    ap.add_argument("results")
    ap.add_argument("--base-url", required=True)
    ap.add_argument("--run-id", help="append to a run that already exists instead of creating one")
    args = ap.parse_args(argv)
    token = os.environ.get("ZID_UAT_TOKEN", "")
    if not token:
        print(
            "set ZID_UAT_TOKEN to the tester's bearer token (it is not accepted on the command line)",
            file=sys.stderr,
        )
        return 2
    with open(args.results, encoding="utf-8") as f:
        results = json.load(f)
    try:
        out = record(http_send(args.base_url, token), results, run_id=args.run_id)
    except UatError as e:
        print(f"refused: {e}", file=sys.stderr)
        return 1
    print(json.dumps(out, indent=2))
    return 0 if out["all_as_claimed"] else 1


if __name__ == "__main__":
    sys.exit(main())
