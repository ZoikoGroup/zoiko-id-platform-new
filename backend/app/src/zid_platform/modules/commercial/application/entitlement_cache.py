"""Signed entitlement snapshots with a bounded lifetime (ZID-ENG-DEC-008 section 05, "Cache rule").

A snapshot is the tenant's strict decision for every feature, signed with the evidence runtime's internal MAC key so a copy that was
edited in memory is refused. It is trusted only while all three hold:

1. it has not expired: the policy TTL, or sooner if a live enterprise override ends first;
2. its signature verifies under the current key;
3. its fingerprint still equals the tenant's commercial state now: plan, suspension, billing restriction and every override change,
   including a revocation. One cheap read answers this on every decision, so a suspension, a revoked override or a plan change takes
   effect on the next call and never waits for the TTL.

Roles, sessions and tokens are not in a snapshot and are never cached here: they are decided fresh each time (the role matrix).
Region and provider coverage are facts of the request, layered on top of the snapshot for each decision.
"""

from __future__ import annotations

import json
from collections.abc import Callable
from dataclasses import dataclass, replace
from datetime import datetime, timedelta
from hmac import compare_digest

from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncConnection

FeatureRow = tuple[str, str, tuple[str, ...], str | None, str | None, bool, str | None]
# (feature id, outcome, reasons, state, band, divergent, upgrade band)


@dataclass(frozen=True, slots=True)
class Snapshot:
    tenant_id: str
    plan_code: str
    band: str | None
    subscription_state: str
    fingerprint: str
    catalog_version: str
    issued_at: datetime
    expires_at: datetime
    features: tuple[FeatureRow, ...]
    signed: str

    def doc(self) -> dict[str, object]:
        return {
            "tenant": self.tenant_id,
            "plan": self.plan_code,
            "band": self.band,
            "state": self.subscription_state,
            "fingerprint": self.fingerprint,
            "catalog": self.catalog_version,
            "issued": self.issued_at.isoformat(),
            "expires": self.expires_at.isoformat(),
            "features": [
                {
                    "id": fid,
                    "outcome": outcome,
                    "reasons": list(reasons),
                    "state": state,
                    "band": band,
                    "divergent": divergent,
                    "upgrade": upgrade,
                }
                for fid, outcome, reasons, state, band, divergent, upgrade in self.features
            ],
        }

    def body(self) -> bytes:
        return json.dumps(self.doc(), sort_keys=True, separators=(",", ":")).encode()


def from_doc(doc: dict[str, object], signed: str) -> Snapshot:
    """The snapshot a stored document describes. Its signature is checked by the caller against these exact bytes."""
    rows = doc["features"]
    assert isinstance(rows, list)
    return Snapshot(
        tenant_id=str(doc["tenant"]),
        plan_code=str(doc["plan"]),
        band=None if doc["band"] is None else str(doc["band"]),
        subscription_state=str(doc["state"]),
        fingerprint=str(doc["fingerprint"]),
        catalog_version=str(doc["catalog"]),
        issued_at=datetime.fromisoformat(str(doc["issued"])),
        expires_at=datetime.fromisoformat(str(doc["expires"])),
        features=tuple(
            (
                str(r["id"]),
                str(r["outcome"]),
                tuple(str(x) for x in r["reasons"]),
                None if r["state"] is None else str(r["state"]),
                None if r["band"] is None else str(r["band"]),
                bool(r["divergent"]),
                None if r["upgrade"] is None else str(r["upgrade"]),
            )
            for r in rows
        ),
        signed=signed,
    )


def default_mac() -> Callable[[bytes], bytes]:
    from zid_platform.modules.evidence.ports import current_runtime

    return current_runtime().mac_key.mac


def sign(snapshot: Snapshot, mac: Callable[[bytes], bytes]) -> Snapshot:
    return replace(snapshot, signed=mac(snapshot.body()).hex())


def verifies(snapshot: Snapshot, mac: Callable[[bytes], bytes]) -> bool:
    return compare_digest(mac(snapshot.body()).hex(), snapshot.signed)


async def tenant_of(conn: AsyncConnection) -> str | None:
    value = (
        await conn.execute(text("SELECT nullif(current_setting('app.tenant_id', true), '')"))
    ).scalar_one()
    return str(value) if value else None


async def fingerprint(conn: AsyncConnection) -> str:
    """What the snapshot rests on, in one read. Any change to it makes every cached snapshot of the tenant unusable at once."""
    row = (
        await conn.execute(
            text("""
                SELECT
                  coalesce((SELECT plan_code || ':' || state || ':' || version
                            FROM commercial.entitlement
                            WHERE state IN ('ACTIVE', 'SUSPENDED')
                            ORDER BY effective_from DESC LIMIT 1), 'DEFAULT'),
                  (SELECT count(*) || ':' || coalesce(max(greatest(created_at, revoked_at))::text, '')
                   FROM commercial.entitlement_override),
                  coalesce((SELECT state || ':' || safety_hold::text
                            FROM commercial.billing_account_state LIMIT 1), 'NONE')
            """)
        )
    ).one()
    return "|".join(str(c) for c in row)


class SnapshotCache:
    """Process-local. A miss, an expiry, a failed signature or a changed fingerprint all end the same way: build again."""

    def __init__(self, mac: Callable[[bytes], bytes] | None = None) -> None:
        self._mac = mac
        self._by_tenant: dict[str, Snapshot] = {}
        self.hits = 0
        self.shared_hits = 0  # read from the shared row, built by another process or an earlier run
        self.rebuilds = 0
        self.rejected = 0

    def mac(self) -> Callable[[bytes], bytes]:
        return self._mac or default_mac()

    def usable(self, tenant_id: str, fp: str, now: datetime) -> Snapshot | None:
        snap = self._by_tenant.get(tenant_id)
        if snap is None:
            return None
        if now >= snap.expires_at or snap.fingerprint != fp:
            return None
        if not verifies(snap, self.mac()):
            self.rejected += 1
            del self._by_tenant[tenant_id]
            return None
        self.hits += 1
        return snap

    def store(self, snap: Snapshot) -> Snapshot:
        signed = sign(snap, self.mac())
        self._by_tenant[snap.tenant_id] = signed
        self.rebuilds += 1
        return signed

    def adopt(self, snap: Snapshot, fp: str, now: datetime) -> bool:
        """Take a snapshot another process published, but only if it would pass the same three tests as one built here."""
        if now >= snap.expires_at or snap.fingerprint != fp:
            return False
        if not verifies(snap, self.mac()):
            self.rejected += 1
            return False
        self._by_tenant[snap.tenant_id] = snap
        self.shared_hits += 1
        return True

    def invalidate(self, tenant_id: str | None = None) -> None:
        if tenant_id is None:
            self._by_tenant.clear()
        else:
            self._by_tenant.pop(tenant_id, None)

    def peek(self, tenant_id: str) -> Snapshot | None:
        return self._by_tenant.get(tenant_id)

    def put_raw(self, snap: Snapshot) -> None:
        """For tests of tampering: place a snapshot without signing it."""
        self._by_tenant[snap.tenant_id] = snap


def expiry(now: datetime, ttl_seconds: int, override_ends: list[datetime]) -> datetime:
    """The policy TTL, or the moment the earliest live override ends, whichever is first."""
    ends = [now + timedelta(seconds=ttl_seconds), *[e for e in override_ends if e > now]]
    return min(ends)
