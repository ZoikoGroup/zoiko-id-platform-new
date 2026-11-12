# CR-072: the /v2 privacy control plane (ZID-PRI-004)

Status: built and tested locally, uncommitted. Needs reviewer approval like every change request.

## What changes
- **Migration** `privacy_0003` (single cell database): `privacy.purpose_decision`, `notice_acknowledgment`, `transfer_decision` (append-only by trigger),
  `erasure_job`, `erasure_task`, `subprocessor_change`, `dpia_package`, `decision_review`, `breach_case`, and `privacy.processor.receipt_public_key`.
  All tenant-scoped with row-level security.
- **Domain** `privacy/domain/control.py` (pure): risk scoring and bands, the G0 to G7 gates, the strict no-release criteria, default-deny transfer rules,
  the erasure job state machine and proof, breach clocks, review rules.
- **Application** `privacy/application/control.py` over the v1 registry, consent, retention and erasure machinery (it reuses `check_processing`, the data map,
  the disposers and `erase_subject`; nothing is duplicated).
- **Routes** `privacy/api/v2.py`: purpose decisions, notice acknowledgments and alternatives, erasure jobs with proof, provider deletion receipts, transfer
  decisions, provider controls, subprocessor changes, DPIA packages and release gates, appeals and human review, breach cases, KPIs.
- **Config** `backend/config/privacy/`: `alternatives.yaml` (non-biometric routes) and `breach-clocks.yaml` (every deadline is COUNSEL).
- **Contracts**: `contracts/openapi/public/privacy.json` regenerated.

## What does not change
No existing route, event, action or table is altered (only one column is added to `privacy.processor`). The routes reuse `privacy.read`,
`privacy.rights.manage`, `privacy.rights.fulfill` and `privacy.processing_activity.manage`; no action or event is added.

## Deliberate choices to review
- A deletion receipt counts only if its Ed25519 signature verifies against the key registered for that provider. Whoever registers the key is a
  `privacy.processing_activity.manage` holder; this is a trust decision for the privacy officer.
- A breach deadline is never invented: a jurisdiction counsel has not set reports NOT_SET_BY_COUNSEL. The file ships with every value COUNSEL.
- A DPIA cannot be approved while any no-release criterion stands, and the author can neither sign a gate nor decide the package. The numeric scores are
  the controller's input, not ours.
- The erasure job disposes first-party data at creation (as the v1 fulfilment does); providers and backups are open tasks until acknowledged.

## Not done
Real provider callbacks (no provider sends a receipt yet), a DPO console screen for these routes, regulator-submission integration, and the signed approvals
themselves.
