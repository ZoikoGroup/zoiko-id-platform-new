"""Release gates G0 to G3 for a screening module in a market: sign-offs, open defects and holds (ZID-DTV-WF-06 section 22, ENG-20, QA-053; CR-113).

Revision ID: screening_0012
Revises: screening_0011
Create Date: 2026-11-18

screening_gate_record: one append-only table of three kinds of fact about a (market, module) scope. A SIGNOFF is a role's signature on a gate, with its evidence reference and expiry; a DEFECT is an open P0 or
critical security defect, which stops every gate while it is open; a HOLD is a kill switch. The only column that ever changes is `closed_at`, once: a sign-off is rescinded, a defect is closed, a hold is
released. Nothing is deleted, and who did each thing is kept. The table is platform data, not tenant data.
"""

from zid_platform.migrations import execute_script as _run

revision = "screening_0012"
down_revision = "screening_0011"
branch_labels = None
depends_on = None


def upgrade() -> None:
    _run("""
        CREATE TABLE verify.screening_gate_record (
            record_id    uuid PRIMARY KEY,
            kind         text NOT NULL CHECK (kind IN ('SIGNOFF', 'DEFECT', 'HOLD')),
            market       text NOT NULL CHECK (market ~ '^[A-Z]{2}$'),
            module       text NOT NULL CHECK (module IN ('SANCTIONS', 'PEP_RCA', 'ADVERSE_MEDIA', 'MONITORING')),
            gate         text CHECK (gate IN ('G0', 'G1', 'G2', 'G3')),
            role         text CHECK (role IN ('FOUNDER', 'LEGAL', 'DPO', 'AML', 'ENGINEERING', 'SECURITY', 'QA', 'CTO', 'CISO', 'PRODUCT')),
            actor        text NOT NULL CHECK (length(actor) BETWEEN 1 AND 128),
            evidence_ref text NOT NULL CHECK (length(evidence_ref) BETWEEN 1 AND 500),
            created_at   timestamptz NOT NULL,
            expires_at   timestamptz,
            closed_at    timestamptz,
            closed_by    text,
            CHECK (kind <> 'SIGNOFF' OR (gate IS NOT NULL AND role IS NOT NULL AND expires_at IS NOT NULL)),
            CHECK (kind = 'SIGNOFF' OR (gate IS NULL AND role IS NULL)),
            CHECK (closed_at IS NULL OR closed_by IS NOT NULL)
        );
        CREATE INDEX screening_gate_record_scope ON verify.screening_gate_record (market, module, kind) WHERE closed_at IS NULL;

        CREATE FUNCTION verify.screening_gate_record_guard() RETURNS trigger LANGUAGE plpgsql AS $$
        BEGIN
            IF TG_OP = 'DELETE' THEN
                RAISE EXCEPTION 'a gate record is never deleted';
            END IF;
            IF NEW.record_id <> OLD.record_id OR NEW.kind <> OLD.kind OR NEW.market <> OLD.market OR NEW.module <> OLD.module
               OR NEW.gate IS DISTINCT FROM OLD.gate OR NEW.role IS DISTINCT FROM OLD.role OR NEW.actor <> OLD.actor
               OR NEW.evidence_ref <> OLD.evidence_ref OR NEW.created_at <> OLD.created_at OR NEW.expires_at IS DISTINCT FROM OLD.expires_at THEN
                RAISE EXCEPTION 'only the closing of a gate record changes';
            END IF;
            IF OLD.closed_at IS NOT NULL THEN
                RAISE EXCEPTION 'a closed gate record is never reopened';
            END IF;
            RETURN NEW;
        END $$;
        CREATE TRIGGER screening_gate_record_guard BEFORE UPDATE OR DELETE ON verify.screening_gate_record
            FOR EACH ROW EXECUTE FUNCTION verify.screening_gate_record_guard();
    """)


def downgrade() -> None:
    _run(
        "DROP TABLE verify.screening_gate_record; DROP FUNCTION verify.screening_gate_record_guard();"
    )
