"""WF-05 address claims, proof of address documents and their two-person review (ZID-DTV-WF-05 §06 AddressClaim, AddressEvidence; A05-A07, A10;
CR-115).

Revision ID: verification_0024
Revises: verification_0023
Create Date: 2026-11-18

check_address: the address a case asked to check, as typed (fields kept apart from any normalized form), revisioned; a change supersedes.
check_address_document: a proof of address document: the evidence upload it rests on, its type, what was READ from it (kept apart from the upload),
the rules' assessment and the review status. check_address_review: one row per review step (MAKER, then CHECKER), append-only; the checker is never
the maker. All tenant data under row-level security.
"""

from zid_platform.migrations import execute_script as _run

revision = "verification_0024"
down_revision = "verification_0023"
branch_labels = None
depends_on = None


def upgrade() -> None:
    _run("""
        CREATE TABLE verify.check_address (
            address_id    uuid PRIMARY KEY,
            tenant_id     uuid NOT NULL,
            case_id       uuid NOT NULL REFERENCES verify.check_case (case_id),
            revision      integer NOT NULL CHECK (revision >= 1),
            country       text NOT NULL CHECK (country ~ '^[A-Z]{2}$'),
            fields        jsonb NOT NULL,
            postcode      text NOT NULL CHECK (postcode IN ('OK', 'BAD_SHAPE', 'NOT_CHECKED')),
            created_at    timestamptz NOT NULL,
            superseded_at timestamptz,
            UNIQUE (case_id, revision)
        );
        CREATE UNIQUE INDEX check_address_current ON verify.check_address (case_id) WHERE superseded_at IS NULL;

        CREATE TABLE verify.check_address_document (
            document_id   uuid PRIMARY KEY,
            tenant_id     uuid NOT NULL,
            case_id       uuid NOT NULL REFERENCES verify.check_case (case_id),
            address_id    uuid NOT NULL REFERENCES verify.check_address (address_id),
            upload_ref    text NOT NULL CHECK (upload_ref ~ '^[A-Za-z0-9][A-Za-z0-9_:.-]{7,127}$'),
            doc_type      text NOT NULL CHECK (doc_type ~ '^[A-Z][A-Z_]{2,40}$'),
            read_fields   jsonb NOT NULL,
            support       text NOT NULL CHECK (support IN ('SUPPORTS', 'REVIEW', 'DOES_NOT_SUPPORT')),
            findings      text[] NOT NULL,
            policy_status text NOT NULL CHECK (policy_status IN ('SAMPLE', 'APPROVED')),
            status        text NOT NULL CHECK (status IN ('AWAITING_REVIEW', 'AWAITING_CHECKER', 'DONE')),
            created_at    timestamptz NOT NULL
        );

        CREATE TABLE verify.check_address_review (
            review_id     uuid PRIMARY KEY,
            tenant_id     uuid NOT NULL,
            document_id   uuid NOT NULL REFERENCES verify.check_address_document (document_id),
            step          text NOT NULL CHECK (step IN ('MAKER', 'CHECKER')),
            finding       text NOT NULL CHECK (finding IN ('CONFIRMED', 'REQUIRES_MORE_EVIDENCE', 'NOT_SUPPORTED', 'AGREED', 'DISAGREED')),
            reason_codes  text[] NOT NULL,
            decided_by    text NOT NULL CHECK (length(btrim(decided_by)) BETWEEN 1 AND 128),
            decided_at    timestamptz NOT NULL,
            UNIQUE (document_id, step),
            CHECK (NOT (array_to_string(reason_codes, ' ') ILIKE '%fraud%'))
        );

        CREATE FUNCTION verify.check_address_guard() RETURNS trigger LANGUAGE plpgsql AS $$
        BEGIN
            IF TG_OP = 'DELETE' THEN
                RAISE EXCEPTION 'address evidence is kept; erasure is a privacy workflow' USING ERRCODE = 'check_violation';
            END IF;
            IF (NEW.address_id, NEW.case_id, NEW.revision, NEW.country, NEW.fields, NEW.created_at)
               IS DISTINCT FROM (OLD.address_id, OLD.case_id, OLD.revision, OLD.country, OLD.fields, OLD.created_at)
               OR OLD.superseded_at IS NOT NULL THEN
                RAISE EXCEPTION 'an address is only ever superseded, once' USING ERRCODE = 'check_violation';
            END IF;
            RETURN NEW;
        END $$;
        CREATE TRIGGER check_address_guard BEFORE UPDATE OR DELETE ON verify.check_address
            FOR EACH ROW EXECUTE FUNCTION verify.check_address_guard();

        CREATE FUNCTION verify.check_address_document_guard() RETURNS trigger LANGUAGE plpgsql AS $$
        BEGIN
            IF TG_OP = 'DELETE' THEN
                RAISE EXCEPTION 'address evidence is kept; erasure is a privacy workflow' USING ERRCODE = 'check_violation';
            END IF;
            IF (NEW.document_id, NEW.upload_ref, NEW.doc_type, NEW.read_fields, NEW.support, NEW.findings, NEW.created_at)
               IS DISTINCT FROM (OLD.document_id, OLD.upload_ref, OLD.doc_type, OLD.read_fields, OLD.support, OLD.findings, OLD.created_at)
               OR OLD.status = 'DONE' THEN
                RAISE EXCEPTION 'a document keeps what was read and assessed; only its review status moves' USING ERRCODE = 'check_violation';
            END IF;
            RETURN NEW;
        END $$;
        CREATE TRIGGER check_address_document_guard BEFORE UPDATE OR DELETE ON verify.check_address_document
            FOR EACH ROW EXECUTE FUNCTION verify.check_address_document_guard();

        CREATE FUNCTION verify.check_address_review_guard() RETURNS trigger LANGUAGE plpgsql AS $$
        DECLARE
            m text;
        BEGIN
            IF TG_OP <> 'INSERT' THEN
                RAISE EXCEPTION 'review steps are append-only' USING ERRCODE = 'check_violation';
            END IF;
            IF NEW.step = 'CHECKER' THEN
                SELECT decided_by INTO m FROM verify.check_address_review WHERE document_id = NEW.document_id AND step = 'MAKER';
                IF m IS NULL OR m = NEW.decided_by THEN
                    RAISE EXCEPTION 'the checker follows a maker and is a different person' USING ERRCODE = 'check_violation';
                END IF;
            END IF;
            RETURN NEW;
        END $$;
        CREATE TRIGGER check_address_review_guard BEFORE INSERT OR UPDATE OR DELETE ON verify.check_address_review
            FOR EACH ROW EXECUTE FUNCTION verify.check_address_review_guard();

        ALTER TABLE verify.check_address ENABLE ROW LEVEL SECURITY;
        ALTER TABLE verify.check_address FORCE ROW LEVEL SECURITY;
        CREATE POLICY tenant_isolation ON verify.check_address
            USING (tenant_id = nullif(current_setting('app.tenant_id', true), '')::uuid)
            WITH CHECK (tenant_id = nullif(current_setting('app.tenant_id', true), '')::uuid);
        ALTER TABLE verify.check_address_document ENABLE ROW LEVEL SECURITY;
        ALTER TABLE verify.check_address_document FORCE ROW LEVEL SECURITY;
        CREATE POLICY tenant_isolation ON verify.check_address_document
            USING (tenant_id = nullif(current_setting('app.tenant_id', true), '')::uuid)
            WITH CHECK (tenant_id = nullif(current_setting('app.tenant_id', true), '')::uuid);
        ALTER TABLE verify.check_address_review ENABLE ROW LEVEL SECURITY;
        ALTER TABLE verify.check_address_review FORCE ROW LEVEL SECURITY;
        CREATE POLICY tenant_isolation ON verify.check_address_review
            USING (tenant_id = nullif(current_setting('app.tenant_id', true), '')::uuid)
            WITH CHECK (tenant_id = nullif(current_setting('app.tenant_id', true), '')::uuid);
    """)


def downgrade() -> None:
    _run("""
        DROP TABLE verify.check_address_review;
        DROP TABLE verify.check_address_document;
        DROP TABLE verify.check_address;
        DROP FUNCTION verify.check_address_guard();
        DROP FUNCTION verify.check_address_document_guard();
        DROP FUNCTION verify.check_address_review_guard();
    """)
