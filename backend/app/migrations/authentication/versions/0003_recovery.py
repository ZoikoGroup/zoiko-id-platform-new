"""Account recovery: recovery codes and recovery cases.

Revision ID: authentication_0003
Revises: authentication_0002
Create Date: 2026-10-13

ZID-AUTH-001 §4 (recovery code), §14, §24 (AUTH-W7; AUTH-13).
- authn.recovery_code: one-time, high-entropy, stored only as SHA-256; issuing a new batch revokes
  the previous one.
- authn.recovery_case: one recovery attempt with its class (RC-1..RC-3), the gates it must pass
  (re-proofing, second-administrator approval, cooling-off) and a terminal outcome. The case is
  continued with a secret handle that is stored only as a hash. COMPLETED/DENIED/EXPIRED are final.
"""

from zid_platform.migrations import execute_script as _run

revision = "authentication_0003"
down_revision = "authentication_0002"
branch_labels = None
depends_on = None

TENANT_TABLES = ("recovery_code", "recovery_case")


def upgrade() -> None:
    _run("""
        CREATE TABLE authn.recovery_code (
            code_hash      bytea PRIMARY KEY CHECK (octet_length(code_hash) = 32),
            tenant_id      uuid NOT NULL,
            principal_id   uuid NOT NULL,
            batch_id       uuid NOT NULL,
            created_at     timestamptz NOT NULL,
            used_at        timestamptz,
            revoked_at     timestamptz
        );
        CREATE INDEX recovery_code_principal ON authn.recovery_code (tenant_id, principal_id);

        CREATE TABLE authn.recovery_case (
            recovery_id           uuid PRIMARY KEY,
            tenant_id             uuid NOT NULL,
            principal_id          uuid NOT NULL,
            recovery_class        text NOT NULL CHECK (recovery_class IN ('RC-1', 'RC-2', 'RC-3', 'RC-4')),
            method                text NOT NULL CHECK (method IN ('RECOVERY_CODE', 'REPROOF')),
            status                text NOT NULL CHECK (status IN ('PENDING_REPROOF', 'PENDING_APPROVAL',
                                      'COOLING_OFF', 'READY', 'COMPLETED', 'DENIED', 'EXPIRED')),
            handle_hash           bytea NOT NULL UNIQUE CHECK (octet_length(handle_hash) = 32),
            opened_by             text NOT NULL,
            reproof_assertion_id  uuid,
            approval_request_id   uuid,
            cooling_off_until     timestamptz,
            denied_reason         text,
            created_at            timestamptz NOT NULL,
            expires_at            timestamptz NOT NULL,
            completed_at          timestamptz,
            CHECK (recovery_class <> 'RC-1' OR method = 'RECOVERY_CODE'),
            CHECK (expires_at <= created_at + interval '7 days')
        );
        CREATE INDEX recovery_case_principal ON authn.recovery_case (tenant_id, principal_id, status);
        CREATE FUNCTION authn.recovery_case_guard() RETURNS trigger LANGUAGE plpgsql AS $$
        BEGIN
            IF OLD.status IN ('COMPLETED', 'DENIED', 'EXPIRED') AND NEW.status IS DISTINCT FROM OLD.status THEN
                RAISE EXCEPTION 'terminal recovery state' USING ERRCODE = 'check_violation';
            END IF;
            IF (NEW.principal_id, NEW.recovery_class, NEW.handle_hash, NEW.created_at)
               IS DISTINCT FROM (OLD.principal_id, OLD.recovery_class, OLD.handle_hash, OLD.created_at) THEN
                RAISE EXCEPTION 'recovery subject and class are fixed' USING ERRCODE = 'check_violation';
            END IF;
            RETURN NEW;
        END $$;
        CREATE TRIGGER recovery_case_guard BEFORE UPDATE ON authn.recovery_case
            FOR EACH ROW EXECUTE FUNCTION authn.recovery_case_guard();
    """)
    for table in TENANT_TABLES:
        _run(f"""
            ALTER TABLE authn.{table} ENABLE ROW LEVEL SECURITY;
            ALTER TABLE authn.{table} FORCE ROW LEVEL SECURITY;
            CREATE POLICY tenant_isolation ON authn.{table}
                USING (tenant_id = nullif(current_setting('app.tenant_id', true), '')::uuid)
                WITH CHECK (tenant_id = nullif(current_setting('app.tenant_id', true), '')::uuid);
        """)


def downgrade() -> None:
    _run("""
        DROP TRIGGER recovery_case_guard ON authn.recovery_case;
        DROP FUNCTION authn.recovery_case_guard();
        DROP TABLE authn.recovery_case;
        DROP TABLE authn.recovery_code;
    """)
