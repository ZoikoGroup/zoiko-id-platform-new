"""Bulk evidence export rules (ZID-API-001 API-13). Pure domain.

A bulk package is a convenience copy of evidence, so it is short-lived: a requested lifetime is clamped to a
ceiling, and after `expires_at` it is not served, whatever the caller asks.
"""

from __future__ import annotations

from datetime import datetime, timedelta

MAX_RECEIPTS = 100
DEFAULT_TTL = timedelta(hours=24)
MAX_TTL = timedelta(days=7)
MIN_TTL = timedelta(minutes=5)


def expiry(now: datetime, ttl_minutes: int | None) -> datetime:
    """`now` plus the requested lifetime, bounded to [MIN_TTL, MAX_TTL]; no request means DEFAULT_TTL."""
    ttl = DEFAULT_TTL if ttl_minutes is None else timedelta(minutes=ttl_minutes)
    return now + max(MIN_TTL, min(ttl, MAX_TTL))


def available(status: str, expires_at: datetime, now: datetime) -> bool:
    return status == "AVAILABLE" and now < expires_at
