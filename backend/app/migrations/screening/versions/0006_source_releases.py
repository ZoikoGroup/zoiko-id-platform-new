"""Governed release of an official sanctions source (ZID-DTV-WF-06 S10, ENG-03 to ENG-05; CR-113).

Revision ID: screening_0006
Revises: screening_0005
Create Date: 2026-11-18

sanction_list_release: one row per staged corpus of one source. It carries the importer's receipt, the validation (what blocked it, what two people must acknowledge), the state, and
the approvals. A corpus is ACTIVE only after two people other than the stager released it; the corpus screening reads is still the single active sanction_list_version, which a release
switches. A quarantined release keeps its receipt and has no version. The receipt, validation, stager and source of a release never change, and a release is never deleted: a rollback
reactivates an earlier release and supersedes the one it replaces.
"""

from zid_platform.migrations import execute_script as _run

revision = "screening_0006"
down_revision = "screening_0005"
branch_labels = None
depends_on = None


def upgrade() -> None:
    _run("""
        CREATE TABLE verify.sanction_list_release (
            release_id     uuid PRIMARY KEY,
            list_id        text NOT NULL CHECK (list_id IN ('OFAC_SDN', 'OFAC_NON_SDN', 'UK_SANCTIONS', 'EU_CONSOLIDATED',
                           'UN_SC', 'LOCAL', 'PEP', 'OFSI_CONSOLIDATED')),
            version_id     uuid REFERENCES verify.sanction_list_version (version_id),
            state          text NOT NULL CHECK (state IN ('VALIDATED', 'QUARANTINED', 'ACTIVE', 'SUPERSEDED')),
            staged_by      text NOT NULL CHECK (length(staged_by) BETWEEN 1 AND 128),
            receipt        jsonb NOT NULL,
            validation     jsonb NOT NULL,
            approvals      jsonb NOT NULL DEFAULT '[]',
            staged_at      timestamptz NOT NULL,
            activated_at   timestamptz,
            superseded_at  timestamptz,
            CHECK (state = 'QUARANTINED' OR version_id IS NOT NULL)
        );
        CREATE UNIQUE INDEX sanction_list_release_active ON verify.sanction_list_release (list_id) WHERE state = 'ACTIVE';
        CREATE INDEX sanction_list_release_by_list ON verify.sanction_list_release (list_id, staged_at DESC);

        CREATE FUNCTION verify.sanction_list_release_guard() RETURNS trigger LANGUAGE plpgsql AS $$
        BEGIN
            IF TG_OP = 'DELETE' THEN
                RAISE EXCEPTION 'a sanction list release is never deleted';
            END IF;
            IF NEW.release_id <> OLD.release_id OR NEW.list_id <> OLD.list_id
               OR NEW.version_id IS DISTINCT FROM OLD.version_id OR NEW.staged_by <> OLD.staged_by
               OR NEW.receipt <> OLD.receipt OR NEW.validation <> OLD.validation OR NEW.staged_at <> OLD.staged_at THEN
                RAISE EXCEPTION 'the receipt, validation and stager of a release never change';
            END IF;
            IF OLD.state = 'QUARANTINED' AND NEW.state <> 'QUARANTINED' THEN
                RAISE EXCEPTION 'a quarantined release is never released';
            END IF;
            RETURN NEW;
        END $$;
        CREATE TRIGGER sanction_list_release_guard BEFORE UPDATE OR DELETE ON verify.sanction_list_release
            FOR EACH ROW EXECUTE FUNCTION verify.sanction_list_release_guard();
    """)


def downgrade() -> None:
    _run(
        "DROP TABLE verify.sanction_list_release; DROP FUNCTION verify.sanction_list_release_guard();"
    )
