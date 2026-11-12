"""Jurisdiction and profession policy packs and their approvals (ZID-RCG-003 RCG-G01).

Revision ID: verification_0005
Revises: verification_0004
Create Date: 2026-11-11

policy_pack: the rules for one market (country, optional subdivision and profession, product, purpose) as one immutable version.
Platform configuration, not tenant data, so it carries no tenant_id. Once a version leaves DRAFT its content can never change; it can
only be retired. A change is a new version. policy_pack_approval: who signed which pack hash in which role, append-only. The database
repeats the domain rules (the author cannot approve, one person holds one role, an approval signs the stored hash of a draft) so a
caller that skips the domain code still cannot get around them.
"""

from zid_platform.migrations import execute_script as _run

revision = "verification_0005"
down_revision = "verification_0004"
branch_labels = None
depends_on = None


def upgrade() -> None:
    _run("""
        CREATE TABLE verify.policy_pack (
            pack_id       text NOT NULL CHECK (length(pack_id) BETWEEN 1 AND 128),
            version       integer NOT NULL CHECK (version >= 1),
            country       text NOT NULL CHECK (country ~ '^[A-Z]{2}$'),
            subdivision   text CHECK (subdivision IS NULL OR length(subdivision) BETWEEN 1 AND 16),
            profession    text CHECK (profession IS NULL OR length(profession) BETWEEN 1 AND 64),
            product       text NOT NULL CHECK (length(product) BETWEEN 1 AND 64),
            purpose       text NOT NULL CHECK (length(purpose) BETWEEN 1 AND 64),
            status        text NOT NULL CHECK (status IN ('DRAFT', 'APPROVED', 'RETIRED')),
            sources       jsonb NOT NULL CHECK (jsonb_typeof(sources) = 'array'),
            rules         jsonb NOT NULL CHECK (jsonb_typeof(rules) = 'object'),
            author        text NOT NULL CHECK (length(author) BETWEEN 1 AND 128),
            pack_hash     text NOT NULL CHECK (pack_hash ~ '^[0-9a-f]{64}$'),
            created_at    timestamptz NOT NULL,
            published_at  timestamptz,
            retired_at    timestamptz,
            PRIMARY KEY (pack_id, version),
            CHECK ((status = 'DRAFT') = (published_at IS NULL)),
            CHECK ((status = 'RETIRED') = (retired_at IS NOT NULL))
        );
        CREATE INDEX policy_pack_market ON verify.policy_pack (country, product, purpose) WHERE status = 'APPROVED';

        CREATE TABLE verify.policy_pack_approval (
            pack_id      text NOT NULL,
            version      integer NOT NULL,
            role         text NOT NULL CHECK (role IN ('legal', 'grc', 'security', 'business_owner')),
            approver     text NOT NULL CHECK (length(approver) BETWEEN 1 AND 128),
            pack_hash    text NOT NULL CHECK (pack_hash ~ '^[0-9a-f]{64}$'),
            approved_at  timestamptz NOT NULL,
            PRIMARY KEY (pack_id, version, role),
            UNIQUE (pack_id, version, approver),
            FOREIGN KEY (pack_id, version) REFERENCES verify.policy_pack (pack_id, version)
        );

        CREATE FUNCTION verify.policy_pack_guard() RETURNS trigger LANGUAGE plpgsql AS $$
        BEGIN
            IF TG_OP = 'DELETE' THEN
                IF OLD.status <> 'DRAFT' THEN
                    RAISE EXCEPTION 'a published policy pack version is never deleted' USING ERRCODE = 'check_violation';
                END IF;
                RETURN OLD;
            END IF;
            IF OLD.status <> 'DRAFT' THEN
                IF (NEW.pack_id, NEW.version, NEW.country, NEW.subdivision, NEW.profession, NEW.product, NEW.purpose,
                    NEW.sources, NEW.rules, NEW.author, NEW.pack_hash, NEW.created_at, NEW.published_at)
                   IS DISTINCT FROM
                   (OLD.pack_id, OLD.version, OLD.country, OLD.subdivision, OLD.profession, OLD.product, OLD.purpose,
                    OLD.sources, OLD.rules, OLD.author, OLD.pack_hash, OLD.created_at, OLD.published_at) THEN
                    RAISE EXCEPTION 'a published policy pack version cannot be changed; make a new version'
                        USING ERRCODE = 'check_violation';
                END IF;
                IF NOT (OLD.status = 'APPROVED' AND NEW.status IN ('APPROVED', 'RETIRED')) AND NEW.status <> OLD.status THEN
                    RAISE EXCEPTION 'a policy pack version only moves from APPROVED to RETIRED'
                        USING ERRCODE = 'check_violation';
                END IF;
            ELSIF NEW.status = 'RETIRED' THEN
                RAISE EXCEPTION 'a draft is approved before it can be retired' USING ERRCODE = 'check_violation';
            ELSIF NEW.status = 'APPROVED' AND (
                    SELECT count(DISTINCT role) FROM verify.policy_pack_approval a
                    WHERE a.pack_id = NEW.pack_id AND a.version = NEW.version AND a.pack_hash = NEW.pack_hash) <> 4 THEN
                RAISE EXCEPTION 'a policy pack needs the four approvals of its current hash before it is approved'
                    USING ERRCODE = 'check_violation';
            END IF;
            RETURN NEW;
        END $$;
        CREATE TRIGGER policy_pack_guard BEFORE UPDATE OR DELETE ON verify.policy_pack
            FOR EACH ROW EXECUTE FUNCTION verify.policy_pack_guard();

        CREATE FUNCTION verify.policy_pack_approval_guard() RETURNS trigger LANGUAGE plpgsql AS $$
        DECLARE p verify.policy_pack%ROWTYPE;
        BEGIN
            IF TG_OP <> 'INSERT' THEN
                RAISE EXCEPTION 'policy pack approvals are append-only' USING ERRCODE = 'check_violation';
            END IF;
            SELECT * INTO p FROM verify.policy_pack WHERE pack_id = NEW.pack_id AND version = NEW.version;
            IF p.status <> 'DRAFT' THEN
                RAISE EXCEPTION 'only a draft policy pack can be approved' USING ERRCODE = 'check_violation';
            END IF;
            IF NEW.approver = p.author THEN
                RAISE EXCEPTION 'the author of a policy pack cannot approve it' USING ERRCODE = 'check_violation';
            END IF;
            IF NEW.pack_hash <> p.pack_hash THEN
                RAISE EXCEPTION 'an approval must sign the hash of this version' USING ERRCODE = 'check_violation';
            END IF;
            RETURN NEW;
        END $$;
        CREATE TRIGGER policy_pack_approval_guard BEFORE INSERT OR UPDATE OR DELETE ON verify.policy_pack_approval
            FOR EACH ROW EXECUTE FUNCTION verify.policy_pack_approval_guard();
    """)


def downgrade() -> None:
    _run("""
        DROP TABLE verify.policy_pack_approval, verify.policy_pack;
        DROP FUNCTION verify.policy_pack_approval_guard();
        DROP FUNCTION verify.policy_pack_guard();
    """)
