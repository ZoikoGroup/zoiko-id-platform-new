"""Service catalogue, runbooks, alert ownership, PRR and incident rules (G-W8; ZID-SRE-001 §1,
§5-6, §22, §24, §26; OPS-01/04/26/28; SRE-04/26/32)."""

from __future__ import annotations

import copy
import re
from datetime import date
from pathlib import Path

import pytest
import yaml
from zid_platform.modules.operations.domain.catalogue import CatalogueError, parse
from zid_platform.modules.operations.domain.incidents import (
    PRR_GATES,
    ActionItem,
    GateResult,
    GateStatus,
    IncidentStatus,
    OperationsError,
    OpState,
    Severity,
    check_close,
    check_gates,
    check_move,
    check_resolve,
    operational_state,
    readiness_verdict,
)
from zid_platform.ops_export import RULES_FILE, rendered

REPO = Path(__file__).resolve().parents[4]
DOC = yaml.safe_load(
    (REPO / "backend/config/operations/service-catalogue.yaml").read_text(encoding="utf-8")
)
RUNBOOKS = REPO / "docs" / "runbooks"
FIELDS = (
    "Trigger",
    "Impact",
    "Preconditions",
    "Diagnosis",
    "Mitigation",
    "Recovery",
    "Validation",
    "Rollback",
    "Escalation",
    "Evidence",
    "Last exercised",
    "Owner/version",
)


def test_catalogue_parses_and_every_service_has_an_owner_and_runbook() -> None:  # OPS-01
    cat = parse(DOC)
    for svc in cat.services.values():
        assert svc.owner.startswith("@zoiko/") and svc.oncall and svc.runbooks and svc.dashboard
    assert {s.tier for s in cat.services.values()} <= {"T0", "T1", "T2", "T3"}


def test_every_referenced_runbook_exists_with_every_field() -> None:  # SRE-26, §22
    cat = parse(DOC)
    for rid in sorted(cat.runbooks() | {f"RB-{n:02d}" for n in range(1, 19)}):
        path = RUNBOOKS / f"{rid}.md"
        assert path.exists(), f"{rid} is referenced but missing"
        text = path.read_text(encoding="utf-8")
        front = yaml.safe_load(text.split("---")[1])
        assert (
            front["id"] == rid
            and front["owner"].startswith("@zoiko/")
            and "last_exercised" in front
        )
        missing = [f for f in FIELDS if not re.search(rf"^## {re.escape(f)}$", text, re.MULTILINE)]
        assert not missing, f"{rid} lacks {missing}"


def test_owners_of_runbooks_and_catalogue_are_codeowners_teams() -> None:
    codeowners = (REPO / ".github" / "CODEOWNERS").read_text(encoding="utf-8")
    teams = set(re.findall(r"@zoiko/[a-z0-9-]+", codeowners))
    cat = parse(DOC)
    owners = {s.owner for s in cat.services.values()} | {i.owner for i in cat.invariants}
    assert owners <= teams, owners - teams


def test_every_alert_is_actionable() -> None:  # OPS-04
    rules = yaml.safe_load(rendered())
    alerts = [r for g in rules["groups"] for r in g["rules"] if "alert" in r]
    assert alerts
    for a in alerts:
        ann = a["annotations"]
        assert ann["owner"] and ann["dashboard"] and ann["summary"], a["alert"]
        rb = ann["runbook_url"].rsplit("/", 1)[-1]
        assert (RUNBOOKS / rb).exists(), a["alert"]
        assert a["labels"]["class"] in ("PAGE", "TICKET")


def test_ownerless_or_runbookless_alerts_are_rejected() -> None:  # SRE-04
    doc = copy.deepcopy(DOC)
    doc["invariants"][0]["owner"] = ""
    with pytest.raises(CatalogueError):
        parse(doc)
    doc = copy.deepcopy(DOC)
    doc["services"]["authorization"]["runbooks"] = ["see wiki"]
    with pytest.raises(CatalogueError):
        parse(doc)
    doc = copy.deepcopy(DOC)
    doc["services"]["authorization"]["slos"] = doc["services"]["authorization"]["slos"][:1]
    with pytest.raises(CatalogueError):  # a T0 needs availability and latency
        parse(doc)
    doc = copy.deepcopy(DOC)
    doc["services"]["evidence"]["journeys"].append({"prefix": "/access/v1/", "latency_s": 1})
    with pytest.raises(CatalogueError):  # one prefix, one owner
        parse(doc)


def test_prometheus_rules_match_the_catalogue() -> None:
    assert RULES_FILE.read_text(encoding="utf-8") == rendered(), (
        "regenerate: python -m zid_platform.ops_export"
    )


# ------------------------------------------------------------------------------------- PRR


def passing() -> list[GateResult]:
    return [GateResult(g, GateStatus.PASS, f"EV-{g}", None, None, None) for g in PRR_GATES]


def test_prr_ready_only_when_every_gate_passes_or_is_accepted() -> None:  # SRE-32, OPS-26
    today = date(2026, 10, 22)
    assert readiness_verdict(check_gates(passing()), today) == (True, [])
    gates = passing()
    gates[6] = GateResult("ha_dr", GateStatus.FAIL, None, None, None, None)
    ready, blocking = readiness_verdict(check_gates(gates), today)
    assert not ready and blocking == [{"gate": "ha_dr", "reason": "FAILED"}]
    ready, blocking = readiness_verdict(check_gates(passing()[:-1]), today)
    assert not ready and blocking == [{"gate": "open_risks", "reason": "NOT_REVIEWED"}]
    gates = passing()
    gates[5] = GateResult(
        "capacity", GateStatus.ACCEPTED, None, "o", date(2026, 11, 1), "manual scale runbook"
    )
    assert readiness_verdict(check_gates(gates), today)[0]
    assert readiness_verdict(gates, date(2026, 11, 2)) == (
        False,
        [{"gate": "capacity", "reason": "ACCEPTANCE_EXPIRED"}],
    )


@pytest.mark.parametrize(
    ("gate", "code"),
    [
        (GateResult("slos", GateStatus.PASS, None, None, None, None), "PRR_EVIDENCE_REQUIRED"),
        (
            GateResult("slos", GateStatus.ACCEPTED, None, "o", None, "x"),
            "PRR_ACCEPTANCE_INCOMPLETE",
        ),
        (GateResult("vibes", GateStatus.PASS, "EV", None, None, None), "PRR_GATE_UNKNOWN"),
    ],
)
def test_prr_gate_records_are_complete(gate: GateResult, code: str) -> None:
    with pytest.raises(OperationsError) as e:
        check_gates([gate])
    assert e.value.code == code


# ------------------------------------------------------------------------------- incidents


def test_incident_lifecycle_rules() -> None:  # OPS-18/28, SRE-25
    check_move(IncidentStatus.DECLARED, IncidentStatus.MITIGATING)
    with pytest.raises(OperationsError):
        check_move(IncidentStatus.DECLARED, IncidentStatus.CLOSED)
    with pytest.raises(OperationsError) as e:
        check_resolve(
            IncidentStatus.MITIGATING,
            Severity.SEV1,
            True,
            {"INCIDENT_COMMANDER": "a"},
            "validated ok now",
        )
    assert e.value.code == "INCIDENT_SECURITY_LEAD_REQUIRED"
    check_resolve(
        IncidentStatus.MITIGATING,
        Severity.SEV2,
        False,
        {"INCIDENT_COMMANDER": "a"},
        "synthetics green 30m",
    )

    with pytest.raises(OperationsError) as e:
        check_close(IncidentStatus.RESOLVED, Severity.SEV1, None, [])
    assert e.value.code == "PIR_REQUIRED"
    for item, code in (
        (ActionItem("add alert", None, date(2026, 11, 1), None), "PIR_ACTION_UNOWNED"),
        (ActionItem("add alert", "o", date(2026, 11, 1), None), "PIR_ACTION_OPEN"),
        (ActionItem("add alert", "o", date(2026, 11, 1), "o"), "PIR_VERIFICATION_NOT_INDEPENDENT"),
    ):
        with pytest.raises(OperationsError) as e:
            check_close(IncidentStatus.RESOLVED, Severity.SEV1, "PIR-1", [item])
        assert e.value.code == code
    check_close(
        IncidentStatus.RESOLVED,
        Severity.SEV1,
        "PIR-1",
        [ActionItem("add alert", "o", date(2026, 11, 1), "v")],
    )
    check_close(IncidentStatus.RESOLVED, Severity.SEV3, None, [])  # minor incidents need no PIR


def test_operational_state_is_conservative() -> None:  # §30
    base = {
        "paging": False,
        "ticketing": False,
        "availability_short": None,
        "frozen": False,
        "open_incident": None,
        "failover_in_progress": False,
    }
    assert operational_state(ready=None, **base) is OpState.UNKNOWN  # type: ignore[arg-type]
    assert operational_state(ready=True, **base) is OpState.HEALTHY  # type: ignore[arg-type]
    assert operational_state(ready=True, **{**base, "paging": True}) is OpState.PARTIAL_OUTAGE  # type: ignore[arg-type]
    assert operational_state(ready=True, **{**base, "frozen": True}) is OpState.CHANGE_FROZEN  # type: ignore[arg-type]
    sec = {**base, "open_incident": (Severity.SEV0, IncidentStatus.MITIGATING, True)}
    assert operational_state(ready=True, **sec) is OpState.SECURITY_CONTAINMENT  # type: ignore[arg-type]
