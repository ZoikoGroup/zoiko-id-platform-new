"""Writes engineering-acceptance.csv: the 30 acceptance tests of ZID-ENG-TRC-001 section 10 under the ENG-AT- prefix (the older AT-01..30 ids in acceptance-tests.csv are a different set).

A row is linked only when an existing test really exercises the statement; the note says how far. Everything else stays NOT_STARTED. A link is checked: the test function must exist.
"""

import csv
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
U = "backend/app/tests/unit/"
C = "backend/app/tests/component/"

AT = {
    1: "Cross-tenant access denied for case/evidence/metrics/export",
    2: "No DTV unknown counted complete",
    3: "Requirement to source to code to CI to gate trace reproducible",
    4: "Changed digest invalidates dependent test PASS",
    5: "Unrelated partner obtains sandbox credential and signed webhook",
    6: "No premature final verified webhook",
    7: "Duplicate API events cannot duplicate billing or decisions",
    8: "Provider unknown outcome reconciled before retry",
    9: "PAD lab missing means high-assurance route blocked",
    10: "Untrusted CSCA chain yields indeterminate",
    11: "Incomplete UBO/authority cannot confer signing privilege",
    12: "Formatted phone is not phone possession",
    13: "Geocoded address is not proof of residence",
    14: "Unreachable sanctions list is not clear",
    15: "Shared device/IP does not automatically prove fraud",
    16: "Parallel agent actions cannot overspend shared budget",
    17: "Revoked sponsor denies subsequent tool call",
    18: "Credential revocation blocks later use, not historical data",
    19: "Offline signature verification works across key rotation",
    20: "Disallowed regional data transfer blocks route publish",
    21: "Policy replay generates no invoices or external notifications",
    22: "Zoiko iD billing isolated from Zoiko Billing product",
    23: "Evidence export contains no biometric or secret payload",
    24: "Unsupported certification or case study cannot publish",
    25: "Status page and internal reliability use same telemetry",
    26: "Implementer cannot self-approve independent QA",
    27: "US pilot does not implicitly activate UK or other market",
    28: "Accessible keyboard, screen-reader and zoom review",
    29: "Disaster recovery preserves lawful tombstones and receipts",
    30: "Provider removal invalidates downstream routes and claims",
}

LINK = {
    2: (U + "test_trace_workbench.py::test_nineteen_dtv_ids_stay_missing_source_and_none_is_countable_as_done", "the 19 undefined DTV ids are held as MISSING_SOURCE in the workbench"),
    3: (U + "test_trace_workbench.py::test_the_sheet_says_not_assessed_where_there_is_no_source_and_never_claims_more_than_a_passed_run", "the sheet is generated from the CSVs; the CI-run and build-digest columns are NOT ASSESSED until pipelines supply them"),
    4: (U + "test_trace_workbench.py::test_a_pass_on_a_file_that_changed_is_expired_and_cannot_close_a_gate", "file-digest level; a build digest and a provider or model change are not yet inputs"),
    14: (C + "test_attestation_pg.py::test_a_stale_snapshot_is_hold_so_an_unreachable_register_cannot_keep_a_certification_alive", "certification registry; the sanctions-list case has its own stale-list test"),
    19: (C + "test_evidence_pg.py::test_worker_seals_and_offline_verifier_accepts_the_export", "offline verification exists; verification across a key rotation is not shown by this test"),
    24: (C + "test_trust_center_register_pg.py::test_a_claim_is_drafted_approved_by_two_roles_published_superseded_withdrawn_and_never_edited", "claims need evidence, two reviewers and a gate; a case study is the endorsement rule in the domain tests"),
    26: (C + "test_trust_center_register_pg.py::test_the_database_refuses_what_the_code_should_never_attempt", "enforced for claim approval and publication; the workbench gate also refuses an implementer as signer"),
    27: (U + "test_trace_workbench.py::test_one_markets_approval_does_not_activate_another", "release tuple assessment; no production release process consumes it yet"),
    28: (U + "test_wf12_trust_center_domain.py::test_ordinary_trust_language_passes_and_an_allowed_contact_is_not_a_finding", ""),
}
LINK.pop(28)


def main() -> None:
    rows = []
    for n, name in AT.items():
        link, note = LINK.get(n, ("", "no existing test exercises this statement"))
        if link:
            path, fn = link.split("::")
            assert f"def {fn}(" in (ROOT / path).read_text(encoding="utf-8"), link
        rows.append([f"ENG-AT-{n:02d}", "ZID-ENG-TRC-001", name, "IN_PROGRESS" if link else "NOT_STARTED", link, note])
    with (ROOT / "docs" / "traceability" / "engineering-acceptance.csv").open("w", encoding="utf-8", newline="") as f:
        w = csv.writer(f, lineterminator="\n")
        w.writerow(["test_id", "source_spec", "name", "test_status", "evidence_link", "note"])
        w.writerows(rows)
    print(sum(1 for r in rows if r[3] == "IN_PROGRESS"), "linked of", len(rows))


main()
