"""Identity-plane maintenance: `python -m zid_platform.entrypoints.maintenance` (scheduled job).

Runs with the cell's maintenance role (ZID_MAINTENANCE_DATABASE_URL: BYPASSRLS and DELETE on the
transient tables only - the runtime role has neither). One pass:
- removes partner request-log rows older than the retention period (verify.api_purge_request_log, never a newer row);
- purges single-use material once it can no longer be replayed: authorization codes, PAR
  requests, DPoP nonces and jti, WebAuthn challenges, enrollment tickets, federated login
  transactions, SAML assertion ids, acknowledged outbound SETs past their retention;
- sweeps lifecycles: identity sessions and refresh families past absolute expiry, verification
  assertions past fresh_until (verification.assertion.expired), verification sessions, recovery
  cases and presentation transactions past their TTL; removes disclosed presentation claims the
  relying party did not collect in time (ZID-VC-001 §7.1 minimization).
Each statement is idempotent; a failed pass is simply run again.
"""

from __future__ import annotations

import asyncio
import os
import sys
from datetime import UTC, datetime, timedelta

from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncConnection, create_async_engine
from zid_outbox import emit

REGISTRATION_RETENTION = timedelta(days=30)  # sign-up requests after they end
REPLAY_GRACE = timedelta(hours=1)  # keep spent single-use rows a little past expiry
SET_RETENTION = timedelta(days=30)
UNCOLLECTED_CLAIMS = timedelta(hours=1)
REQUEST_LOG_RETENTION_DAYS = 90  # the partner request log (CR-173); the database function refuses anything shorter. A default for the owners to confirm.

PURGE: tuple[tuple[str, str], ...] = (
    # sign-up requests: kept 30 days after they end (owners, 2026-11-02); the account index is not purged
    (
        "platform.registration_intent",
        "state NOT IN ('INITIATED', 'VERIFICATION_SENT') AND updated_at < :registration_retention",
    ),
    (
        "platform.registration_invitation_index",
        "status = 'ROUTED' AND created_at < :registration_retention",
    ),
    ("authn.authorization_code", "expires_at < :grace"),
    ("authn.par_request", "expires_at < :grace"),
    ("authn.dpop_nonce", "expires_at < :now"),
    ("authn.dpop_proof_jti", "expires_at < :now"),
    ("authn.webauthn_challenge", "expires_at < :grace"),
    ("authn.enrollment_ticket", "expires_at < :grace"),
    ("authn.federation_login", "expires_at < :grace"),
    ("authn.saml_assertion_replay", "expires_at < :now"),
    ("signals.ssf_outbound_set", "acked_at < :set_retention"),
)

SWEEP: dict[str, str] = {
    "authn.session": (
        "UPDATE authn.session SET status = 'EXPIRED' WHERE status IN ('ACTIVE', 'REAUTH_REQUIRED') "
        "AND expires_at <= :now"
    ),
    "authn.refresh_token_family": (
        "UPDATE authn.refresh_token_family SET status = 'EXPIRED', revoked_at = :now, "
        "revoked_reason = 'expired' WHERE status = 'ACTIVE' AND expires_at <= :now"
    ),
    "verify.verification_session": (
        "UPDATE verify.verification_session SET status = 'EXPIRED', updated_at = :now, "
        "row_version = row_version + 1 WHERE status IN ('CREATED', 'IN_PROGRESS', "
        "'PENDING_EXTERNAL', 'RESUBMISSION_REQUIRED', 'MANUAL_REVIEW') AND expires_at <= :now"
    ),
    "authn.recovery_case": (
        "UPDATE authn.recovery_case SET status = 'EXPIRED', completed_at = :now WHERE status NOT IN "
        "('COMPLETED', 'DENIED', 'EXPIRED') AND expires_at <= :now"
    ),
    "cred.presentation_record": (
        "UPDATE cred.presentation_record SET status = 'EXPIRED', completed_at = :now, response_key = NULL "
        "WHERE status = 'REQUESTED' AND expires_at <= :now"
    ),
    "platform.registration_intent": (
        "UPDATE platform.registration_intent SET state = 'EXPIRED', token_hash = NULL, email_pending = NULL, updated_at = :now "
        "WHERE state IN ('INITIATED', 'VERIFICATION_SENT') AND expires_at <= :now"
    ),
    "cred.presentation_record.claims": (
        "UPDATE cred.presentation_record SET disclosed_claims = NULL "
        "WHERE disclosed_claims IS NOT NULL AND completed_at < :uncollected"
    ),
}


async def run_once(conn: AsyncConnection, now: datetime) -> dict[str, int]:
    params = {
        "now": now,
        "grace": now - REPLAY_GRACE,
        "set_retention": now - SET_RETENTION,
        "uncollected": now - UNCOLLECTED_CLAIMS,
        "registration_retention": now - REGISTRATION_RETENTION,
    }
    counts: dict[str, int] = {}
    for table, where in PURGE:
        result = await conn.execute(text(f"DELETE FROM {table} WHERE {where}"), params)
        counts[f"purged:{table}"] = result.rowcount
    # the partner request log is append-only; its one way to lose rows is this function, and it never touches a row newer than the period
    removed = (
        await conn.execute(
            text("SELECT verify.api_purge_request_log(:days)"), {"days": REQUEST_LOG_RETENTION_DAYS}
        )
    ).scalar_one()
    counts["purged:verify.api_request_log"] = int(removed)
    for name, statement in SWEEP.items():
        result = await conn.execute(text(statement), params)
        counts[f"swept:{name}"] = result.rowcount
    expired = (
        await conn.execute(
            text(
                "UPDATE verify.verification_assertion SET status = 'EXPIRED' WHERE status = 'VALID' "
                "AND valid_until <= :now RETURNING assertion_id, tenant_id, subject_principal_id"
            ),
            params,
        )
    ).all()
    for row in expired:
        await emit(
            conn,
            name="verification.assertion.expired",
            tenant_id=str(row.tenant_id),
            aggregate_type="verification_assertion",
            aggregate_id=str(row.assertion_id),
            aggregate_version=2,
            data={
                "assertion_id": str(row.assertion_id),
                "subject_principal_id": str(row.subject_principal_id),
            },
        )
    counts["swept:verify.verification_assertion"] = len(expired)
    return counts


async def main_async(database_url: str) -> dict[str, int]:
    engine = create_async_engine(database_url, pool_size=1, max_overflow=0)
    try:
        async with engine.begin() as conn:
            return await run_once(conn, datetime.now(UTC))
    finally:
        await engine.dispose()


def main() -> None:
    url = os.environ.get("ZID_MAINTENANCE_DATABASE_URL")
    if not url:
        sys.stderr.write("ZID_MAINTENANCE_DATABASE_URL is required\n")
        raise SystemExit(2)
    counts = asyncio.run(main_async(url))
    for key, value in sorted(counts.items()):
        sys.stdout.write(f"{key} {value}\n")


if __name__ == "__main__":
    main()
