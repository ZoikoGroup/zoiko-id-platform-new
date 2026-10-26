"""Billable meters, deduplication keys and ledger arithmetic (ZID-COM-001 §7, §26; CR-025). Pure domain.

One billable event per chargeable operation. The deduplication key is built from the identity of the thing
that was charged (a session, a decision, a token, a credential, a presentation), never from a clock or a
counter, so recording the same operation twice can only ever produce one row.
"""

from __future__ import annotations

import hashlib
import json
from collections.abc import Iterable, Mapping
from dataclasses import dataclass
from datetime import UTC, date, datetime
from decimal import Decimal

METERS = ("MAH", "AUTH_EVENT", "POLICY_DECISION", "M2M_GRANT", "VC_ISSUED", "VC_PRESENTED")
PRINCIPAL_CLASSES = ("HUMAN", "NON_HUMAN")


class BillableError(Exception):
    def __init__(self, code: str, detail: str = "") -> None:
        super().__init__(detail or code)
        self.code, self.detail = code, detail


def window_month(at: datetime) -> date:
    """The UTC billing month an instant belongs to, as the first day of that month."""
    a = at.astimezone(UTC)
    return date(a.year, a.month, 1)


def next_month(m: date) -> date:
    return date(m.year + (m.month == 12), m.month % 12 + 1, 1)


def _digest(value: str) -> str:
    return hashlib.sha256(value.encode("utf-8")).hexdigest()


def key_auth(session_id: str) -> str:
    return f"auth:{session_id}"


def key_decision(decision_id: str) -> str:
    return f"pdp:{decision_id}"


def key_m2m(token_id: str) -> str:
    return f"m2m:{token_id}"


def key_vc_issued(credential_id: str) -> str:
    return f"vci:{credential_id}"


def key_vc_presented(presentation_id: str) -> str:
    return f"vcp:{presentation_id}"


def key_mah(month: date, principal_id: str) -> str:
    """The principal is hashed: the key is stored in the ledger, which keeps no personal identifiers."""
    return f"mah:{month:%Y%m}:{_digest(principal_id)[:32]}"


@dataclass(frozen=True, slots=True)
class BillableEvent:
    event_id: str
    tenant_id: str
    deduplication_key: str
    meter_id: str
    quantity: Decimal
    occurred_at: datetime
    window_month: date
    environment_id: str
    region: str
    principal_class: str | None = None
    source_request_id: str | None = None
    late: bool = False
    recovered: bool = False

    def validate(self) -> None:
        if self.meter_id not in METERS:
            raise BillableError("METER_UNKNOWN", self.meter_id)
        if self.quantity <= 0:
            raise BillableError("QUANTITY_INVALID", "a billable quantity is positive")
        if not 3 <= len(self.deduplication_key) <= 200:
            raise BillableError("DEDUPLICATION_KEY_INVALID")
        if self.principal_class is not None and self.principal_class not in PRINCIPAL_CLASSES:
            raise BillableError("PRINCIPAL_CLASS_INVALID")

    @property
    def row_hash(self) -> str:
        """The digest of the economic facts; the window hash total is built from these."""
        return row_hash(
            self.tenant_id,
            self.deduplication_key,
            self.meter_id,
            self.quantity,
            self.occurred_at,
            self.window_month,
            self.environment_id,
            self.region,
        )


def row_hash(
    tenant_id: str,
    key: str,
    meter: str,
    quantity: Decimal,
    occurred_at: datetime,
    month: date,
    environment: str,
    region: str,
) -> str:
    canonical = json.dumps(
        [
            tenant_id,
            key,
            meter,
            format(quantity.normalize(), "f"),
            occurred_at.astimezone(UTC).isoformat(),
            month.isoformat(),
            environment,
            region,
        ],
        separators=(",", ":"),
    )
    return _digest(canonical)


def hash_total(row_hashes: Iterable[str]) -> str:
    """Order-independent digest of a window: the same events always give the same total on replay."""
    return _digest("\n".join(sorted(row_hashes)))


def net_quantities(
    events: Mapping[str, Decimal], adjustments: Mapping[str, Decimal]
) -> dict[str, Decimal]:
    """Per meter: events plus approved adjustments. Never below zero for a meter."""
    out: dict[str, Decimal] = {}
    for meter in sorted({*events, *adjustments}):
        out[meter] = max(
            Decimal(0), events.get(meter, Decimal(0)) + adjustments.get(meter, Decimal(0))
        )
    return out
