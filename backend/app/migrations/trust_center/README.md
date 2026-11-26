# Migrations: `trust_center` module

Alembic stream owned by the **trust_center** module (ZID-CP-WF12 Public Trust Center, certification, evidence and claims governance). Version table: `zid_migrations.alembic_version__trust_center`.

May create or alter **only** these tables (enforced by CI against `backend/database/table-ownership.yaml`):

- `platform.tc_evidence`: an artifact a claim can rest on, with the exact scope it was assessed for; facts never change, only its lifecycle and the issuer's confirmation
- `platform.tc_claim_revision`: one version of one public sentence; content never changes, only the status moves and never out of EMERGENCY_WITHDRAWN
- `platform.tc_approval`: append-only reviewer decisions bound to an exact content hash; the author cannot review, one person and one role count once
- `platform.tc_publication`: append-only publications and rollbacks; needs two distinct approvers other than the author
- `platform.tc_withdrawal`: emergency withdrawals and the one retrospective review each demands

- `platform.tc_sli_snapshot`: measured availability per service, region and window, recorded and validated by two different people
- `platform.tc_advisory`, `platform.tc_advisory_version`: public incident advisories; versions are append-only, a correction is a new version
- `platform.tc_report`, `platform.tc_report_event`: vulnerability reports and their append-only timeline

- `platform.tc_disclosure`: numbered privacy, subprocessor, retention, biometric and transfer disclosures; content never changes, a second person approves
- `platform.tc_evidence_blob`: scanned bytes of restricted evidence, never served directly
- `platform.tc_room_request`, `platform.tc_room_grant`, `platform.tc_room_access`: the restricted evidence room; a request, an expiring requester-bound grant, an append-only access history

Company records, not tenant content: no tenant scope and no customer data.
