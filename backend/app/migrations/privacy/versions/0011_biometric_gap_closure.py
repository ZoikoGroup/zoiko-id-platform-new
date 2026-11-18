"""Biometric gap closure (ZID-DTV-WF-04 QA-004, QA-023, QA-034, QA-041; CR-113).

Revision ID: privacy_0011
Revises: privacy_0010
Create Date: 2026-11-18

- erasure_task: `attempts`, `next_attempt_at`, `alerted_at`. The worker asks a provider again for an overdue or failed deletion, with a growing wait,
  and raises one alert when the attempts run out. A task never becomes ACKNOWLEDGED without a receipt (the existing CHECK still holds).
- breach_case: `data_classes`, the biometric asset classes (and other data classes) the incident touched, recorded at opening and added to as the
  investigation goes on; never removed.
- biometric_release_attestation (QA-041): one row per approved step up of a biometric method scope, written in the approval's transaction: the
  decision hash bound to the running build and the capture SDK, with a hash over all of them and the approver. Append-only; release governance
  sits with the DPIA release gate. Platform configuration, not tenant data: no row-level security.
- dpia_package: `policy_hash`, the digest of the processing policy the package was approved against (the purpose, its retention policies and the
  named provider). The release gate refuses a package whose policy has changed since (DPIA_POLICY_CHANGED) until a new version is approved.
"""

from zid_platform.migrations import execute_script as _run

revision = "privacy_0011"
down_revision = "privacy_0010"
branch_labels = None
depends_on = None


def upgrade() -> None:
    _run("""
        ALTER TABLE privacy.erasure_task
            ADD COLUMN attempts        integer NOT NULL DEFAULT 0 CHECK (attempts >= 0),
            ADD COLUMN next_attempt_at timestamptz,
            ADD COLUMN alerted_at      timestamptz;
        CREATE INDEX erasure_task_retry ON privacy.erasure_task (next_attempt_at) WHERE state IN ('PENDING', 'FAILED');

        ALTER TABLE privacy.breach_case
            ADD COLUMN data_classes text[] NOT NULL DEFAULT '{}'
                CHECK (array_position(data_classes, NULL) IS NULL);

        ALTER TABLE privacy.dpia_package
            ADD COLUMN policy_hash text CHECK (policy_hash IS NULL OR policy_hash ~ '^[0-9a-f]{64}$');

        CREATE TABLE privacy.biometric_release_attestation (
            scope_id         text NOT NULL CHECK (scope_id ~ '^[a-z0-9][a-z0-9-]{0,127}$'),
            version          integer NOT NULL CHECK (version >= 1),
            decision_hash    text NOT NULL CHECK (decision_hash ~ '^[0-9a-f]{64}$'),
            build_ref        text NOT NULL CHECK (length(btrim(build_ref)) BETWEEN 1 AND 200),
            sdk_version      text NOT NULL CHECK (length(btrim(sdk_version)) BETWEEN 1 AND 100),
            attested_by      text NOT NULL CHECK (length(btrim(attested_by)) BETWEEN 1 AND 128),
            attested_at      timestamptz NOT NULL,
            attestation_hash text NOT NULL CHECK (attestation_hash ~ '^[0-9a-f]{64}$'),
            PRIMARY KEY (scope_id, version)
        );
        CREATE FUNCTION privacy.biometric_release_attestation_guard() RETURNS trigger LANGUAGE plpgsql AS $$
        BEGIN
            RAISE EXCEPTION 'release attestations are append-only' USING ERRCODE = 'check_violation';
        END $$;
        CREATE TRIGGER biometric_release_attestation_guard BEFORE UPDATE OR DELETE ON privacy.biometric_release_attestation
            FOR EACH ROW EXECUTE FUNCTION privacy.biometric_release_attestation_guard();
    """)


def downgrade() -> None:
    _run("""
        DROP TABLE privacy.biometric_release_attestation;
        DROP FUNCTION privacy.biometric_release_attestation_guard();
        ALTER TABLE privacy.dpia_package DROP COLUMN policy_hash;
        ALTER TABLE privacy.breach_case DROP COLUMN data_classes;
        DROP INDEX privacy.erasure_task_retry;
        ALTER TABLE privacy.erasure_task DROP COLUMN alerted_at, DROP COLUMN next_attempt_at, DROP COLUMN attempts;
    """)
