"""Security signals, security state and Shared Signals streams.

Revision ID: signals_0001
Revises:
Create Date: 2026-10-13

ZID-DATA-001 §10.1; ZID-SIGNALS-001 §2-9, §13-14, §21-22.
- signals.security_signal: append-only record of every normalized signal and what it did; the
  (tenant, dedupe key) pair is unique, so a replayed SET jti has one effect (SIG-05).
- signals.security_state: current state per subject; `revision` only ever increases (no rollback
  by late or reordered events, SIG-06).
- signals.ssf_stream: receiver streams (pinned transmitter issuer, audience, keys, event allowlist,
  push credential hash) and transmitter streams (receiver audience, events, poll delivery).
- signals.ssf_subject_map: stream-scoped opaque subject ids -> principal (no email correlation).
- signals.ssf_outbound_set: SETs queued for poll delivery (RFC 8936) until acknowledged.
"""

from zid_platform.migrations import execute_script as _run

revision = "signals_0001"
down_revision = None
branch_labels = None
depends_on = None

TENANT_TABLES = (
    "security_signal",
    "security_state",
    "ssf_stream",
    "ssf_subject_map",
    "ssf_outbound_set",
)


def upgrade() -> None:
    _run("""
        CREATE TABLE signals.security_signal (
            signal_id           uuid PRIMARY KEY,
            tenant_id           uuid NOT NULL,
            subject_type        text NOT NULL CHECK (subject_type IN ('PRINCIPAL', 'SESSION', 'CREDENTIAL',
                                    'DEVICE', 'APPLICATION', 'TENANT', 'ORG_UNIT', 'ORGANIZATION',
                                    'AUTHORITY_ROOT', 'MANDATE', 'WORKLOAD', 'TOOL')),
            subject_ref         text NOT NULL,
            source_id           text NOT NULL,
            source_event_id     text NOT NULL,
            source_event_type   text NOT NULL,
            event_type          text NOT NULL,
            severity            text NOT NULL CHECK (severity IN ('INFO', 'LOW', 'MEDIUM', 'HIGH', 'CRITICAL')),
            confidence          text NOT NULL CHECK (confidence IN ('LOW', 'MEDIUM', 'HIGH', 'AUTHORITATIVE')),
            required_action     text NOT NULL CHECK (required_action IN ('NONE', 'REEVALUATE', 'STEP_UP',
                                    'REAUTH', 'REPROOF', 'RESTRICT', 'SUSPEND', 'REVOKE', 'PURGE')),
            risk_class_floor    smallint NOT NULL CHECK (risk_class_floor BETWEEN 0 AND 3),
            effective_at        timestamptz NOT NULL,
            observed_at         timestamptz NOT NULL,
            normalized_at       timestamptz NOT NULL,
            dedupe_key_hash     bytea NOT NULL CHECK (octet_length(dedupe_key_hash) = 32),
            payload_commitment  bytea CHECK (payload_commitment IS NULL OR octet_length(payload_commitment) = 32),
            source_subject_commitment bytea,
            status              text NOT NULL CHECK (status IN ('APPLIED', 'OUT_OF_ORDER', 'INFORMATIONAL',
                                    'UNRESOLVED', 'REJECTED')),
            reason_code         text NOT NULL,
            state_revision      bigint,
            UNIQUE (tenant_id, dedupe_key_hash)
        );
        CREATE INDEX security_signal_subject ON signals.security_signal (tenant_id, subject_type, subject_ref, effective_at);

        CREATE FUNCTION signals.append_only() RETURNS trigger LANGUAGE plpgsql AS $$
        BEGIN
            RAISE EXCEPTION 'security signals are append-only' USING ERRCODE = 'check_violation';
        END $$;
        CREATE TRIGGER security_signal_append_only BEFORE UPDATE OR DELETE ON signals.security_signal
            FOR EACH ROW EXECUTE FUNCTION signals.append_only();

        CREATE TABLE signals.security_state (
            tenant_id        uuid NOT NULL,
            subject_type     text NOT NULL,
            subject_id       text NOT NULL,
            state            text NOT NULL CHECK (state IN ('TRUSTED', 'ELEVATED_RISK', 'STEP_UP_REQUIRED',
                                 'REAUTH_REQUIRED', 'REPROOF_REQUIRED', 'RESTRICTED', 'SUSPENDED',
                                 'COMPROMISED', 'REVOKED', 'PURGED')),
            revision         bigint NOT NULL CHECK (revision >= 1),
            effective_at     timestamptz NOT NULL,
            committed_at     timestamptz NOT NULL,
            reason_code      text NOT NULL,
            cause_signal_id  uuid,
            PRIMARY KEY (tenant_id, subject_type, subject_id)
        );
        CREATE FUNCTION signals.state_guard() RETURNS trigger LANGUAGE plpgsql AS $$
        BEGIN
            IF NEW.revision <= OLD.revision THEN
                RAISE EXCEPTION 'security state revision only increases' USING ERRCODE = 'check_violation';
            END IF;
            IF (NEW.tenant_id, NEW.subject_type, NEW.subject_id) IS DISTINCT FROM
               (OLD.tenant_id, OLD.subject_type, OLD.subject_id) THEN
                RAISE EXCEPTION 'security state subject is fixed' USING ERRCODE = 'check_violation';
            END IF;
            IF OLD.state IN ('REVOKED', 'PURGED') AND NEW.state IS DISTINCT FROM OLD.state THEN
                RAISE EXCEPTION 'terminal security state' USING ERRCODE = 'check_violation';
            END IF;
            RETURN NEW;
        END $$;
        CREATE TRIGGER security_state_guard BEFORE UPDATE ON signals.security_state
            FOR EACH ROW EXECUTE FUNCTION signals.state_guard();

        CREATE TABLE signals.ssf_stream (
            stream_id          uuid PRIMARY KEY,
            tenant_id          uuid NOT NULL,
            direction          text NOT NULL CHECK (direction IN ('RECEIVER', 'TRANSMITTER')),
            issuer             text NOT NULL,
            audience           text NOT NULL,
            jwks               jsonb,
            algorithms         text[] NOT NULL DEFAULT '{ES256}',
            events             text[] NOT NULL,
            subject_formats    text[] NOT NULL DEFAULT '{iss_sub,opaque}',
            delivery_method    text NOT NULL CHECK (delivery_method IN ('push', 'poll')),
            push_token_hash    bytea CHECK (push_token_hash IS NULL OR octet_length(push_token_hash) = 32),
            source_trust       text NOT NULL DEFAULT 'TRUSTED'
                                   CHECK (source_trust IN ('INFORMATIONAL', 'TRUSTED', 'AUTHORITATIVE')),
            federation_issuer  text,
            receiver_client_id text,
            status             text NOT NULL DEFAULT 'PENDING'
                                   CHECK (status IN ('PENDING', 'ACTIVE', 'DISABLED', 'QUARANTINED', 'RETIRED')),
            metadata_revision  integer NOT NULL DEFAULT 1,
            created_at         timestamptz NOT NULL DEFAULT now(),
            updated_at         timestamptz NOT NULL DEFAULT now(),
            CHECK (cardinality(events) BETWEEN 1 AND 32),
            CHECK (NOT (subject_formats && ARRAY['email', 'phone_number']::text[])),
            CHECK (direction <> 'RECEIVER' OR (jwks IS NOT NULL AND push_token_hash IS NOT NULL
                                                AND delivery_method = 'push')),
            CHECK (direction <> 'TRANSMITTER' OR (delivery_method = 'poll' AND receiver_client_id IS NOT NULL)),
            CHECK (algorithms <@ ARRAY['ES256', 'Ed25519', 'RS256', 'PS256']::text[])
        );

        CREATE TABLE signals.ssf_subject_map (
            stream_id      uuid NOT NULL REFERENCES signals.ssf_stream (stream_id),
            opaque_id      text NOT NULL,
            tenant_id      uuid NOT NULL,
            principal_id   uuid NOT NULL,
            created_at     timestamptz NOT NULL DEFAULT now(),
            PRIMARY KEY (stream_id, opaque_id)
        );

        CREATE TABLE signals.ssf_outbound_set (
            stream_id      uuid NOT NULL REFERENCES signals.ssf_stream (stream_id),
            jti            text NOT NULL,
            tenant_id      uuid NOT NULL,
            set_jwt        text NOT NULL,
            created_at     timestamptz NOT NULL DEFAULT now(),
            acked_at       timestamptz,
            PRIMARY KEY (stream_id, jti)
        );
        CREATE INDEX ssf_outbound_pending ON signals.ssf_outbound_set (stream_id, created_at) WHERE acked_at IS NULL;
    """)
    for table in TENANT_TABLES:
        _run(f"""
            ALTER TABLE signals.{table} ENABLE ROW LEVEL SECURITY;
            ALTER TABLE signals.{table} FORCE ROW LEVEL SECURITY;
            CREATE POLICY tenant_isolation ON signals.{table}
                USING (tenant_id = nullif(current_setting('app.tenant_id', true), '')::uuid)
                WITH CHECK (tenant_id = nullif(current_setting('app.tenant_id', true), '')::uuid);
        """)


def downgrade() -> None:
    _run("""
        DROP TABLE signals.ssf_outbound_set;
        DROP TABLE signals.ssf_subject_map;
        DROP TABLE signals.ssf_stream;
        DROP TRIGGER security_state_guard ON signals.security_state;
        DROP FUNCTION signals.state_guard();
        DROP TABLE signals.security_state;
        DROP TRIGGER security_signal_append_only ON signals.security_signal;
        DROP FUNCTION signals.append_only();
        DROP TABLE signals.security_signal;
    """)
