"""Audit-only event types of the approval service (slice L-10, CR-047). These are evidence records, not outbox events, so they live
apart from the module that emits events: the event-catalogue guard reads every dotted name in a file that touches the outbox."""

REFUSED = "governance.approval.refused"
VOTE_DENIED = "governance.approval.vote_denied"
EXECUTION_REFUSED = "governance.approval.execution_refused"
READINESS_REPORT = "governance.approval.readiness_report"
CONTAINMENT_REFUSED = "governance.containment.refused"
AUTHORIZATION_CONSUMED = "governance.authorization.consumed"
AUTHORIZATION_REFUSED = "governance.authorization.refused"
