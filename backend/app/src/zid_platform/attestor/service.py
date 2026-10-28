"""Deciding what to vouch for, and signing it.

Levels (ZID-MACHINE-001 §9), as this attestor earns them:

- 0: the observed runtime object does not match the registration, or is not running: nothing is signed.
- 1 (environment): it is running and satisfies every registered selector.
- 2 (artifact): level 1, and it is running an artifact digest the registration approves.
- 3 (hardware or TEE): only when a `HardwareQuoteVerifier` is configured and accepts the runtime. None ships here, so
  this attestor stops at 2 even if a registration asks for more.

A statement is short-lived (default 15 minutes) and is renewed well before it ends, so a stopped, replaced or tampered
workload loses its attestation within minutes and is refused new tokens (fail closed, MACHINE-001 §26)."""

from __future__ import annotations

import logging
import time
from collections.abc import Iterable
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta

import httpx
from zid_crypto import Signer, sign_jws

from zid_platform.attestor.inspectors import (
    Facts,
    HardwareQuoteVerifier,
    InspectionError,
    Inspector,
)
from zid_platform.attestor.registration import Entry

log = logging.getLogger("zid.attestor")

STATEMENT_TYP = "attestation+jwt"  # the cell requires exactly this type
DEFAULT_LIFETIME = timedelta(minutes=15)
DEFAULT_RENEW_EVERY = timedelta(minutes=5)


@dataclass(frozen=True, slots=True)
class Finding:
    level: int
    digest: str | None
    why: str


def evaluate(
    entry: Entry,
    facts: Facts,
    *,
    target: str = "",
    hardware: HardwareQuoteVerifier | None = None,
) -> Finding:
    """What this attestor can honestly say about the observed runtime, as a level and the approved digest it matched."""
    if not facts.running:
        return Finding(0, None, "the workload is not running")
    wrong = sorted(k for k, v in entry.selectors.items() if facts.selectors.get(k) != v)
    if wrong:
        return Finding(0, None, f"selectors do not match: {', '.join(wrong)}")
    matched = sorted(facts.digests & entry.approved_digests)
    if not matched:
        observed = sorted(facts.digests)
        return Finding(
            1, observed[0] if observed else None, "running, but not an approved artifact"
        )
    level = 2
    if entry.max_level >= 3 and hardware is not None and hardware.verify(target, facts):
        level = 3
    return Finding(min(level, entry.max_level), matched[0], "approved artifact observed")


def statement(
    signer: Signer,
    entry: Entry,
    finding: Finding,
    *,
    now: datetime | None = None,
    lifetime: timedelta = DEFAULT_LIFETIME,
) -> str:
    """The signed statement for a finding. It always names a digest the runtime was observed running (the approved one
    at level 2, the observed one at level 1), never one taken from the registration alone."""
    now = now or datetime.now(UTC)
    digest = finding.digest
    if finding.level < 1 or digest is None:
        raise ValueError("nothing to attest")
    return sign_jws(
        signer,
        {
            "sub": entry.spiffe_id,
            "environment_id": entry.environment_id,
            "artifact_digest": digest,
            "level": finding.level,
            "iat": int(now.timestamp()),
            "exp": int((now + lifetime).timestamp()),
        },
        typ=STATEMENT_TYP,
    )


def attest_once(
    signer: Signer,
    entry: Entry,
    inspector: Inspector,
    target: str,
    *,
    hardware: HardwareQuoteVerifier | None = None,
    now: datetime | None = None,
    lifetime: timedelta = DEFAULT_LIFETIME,
) -> tuple[Finding, str | None]:
    """Inspect one workload and, if it earns a level, sign a statement. A runtime that cannot be read earns nothing."""
    try:
        facts = inspector.inspect(target)
    except InspectionError as e:
        return Finding(0, None, f"could not inspect: {e}"), None
    found = evaluate(entry, facts, target=target, hardware=hardware)
    if found.level < 1 or found.digest is None:
        return found, None
    return found, statement(signer, entry, found, now=now, lifetime=lifetime)


def submit(url: str, token: str, *, client: httpx.Client | None = None) -> tuple[int, str]:
    """Send a statement to the cell; returns (HTTP status, the cell's error code or 'OK')."""
    own = client is None
    http = client or httpx.Client(timeout=10)
    try:
        res = http.post(url, json={"statement": token})
    except httpx.HTTPError as e:
        return 0, type(e).__name__
    finally:
        if own:
            http.close()
    code = (
        "OK"
        if res.is_success
        else str((res.json().get("error") or {}).get("code", res.status_code))
    )
    return res.status_code, code


def run(
    signer: Signer,
    targets: Iterable[tuple[Entry, str]],
    inspector: Inspector,
    url: str,
    *,
    hardware: HardwareQuoteVerifier | None = None,
    renew_every: timedelta = DEFAULT_RENEW_EVERY,
    lifetime: timedelta = DEFAULT_LIFETIME,
    rounds: int | None = None,
) -> None:
    """Attest every target now and then every `renew_every`, until `rounds` is reached (None: forever)."""
    pairs = list(targets)
    done = 0
    while rounds is None or done < rounds:
        for entry, target in pairs:
            found, token = attest_once(
                signer, entry, inspector, target, hardware=hardware, lifetime=lifetime
            )
            if token is None:
                log.warning("not attested %s: %s", entry.spiffe_id, found.why)
                continue
            status, code = submit(url, token)
            log.info("attested %s level %s: %s %s", entry.spiffe_id, found.level, status, code)
        done += 1
        if rounds is None or done < rounds:
            time.sleep(renew_every.total_seconds())
