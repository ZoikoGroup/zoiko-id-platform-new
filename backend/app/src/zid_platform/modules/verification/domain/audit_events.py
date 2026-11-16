"""Audit-only names (audit records and an alert kind, not events on the bus; the catalogue declares only emitted events)."""

CALLBACK_REJECTED = "verification.callback.rejected"
REVERIFICATION_DUE = "verification.reverification.due"  # an alert kind, not an event
