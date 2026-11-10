"""Feed a company's real payout decisions to Zoiko iD through the decision API, safely and exactly once (operations tool).

Each row of the file is one decision the company's own system made (who, what, how much, and whether it went ahead). The tool sends it to
`POST /access/v1/evaluation` as the registered application, with what the old system decided as `incumbent_decision`, so shadow can compare Zoiko iD's
answer with it. Nothing here changes any payout: in shadow Zoiko iD only observes.

    python tools/scripts/import_decisions.py --file payouts.csv --client-id <machine client> --key payroll-private-key.json            # preview
    python tools/scripts/import_decisions.py --file payouts.csv --client-id <machine client> --key payroll-private-key.json --apply    # send

The file (CSV with a header row), one row per decision:

    record_id      required  the source system's own unique id for this decision (what makes a re-run safe)
    person         required  the person as the company's own system knows them (Auth0: the user id `auth0|...`), or `principal:<uuid>`
    action         required  an action of the action catalogue, e.g. payment.release or invoice.approve
    resource_type  required  what it applies to, e.g. payment
    resource_id    required  its id in the source system, e.g. the batch or invoice number
    amount         required  a plain decimal, e.g. 4000.00 (no thousands separators)
    currency       required  ISO 4217, e.g. GBP
    purpose        required  why, in the application's registered words, e.g. salary_payout
    outcome        required  what the old system did: APPROVED or REJECTED
    transaction_id optional  the business transaction it belongs to
    risk_class     optional  R0..R3 (an action's own floor always applies)
    occurred_at    optional  kept for the record; the decision is judged against the authority in force when it is sent, not on that date

Safety: the whole file is checked first and nothing is sent if any row is wrong; a ledger (`<file>.ledger.jsonl` unless --ledger) records each row as soon
as it has been accepted, so a crash, a re-run or a corrected file can never count a decision twice, and a row that changed after it was sent is reported
and skipped, never re-sent. Tokens, keys and full person ids are never printed.
"""

from __future__ import annotations

import argparse
import csv
import hashlib
import json
import os
import re
import site
import sys
import time
from collections import Counter
from dataclasses import dataclass
from datetime import UTC, datetime
from decimal import Decimal, InvalidOperation
from pathlib import Path
from typing import Any

ROOT = Path(__file__).resolve().parents[2]
for extra in (ROOT / "backend" / ".venv" / "Lib" / "site-packages", ROOT / "backend" / ".venv" / "lib"):
    if extra.exists():
        site.addsitedir(str(extra))
sys.path.insert(0, str(Path(__file__).parent))

import httpx  # noqa: E402
from zid_machine_client import MachineClient, MachineClientError  # noqa: E402

REQUIRED = ("record_id", "person", "action", "resource_type", "resource_id", "amount", "currency", "purpose", "outcome")
OPTIONAL = ("transaction_id", "risk_class", "occurred_at")
OUTCOMES = {"APPROVED": "ALLOW", "REJECTED": "DENY"}
CURRENCY = re.compile(r"^[A-Z]{3}$")
AMOUNT = re.compile(r"^\d{1,15}(\.\d{1,4})?$")
RISK = re.compile(r"^R[0-3]$")
MAX_TRIES = 5


@dataclass(frozen=True)
class Row:
    line: int
    data: dict[str, str]

    @property
    def record_id(self) -> str:
        return self.data["record_id"]

    def digest(self) -> str:
        return hashlib.sha256(json.dumps({k: self.data.get(k, "") for k in (*REQUIRED, "transaction_id", "risk_class")}, sort_keys=True).encode()).hexdigest()

    def request(self, with_incumbent: bool) -> dict[str, Any]:
        d = self.data
        person = d["person"]
        subject = {"type": "principal", "id": person.removeprefix("principal:")} if person.startswith("principal:") else {"type": "directory_user", "id": person}
        context: dict[str, Any] = {"purpose": d["purpose"], "value": {"amount": d["amount"], "currency": d["currency"]}}
        if d.get("transaction_id"):
            context["transaction_context_id"] = d["transaction_id"]
        if d.get("risk_class"):
            context["risk_class"] = d["risk_class"]
        if with_incumbent:
            context["incumbent_decision"] = OUTCOMES[d["outcome"].upper()]
        return {"subject": subject, "action": {"name": d["action"]}, "resource": {"type": d["resource_type"], "id": d["resource_id"]}, "context": context}


def mask(person: str) -> str:
    return person if len(person) <= 8 else f"{person[:6]}...{person[-3:]}"


def read_rows(path: Path) -> tuple[list[Row], list[str]]:
    """Every row of the file and every problem with it; the caller refuses to send anything while there is a problem."""
    problems: list[str] = []
    rows: list[Row] = []
    with path.open(newline="", encoding="utf-8-sig") as f:
        reader = csv.DictReader(f)
        missing = [c for c in REQUIRED if c not in (reader.fieldnames or [])]
        if missing:
            return [], [f"the header is missing: {', '.join(missing)}"]
        seen: dict[str, int] = {}
        for n, raw in enumerate(reader, start=2):
            d = {k: (v or "").strip() for k, v in raw.items() if k is not None}
            bad = [f"{c} is empty" for c in REQUIRED if not d.get(c)]
            if not bad:
                if not AMOUNT.match(d["amount"]):
                    bad.append("amount must be a plain decimal like 4000.00")
                if not CURRENCY.match(d["currency"]):
                    bad.append("currency must be a three-letter code like GBP")
                if d["outcome"].upper() not in OUTCOMES:
                    bad.append("outcome must be APPROVED or REJECTED")
                if d.get("risk_class") and not RISK.match(d["risk_class"]):
                    bad.append("risk_class must be R0, R1, R2 or R3")
                if len(d["record_id"]) > 128 or len(d["resource_id"]) > 256 or len(d["person"]) > 256:
                    bad.append("an id is too long")
                if d["record_id"] in seen:
                    bad.append(f"record_id repeats line {seen[d['record_id']]}")
                else:
                    seen[d["record_id"]] = n
                try:
                    Decimal(d["amount"])
                except InvalidOperation:
                    bad.append("amount is not a number")
            problems += [f"line {n}: {b}" for b in bad]
            rows.append(Row(n, d))
    return rows, problems


def load_ledger(path: Path) -> dict[str, dict[str, Any]]:
    done: dict[str, dict[str, Any]] = {}
    if path.exists():
        for line in path.read_text(encoding="utf-8").splitlines():
            if line.strip():
                entry = json.loads(line)
                done[entry["record_id"]] = entry
    return done


def record(path: Path, entry: dict[str, Any]) -> None:
    """Append and flush to disk before moving on: once a decision is accepted it is never sent again, even after a crash."""
    with path.open("a", encoding="utf-8") as f:
        f.write(json.dumps(entry, separators=(",", ":")) + "\n")
        f.flush()
        os.fsync(f.fileno())


def send(http: httpx.Client, client: MachineClient, base: str, payload: dict[str, Any]) -> dict[str, Any]:
    """One decision, with a new token on 401 and a pause on 429 or a server error; any other refusal is final for that row."""
    for attempt in range(1, MAX_TRIES + 1):
        res = http.post(f"{base}/access/v1/evaluation", json=payload, headers=client.headers())
        if res.status_code == 200:
            return dict(res.json())
        if res.status_code == 401 and attempt == 1:
            client.forget()
            continue
        if res.status_code in (429, 500, 502, 503, 504) and attempt < MAX_TRIES:
            time.sleep(min(2 ** attempt, 30))
            continue
        raise RuntimeError(f"HTTP {res.status_code}")
    raise RuntimeError("gave up")


def main() -> int:
    p = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    p.add_argument("--file", required=True, type=Path)
    p.add_argument("--client-id", required=True, help="the application's machine client (access decisions)")
    p.add_argument("--key", required=True, type=Path, help="the client's private key file")
    p.add_argument("--api-base", default="http://localhost:8000")
    p.add_argument("--ledger", type=Path, default=None, help="default: <file>.ledger.jsonl")
    p.add_argument("--apply", action="store_true", help="send the decisions (the default only shows what would be sent)")
    p.add_argument("--no-incumbent", action="store_true", help="do not say what the old system did (checks the answers without entering the mismatch statistics)")
    p.add_argument("--limit", type=int, default=0, help="send at most this many new rows")
    args = p.parse_args()

    if not args.file.is_file():
        print(f"import_decisions: file not found: {args.file}", file=sys.stderr)
        return 2
    rows, problems = read_rows(args.file)
    if problems:
        print(f"{len(problems)} problem(s) in {args.file.name}; nothing was sent:", file=sys.stderr)
        for line in problems[:40]:
            print(f"  {line}", file=sys.stderr)
        return 2
    ledger_path = args.ledger or args.file.with_suffix(args.file.suffix + ".ledger.jsonl")
    ledger = load_ledger(ledger_path)

    todo: list[Row] = []
    skipped = changed = 0
    for r in rows:
        prior = ledger.get(r.record_id)
        if prior is None:
            todo.append(r)
        elif prior["digest"] == r.digest():
            skipped += 1
        else:
            changed += 1
            print(f"  line {r.line}: record {r.record_id} was sent before and has changed since; it is not sent again", file=sys.stderr)
    if args.limit:
        todo = todo[: args.limit]
    print(f"{len(rows)} row(s): {len(todo)} to send, {skipped} already sent, {changed} changed since sent")
    if not args.apply:
        for r in todo[:5]:
            print(f"  would send {r.record_id}: {mask(r.data['person'])} {r.data['action']} {r.data['amount']} {r.data['currency']} (the old system: {r.data['outcome'].upper()})")
        print("nothing was sent (preview). Run again with --apply to send these.")
        return 0

    try:
        client = MachineClient(args.api_base, args.client_id, args.key, "access:evaluate")
        client.renew()
    except MachineClientError as e:
        print(f"import_decisions: could not sign in as the application: {e}", file=sys.stderr)
        return 2
    base = args.api_base.rstrip("/")
    http = httpx.Client(timeout=30)
    sent = failed = 0
    agree: Counter[str] = Counter()
    reasons: Counter[str] = Counter()
    for r in todo:
        try:
            out = send(http, client, base, r.request(not args.no_incumbent))
        except (RuntimeError, httpx.HTTPError) as e:
            failed += 1
            print(f"  line {r.line}: record {r.record_id} was not accepted ({e}); it can be sent again on the next run", file=sys.stderr)
            continue
        allow = bool(out["decision"])
        old = OUTCOMES[r.data["outcome"].upper()]
        agree["match" if (allow and old == "ALLOW") or (not allow and old == "DENY") else ("false_allow" if allow else "false_deny")] += 1
        reasons.update(out.get("context", {}).get("reason_codes", []))
        record(ledger_path, {"record_id": r.record_id, "digest": r.digest(), "decision_id": out.get("context", {}).get("decision_id"),
                             "decision": "ALLOW" if allow else "DENY", "at": datetime.now(UTC).isoformat()})  # fmt: skip
        sent += 1
    print(f"sent {sent}, not accepted {failed}")
    if not args.no_incumbent and sent:
        print(f"compared with the old system: {agree['match']} match, {agree['false_deny']} false deny, {agree['false_allow']} false allow")
    if reasons:
        print("reasons: " + ", ".join(f"{k} x{v}" for k, v in reasons.most_common(6)))
    return 3 if failed else 0


if __name__ == "__main__":
    raise SystemExit(main())
