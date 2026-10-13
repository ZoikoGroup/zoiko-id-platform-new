"""Run OpenID Foundation conformance plans against a Zoiko iD cell (tests/conformance/plans.yaml).

    python tests/conformance/run_conformance.py --list
    python tests/conformance/run_conformance.py oidc-op-basic fapi2-dpop

Uses the suite's REST API (the same calls as the suite's own run-test-plan.py): create the plan
with its variant and configuration, run every module, wait for each to finish, and fail unless
every module PASSED (or ended REVIEW/SKIPPED/WARNING, which the suite UI then shows for sign-off;
results are exported from the suite UI and attached to the release). Interactive modules (a browser
login) are driven by the suite's own browser automation configured in the plan's `browser` key,
or completed by hand from the suite UI while this script waits.

Environment: CONFORMANCE_SERVER, CONFORMANCE_TOKEN, ZID_CELL, ZID_ISSUER, ZID_API and the
client variables each plan names. Never point it at production.
"""

from __future__ import annotations

import argparse
import json
import os
import re
import sys
import time
from pathlib import Path
from typing import Any

import httpx
import yaml

HERE = Path(__file__).resolve().parent
PLANS = HERE / "plans.yaml"
FINAL = {"FINISHED", "INTERRUPTED"}
OK_RESULTS = {"PASSED", "REVIEW", "SKIPPED", "WARNING"}
PLACEHOLDER = re.compile(r"\$\{([A-Z0-9_]+)\}")


def load() -> dict[str, Any]:
    return yaml.safe_load(PLANS.read_text(encoding="utf-8"))  # type: ignore[no-any-return]


def placeholders(value: Any) -> set[str]:
    if isinstance(value, str):
        return set(PLACEHOLDER.findall(value))
    if isinstance(value, dict):
        return set().union(*(placeholders(v) for v in value.values())) if value else set()
    if isinstance(value, list):
        return set().union(*(placeholders(v) for v in value)) if value else set()
    return set()


def fill(value: Any, env: dict[str, str]) -> Any:
    if isinstance(value, str):
        out = PLACEHOLDER.sub(lambda m: env[m.group(1)], value)
        if out.startswith(("{", "[")) and out != value:  # a JWKS passed as JSON in the environment
            try:
                return json.loads(out)
            except ValueError:
                return out
        return out
    if isinstance(value, dict):
        return {k: fill(v, env) for k, v in value.items()}
    if isinstance(value, list):
        return [fill(v, env) for v in value]
    return value


def run_plan(client: httpx.Client, name: str, spec: dict[str, Any], env: dict[str, str]) -> bool:
    variant = json.dumps(spec.get("variant") or {})
    r = client.post(
        "/api/plan",
        params={"planName": spec["plan"], "variant": variant},
        json=fill(spec["config"], env),
    )
    if r.status_code >= 400:
        print(f"[{name}] suite refused plan {spec['plan']}: {r.status_code} {r.text[:200]}")
        return False
    plan = r.json()
    print(f"[{name}] plan {plan['id']} with {len(plan['modules'])} modules")
    ok = True
    for module in plan["modules"]:
        test = module["testModule"]
        params = {"test": test, "plan": plan["id"]}
        if module.get("variant"):
            params["variant"] = json.dumps(module["variant"])
        started = client.post("/api/runner", params=params)
        started.raise_for_status()
        test_id = started.json()["id"]
        deadline = time.monotonic() + 900
        info: dict[str, Any] = {}
        while time.monotonic() < deadline:
            info = client.get(f"/api/info/{test_id}").json()
            if info.get("status") in FINAL:
                break
            if info.get("status") == "WAITING":
                print(f"  {test}: waiting for interaction - {spec['config'].get('alias')}")
            time.sleep(3)
        result = info.get("result") or "TIMEOUT"
        passed = info.get("status") == "FINISHED" and result in OK_RESULTS
        ok &= passed
        print(f"  {'ok ' if passed else 'BAD'} {test}: {info.get('status')} {result}")
    return ok


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("plans", nargs="*")
    parser.add_argument("--list", action="store_true")
    args = parser.parse_args(argv)
    doc = load()
    if args.list or not args.plans:
        for name, spec in doc["plans"].items():
            print(f"{name:28} {spec['plan']:40} {spec['requirement']}")
        return 0
    unknown = [p for p in args.plans if p not in doc["plans"]]
    if unknown:
        print(f"unknown plans: {unknown}")
        return 2
    needed = placeholders(doc["suite"]) | set().union(
        *(placeholders(doc["plans"][p]) for p in args.plans)
    )
    missing = sorted(v for v in needed if not os.environ.get(v))
    if missing:
        print("missing environment: " + ", ".join(missing))
        return 2
    env = {k: os.environ[k] for k in needed}
    if "prod" in env.get("ZID_CELL", "").lower():
        print("refusing to run conformance tests against a production cell")
        return 2
    suite = fill(doc["suite"], env)
    with httpx.Client(
        base_url=suite["url"],
        headers={"Authorization": f"Bearer {suite['token']}"},
        timeout=30,
    ) as client:
        results = {p: run_plan(client, p, doc["plans"][p], env) for p in args.plans}
    for name, ok in results.items():
        print(f"{'PASS' if ok else 'FAIL'} {name}")
    return 0 if all(results.values()) else 1


if __name__ == "__main__":
    sys.exit(main())
