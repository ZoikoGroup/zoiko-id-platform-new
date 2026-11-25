"""The Command Center's governed analytics store (ZID-CPS-WF-02 sections 04, 05, 08, 09, 07.10 to 07.12).

Revision ID: verify_analytics_0001
Revises:
Create Date: 2026-11-24

va_fact        one row per source event, written once by the ingest consumer (keyed by the event id, so a replay is one fact). Pseudonymous ids
               (session ref, case id) and allow-listed codes only: never a name, a document field, an image, a template or an AML payload.
               Append-only: a mistake is answered by a correction, never by an edit.
va_watermark   per tenant and source domain: the newest event time seen, when it was ingested, and how many arrived late or were rejected.
va_correction  a fact excluded from the metrics, with the reason, who asked and who approved (never the same person).
va_snapshot    a query result frozen with its definition versions and watermark, so a rerun or an export reproduces the same totals.
va_export      an export job: what was asked, the frozen content, a single-use download token (SHA-256 only), its expiry, use and revocation.
va_grant       an analytics permission bundle an Owner gave a member (the five tenant roles stay unchanged).
"""

from zid_platform.migrations import execute_script as _run

revision = "verify_analytics_0001"
down_revision = None
branch_labels = None
depends_on = None

RULE = "(tenant_id = nullif(current_setting('app.tenant_id', true), '')::uuid)"
TABLES = ("va_fact", "va_watermark", "va_correction", "va_snapshot", "va_export", "va_grant")


def upgrade() -> None:
    _run("""
        CREATE TABLE metering.va_fact (
            event_id     uuid PRIMARY KEY,
            tenant_id    uuid NOT NULL,
            event_type   text NOT NULL,
            source       text NOT NULL,
            session_ref  text,
            case_id      uuid,
            environment  text,
            market       text,
            occurred_at  timestamptz NOT NULL,
            ingested_at  timestamptz NOT NULL DEFAULT now(),
            attrs        jsonb NOT NULL DEFAULT '{}'
        );
        CREATE INDEX va_fact_session ON metering.va_fact (tenant_id, session_ref, occurred_at);
        CREATE INDEX va_fact_type_time ON metering.va_fact (tenant_id, event_type, occurred_at);
        CREATE FUNCTION metering.va_fact_guard() RETURNS trigger LANGUAGE plpgsql AS $$
        BEGIN
            IF TG_OP = 'DELETE' AND current_setting('zid.va_purge', true) = 'on' THEN
                RETURN OLD;  -- the retention purge, and only it, removes facts past their period
            END IF;
            RAISE EXCEPTION 'analytics facts are append-only; record a correction instead' USING ERRCODE = 'check_violation';
        END $$;
        CREATE TRIGGER va_fact_append_only BEFORE UPDATE OR DELETE ON metering.va_fact
            FOR EACH ROW EXECUTE FUNCTION metering.va_fact_guard();

        CREATE TABLE metering.va_watermark (
            tenant_id         uuid NOT NULL,
            source            text NOT NULL,
            last_occurred_at  timestamptz NOT NULL,
            last_ingested_at  timestamptz NOT NULL,
            accepted          bigint NOT NULL DEFAULT 0,
            late              bigint NOT NULL DEFAULT 0,
            rejected          bigint NOT NULL DEFAULT 0,
            PRIMARY KEY (tenant_id, source)
        );

        CREATE TABLE metering.va_correction (
            correction_id  uuid PRIMARY KEY,
            tenant_id      uuid NOT NULL,
            event_id       uuid NOT NULL REFERENCES metering.va_fact (event_id),
            reason         text NOT NULL CHECK (length(reason) BETWEEN 10 AND 500),
            requested_by   uuid NOT NULL,
            requested_at   timestamptz NOT NULL,
            approved_by    uuid,
            approved_at    timestamptz,
            CHECK (approved_by IS NULL OR approved_by <> requested_by)
        );
        CREATE UNIQUE INDEX va_correction_event ON metering.va_correction (event_id);

        CREATE TABLE metering.va_snapshot (
            snapshot_id   uuid PRIMARY KEY,
            tenant_id     uuid NOT NULL,
            fingerprint   text NOT NULL,
            request       jsonb NOT NULL,
            result        jsonb NOT NULL,
            created_by    uuid NOT NULL,
            created_at    timestamptz NOT NULL
        );

        CREATE TABLE metering.va_export (
            export_id     uuid PRIMARY KEY,
            tenant_id     uuid NOT NULL,
            snapshot_id   uuid NOT NULL REFERENCES metering.va_snapshot (snapshot_id),
            format        text NOT NULL CHECK (format IN ('csv', 'json')),
            purpose       text NOT NULL CHECK (length(purpose) BETWEEN 3 AND 200),
            requested_by  uuid NOT NULL,
            created_at    timestamptz NOT NULL,
            expires_at    timestamptz NOT NULL,
            token_hash    text NOT NULL,
            content       bytea NOT NULL,
            downloaded_at timestamptz,
            revoked_at    timestamptz,
            revoked_by    uuid
        );

        CREATE TABLE metering.va_grant (
            grant_id      uuid PRIMARY KEY,
            tenant_id     uuid NOT NULL,
            grantee       uuid NOT NULL,
            bundle        text NOT NULL,
            permissions   text[] NOT NULL,
            granted_by    uuid NOT NULL,
            granted_at    timestamptz NOT NULL,
            expires_at    timestamptz,
            revoked_at    timestamptz,
            revoked_by    uuid,
            CHECK (grantee <> granted_by)
        );
    """)
    for t in TABLES:
        _run(f"""
            ALTER TABLE metering.{t} ENABLE ROW LEVEL SECURITY;
            ALTER TABLE metering.{t} FORCE ROW LEVEL SECURITY;
            CREATE POLICY tenant_isolation ON metering.{t} USING {RULE} WITH CHECK {RULE};
        """)


def downgrade() -> None:
    _run("""
        DROP TABLE metering.va_grant;
        DROP TABLE metering.va_export;
        DROP TABLE metering.va_snapshot;
        DROP TABLE metering.va_correction;
        DROP TRIGGER va_fact_append_only ON metering.va_fact;
        DROP FUNCTION metering.va_fact_guard();
        DROP TABLE metering.va_watermark;
        DROP TABLE metering.va_fact;
    """)
