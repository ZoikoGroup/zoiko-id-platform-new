"""Audit-only and alert-kind names (not events on the bus; the catalogue declares only emitted events)."""

DISCLOSURE_DENIED = "credential.disclosure.denied"

# alert kinds (the alert store's own names, not events on the bus)
PROOF_REVIEW_ALERT = "credential.proof.review"
FAIRNESS_HOLD_ALERT = "credential.fairness.hold"
