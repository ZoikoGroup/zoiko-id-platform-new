# CR-017: Service catalogue, SLOs, incidents, readiness reviews and change freezes

- **Target:** ZID-SRE-001 §1, §4–6, §15–17, §22–24, §26, §29–30; DEC-G5; SEC-001 §11–13 (capability); event catalogue · **Raised by:** Phase G W8 · **Status:** Open

## Configuration

`backend/config/operations/service-catalogue.yaml`: every production service with owner (a CODEOWNERS team), on-call rotation, tier, journeys (path prefixes measured as SLIs), SLOs, runbooks and dashboard, plus the zero-budget safety invariants. It generates `deploy/observability/prometheus/rules/zid-slo.yaml` (multi-window burn-rate alerts and invariant alerts). CI fails on drift, on an ownerless or runbook-less alert, and on a T0/T1 service without both availability and latency SLOs.

## Tables

| Table | Owner | Note |
|---|---|---|
| `platform.incident` | operations | Severity SEV0–SEV4, status, security flag, services, cells, roles. A SEV0 is always a security incident. |
| `platform.incident_entry` | operations | Append-only timestamped timeline: declaration, severity, status, roles, actions, decisions, communications, resolution, closure. |
| `platform.incident_action_item` | operations | Post-incident review items: owner, due date, verification by someone other than the owner. |
| `platform.readiness_review` | operations | A production readiness review of a service release with its verdict. |
| `platform.readiness_gate` | operations | The 13 PRR gates (§24) per review: PASS with evidence, FAIL, or ACCEPTED with risk owner, expiry and compensating control. |
| `platform.change_freeze` | operations | An explicit change freeze per service (one active at a time). |

## Operator capability

`platform.change.manage` (R2): lifting a change freeze. Declaring incidents and applying a freeze need only an active operator (§16.1: declaration is never delayed by an approval).

## Events (proposed)

`ops.incident.declared`, `ops.incident.severity_changed`, `ops.incident.updated`, `ops.incident.resolved`, `ops.incident.closed`, `ops.readiness_review.recorded`, `ops.change_freeze.applied`, `ops.change_freeze.lifted`.

## Metrics emitted by the code

`zid_http_requests_total`, `zid_http_request_duration_seconds`, `zid_sli_events_total{journey,sli,outcome}`, `zid_revocation_propagation_seconds`, `zid_outbox_oldest_unpublished_seconds{lane}`, `zid_tenant_isolation_violations_total`, `zid_evidence_receipts_total{evidence_class}`, `zid_evidence_checkpoint_verify_failures_total`, `zid_mandate_ledger_corruption_total`, `zid_writer_epoch_fenced_total`, `zid_telemetry_cardinality_overflow_total`. Identifier labels (tenant, principal, request, session…) are refused at definition time.

## Engineering readings to confirm

- Burn-rate windows: 1h/5m at 14.4× and 6h/30m at 6× page; 3d/6h at 1× tickets; a short window needs ≥ 10 events.
- §5.1 release policy bands (> 50 %, 25–50 %, 10–25 %, < 10 %, exhausted); fixes and security changes are never blocked by a budget.
- SEV0/SEV1 close only with a PIR reference and every action item owned, dated and verified by someone other than its owner.
- The mandate-overspend invariant counts `LEDGER_CORRUPTION` (a balance would go negative), not ordinary limit refusals.
